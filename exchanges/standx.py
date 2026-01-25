"""
StandX exchange client implementation.
Compatible with BaseExchangeClient for arbitrage bot integration.
Supports HTTP REST API and WebSocket Order Updates (Market Stream).
"""

import os
import json
import base64
import time
import asyncio
import logging
import traceback
import websockets
import aiohttp
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple, Callable

# 引入项目基础类
from .base import BaseExchangeClient, OrderResult, OrderInfo, query_retry

# TradingLogger 可能不存在，使用标准 logging 作为后备
try:
    from helpers.logger import TradingLogger
except ImportError:
    TradingLogger = None

# 引入 StandX 协议模块
try:
    from .standx_protocol.perps_auth import StandXAuth
    from .standx_protocol.perp_http import StandXPerpHTTP
except ImportError:
    from exchange.exchange_standx.standx_protocol.perps_auth import StandXAuth
    from exchange.exchange_standx.standx_protocol.perp_http import StandXPerpHTTP

# 引入 Solana 依赖
import base58
import requests
from solders.keypair import Keypair


class Config:
    """Simple config class to wrap dictionary."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class StandXWebSocketManager:
    """
    StandX WebSocket 管理器
    负责连接维护、鉴权、订阅和消息分发
    文档: StandX Perps WebSocket API List -> Market Stream
    URL: wss://perps.standx.com/ws-stream/v1
    """
    def __init__(self, token: str, logger, on_message_callback: Callable):
        self.url = "wss://perps.standx.com/ws-stream/v1"
        self.token = token
        self.logger = logger
        self.on_message_callback = on_message_callback
        
        self._ws = None
        self._running = False
        self._task = None
        self._loop = None

    async def start(self):
        """启动 WebSocket 任务"""
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._run_loop())

    async def stop(self):
        """停止 WebSocket"""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        """主循环：包含自动重连逻辑"""
        while self._running:
            try:
                self.logger.info(f"🔌 [WS] Connecting to {self.url}...")
                # ping_interval=None: 禁用客户端主动 Ping，因为服务器会每10秒 Ping 我们
                # websockets 库会自动回复 Pong
                async with websockets.connect(self.url, ping_interval=None) as ws:
                    self._ws = ws
                    self.logger.info("✅ [WS] Connected")

                    # 1. 发送鉴权并订阅
                    await self._authenticate_and_subscribe()

                    # 2. 消息监听循环
                    while self._running:
                        try:
                            msg = await ws.recv()
                            self._handle_message(msg)
                        except websockets.ConnectionClosed:
                            self.logger.warning("⚠️ [WS] Connection closed by server")
                            break
                        except Exception as e:
                            self.logger.error(f"❌ [WS] Receive error: {e}")
                            break

            except Exception as e:
                self.logger.error(f"❌ [WS] Connection error: {e}")

            if self._running:
                self.logger.info("🔄 [WS] Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    async def _authenticate_and_subscribe(self):
        """
        发送鉴权和订阅请求
        文档参考: Authentication Request -> Log in with JWT
        Payload: { "auth": { "token": "...", "streams": [{"channel": "order"}] } }
        """
        auth_payload = {
            "auth": {
                "token": self.token,
                "streams": [
                    {"channel": "order"}  # 订阅订单更新
                    # {"channel": "position"}, # 可选：订阅持仓
                    # {"channel": "balance"}   # 可选：订阅余额
                ]
            }
        }
        await self._ws.send(json.dumps(auth_payload))
        self.logger.info("📤 [WS] Sent Auth & Subscription")

    def _handle_message(self, message: str):
        """处理收到的 WebSocket 消息"""
        try:
            data = json.loads(message)

            # 1. 处理鉴权响应
            # {"channel": "auth", "data": {"code": 0, "message": "success"}}
            # 注意：code=0 表示成功（不是 200）
            if data.get("channel") == "auth":
                auth_data = data.get("data", {})
                # StandX 的成功 code 是 0，不是 200
                if auth_data.get("code") == 0 or auth_data.get("message") == "success":
                    self.logger.info("✅ [WS] Authentication Successful")
                else:
                    self.logger.error(f"❌ [WS] Auth Failed: {auth_data}")
                return

            # 2. 处理订单更新
            # {"channel": "order", "data": {...}}
            if data.get("channel") == "order":
                order_data = data.get("data", {})
                if order_data:
                    self.on_message_callback(order_data)

        except Exception as e:
            self.logger.error(f"❌ [WS] Parse error: {e}, Message: {message[:100]}")


class StandXClient(BaseExchangeClient):
    """
    StandX 交易所客户端实现
    适配 BaseExchangeClient 接口，支持 Solana 链的复杂签名登录和 WebSocket 订单推送。
    """

    def __init__(self, config: Dict[str, Any]):
        # 将 dict 转换为 Config 对象
        if isinstance(config, dict):
            config_obj = Config(config)
        else:
            config_obj = config
        super().__init__(config_obj)

        # 1. 配置加载
        self.private_key = config.get('private_key') or os.getenv('STANDX_PRIVATE_KEY')
        self.chain = config.get('chain', 'solana')
        self.symbol = config.get('symbol', 'BTC-USD')
        self.base_url = config.get('base_url', 'https://perps.standx.com')
        self.auth_url = config.get('auth_url', 'https://api.standx.com')

        # 提取 ticker (e.g., "BTC" from "BTC-USD")
        ticker = self.symbol.split('-')[0] if '-' in self.symbol else self.symbol

        # 2. 初始化 logger (使用 TradingLogger 或标准 logging)
        if TradingLogger is not None:
            try:
                self.logger = TradingLogger(exchange="standx", ticker=ticker, log_to_console=False)
            except Exception:
                self.logger = self._create_standard_logger(ticker)
        else:
            self.logger = self._create_standard_logger(ticker)

        if not self.private_key:
            raise ValueError("STANDX_PRIVATE_KEY must be provided")

        # 3. 预加载钱包 (必须在 auth_client 之前)
        self.solana_keypair = None
        self.wallet_address = None
        self._setup_wallet()

        # 4. 初始化组件
        self.http_client = StandXPerpHTTP(base_url=self.base_url)
        # Solana keypair is Ed25519: first 32 bytes = private key seed
        ed25519_private_key = bytes(self.solana_keypair)[:32]
        self.auth_client = StandXAuth(private_key=ed25519_private_key)
        self.token = None

        # WebSocket 管理器
        self.ws_manager = None
        self._order_update_handler = None

        # 5. 合约配置 (类似 EdgeX/Lighter)
        self.config.contract_id = self.symbol
        self.config.tick_size = config.get('tick_size', Decimal('0.1'))

    def _validate_config(self) -> None:
        """Validate config (BaseExchangeClient abstract method)"""
        pass

    def _create_standard_logger(self, ticker: str):
        """Create a standard Python logger as fallback."""
        logger = logging.getLogger(f"standx_{ticker}")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _setup_wallet(self):
        """加载 Solana 钱包"""
        try:
            clean_key = self.private_key.replace("0x", "").strip()
            self.solana_keypair = Keypair.from_bytes(base58.b58decode(clean_key))
            self.wallet_address = str(self.solana_keypair.pubkey())
            self.logger.info(f"StandX Wallet loaded: {self.wallet_address}")
        except Exception as e:
            self.logger.error(f"Failed to load Solana wallet: {e}")
            raise

    def get_exchange_name(self) -> str:
        return "standx"

    async def connect(self) -> None:
        """连接流程: REST登录 -> 启动 WebSocket"""
        self.logger.info("Connecting to StandX...")
        try:
            # 1. 同步执行 REST 登录获取 Token
            await asyncio.to_thread(self._perform_login)

            # 2. 如果配置了 WS 回调，启动 WS
            if self._order_update_handler:
                await self._start_websocket()

        except Exception as e:
            self.logger.error(f"StandX connection failed: {e}")
            raise

    async def _start_websocket(self):
        """启动 WebSocket 连接"""
        if self.token:
            self.ws_manager = StandXWebSocketManager(
                token=self.token,
                logger=self.logger,
                on_message_callback=self._on_ws_order_update
            )
            await self.ws_manager.start()

    def _perform_login(self):
        """同步登录逻辑 (Base64 JSON Payload 模式)"""
        # 1. Prepare
        req_id = str(self.solana_keypair.pubkey())
        resp = requests.post(
            f"{self.auth_url}/v1/offchain/prepare-signin?chain=solana",
            json={"address": self.wallet_address, "requestId": req_id}
        )
        if not resp.ok:
            raise ValueError(f"Prepare failed: {resp.text}")
        
        data = resp.json()
        if not data.get("success"):
            raise ValueError(f"API Error: {data.get('message')}")
        
        signed_data_jwt = data["signedData"]

        # 2. Parse JWT & Sign
        parts = signed_data_jwt.split('.')
        padded = parts[1] + '=' * (4 - len(parts[1]) % 4)
        jwt_payload = json.loads(base64.b64decode(padded).decode('utf-8'))
        
        msg_bytes = jwt_payload.get("message").encode('utf-8')
        raw_sig = bytes(self.solana_keypair.sign_message(msg_bytes))

        # 3. Construct Payload
        final_sig = self._construct_complex_signature(jwt_payload, raw_sig, msg_bytes)

        # 4. Login
        resp = requests.post(
            f"{self.auth_url}/v1/offchain/login?chain=solana",
            json={
                "signature": final_sig,
                "signedData": signed_data_jwt,
                "expiresSeconds": 604800
            }
        )
        if not resp.ok:
            raise ValueError(f"Login failed: {resp.text}")

        result = resp.json()

        # StandX 登录成功响应直接包含 token，不需要检查 success 字段
        self.token = result.get("token")
        if not self.token:
            # 如果没有 token，可能是错误响应
            if "message" in result or "error" in result:
                raise ValueError(f"Login error: {result.get('message') or result.get('error')}")
            else:
                raise ValueError(f"Login failed: no token in response: {result}")

        self.logger.info(f"✅ StandX Login Success (Address: {result.get('address', 'N/A')})")

    def _construct_complex_signature(self, jwt_payload: dict, raw_sig: bytes, msg_bytes: bytes) -> str:
        """Construct Solana signature format for StandX"""
        # StandX 需要复杂的 JSON 签名结构
        input_data = {
            "domain": jwt_payload.get("domain"),
            "address": jwt_payload.get("address"),
            "statement": jwt_payload.get("statement"),
            "uri": jwt_payload.get("uri"),
            "version": jwt_payload.get("version"),
            "chainId": jwt_payload.get("chainId"),
            "nonce": jwt_payload.get("nonce"),
            "issuedAt": jwt_payload.get("issuedAt"),
            "requestId": jwt_payload.get("requestId")
        }
        output_data = {
            "account": {"publicKey": list(bytes(self.solana_keypair.pubkey()))},
            "signature": list(raw_sig),
            "signedMessage": list(msg_bytes)
        }
        complex_obj = {"input": input_data, "output": output_data}
        json_str = json.dumps(complex_obj, separators=(',', ':'))
        return base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

    def _on_ws_order_update(self, order_data: dict):
        """WebSocket order update callback"""
        if self._order_update_handler:
            self._order_update_handler(order_data)

    def setup_order_update_handler(self, handler) -> None:
        """Setup order update handler for WebSocket (BaseExchangeClient interface)"""
        self._order_update_handler = handler

    def get_ticker(self, symbol: str) -> dict:
        """Get ticker data for symbol (required by trading loop)"""
        try:
            # 使用 StandX 的 query_symbol_price API
            url = f"{self.base_url}/api/query_symbol_price"
            params = {"symbol": symbol}
            resp = requests.get(url, params=params, timeout=5)
            if not resp.ok:
                self.logger.error(f"Failed to get ticker: {resp.status_code} - {resp.text}")
                return {"bid_price": 0, "ask_price": 0}

            data = resp.json()
            # StandX API 返回字段: spread_bid, spread_ask
            return {
                "bid_price": data.get("spread_bid", 0) or 0,
                "ask_price": data.get("spread_ask", 0) or 0
            }
        except Exception as e:
            self.logger.error(f"Error getting ticker: {e}")
            return {"bid_price": 0, "ask_price": 0}

    @query_retry(default_return=(Decimal('0'), Decimal('0')))
    async def fetch_bbo_prices(self, contract_id: str = None) -> Tuple[Decimal, Decimal]:
        """Get best bid/ask prices asynchronously (compatible with EdgeX interface)."""
        symbol = contract_id or self.symbol
        ticker_data = await asyncio.to_thread(self.get_ticker, symbol)

        best_bid = Decimal(str(ticker_data.get('bid_price') or 0))
        best_ask = Decimal(str(ticker_data.get('ask_price') or 0))

        if best_bid <= 0 or best_ask <= 0:
            raise ValueError("Invalid bid/ask prices from StandX")

        return best_bid, best_ask

    async def get_order_price(self, direction: str) -> Decimal:
        """Get the price for an order based on direction (compatible with EdgeX)."""
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)

        if best_bid <= 0 or best_ask <= 0:
            raise ValueError("Invalid bid/ask prices")

        if direction == 'buy':
            order_price = best_ask - self.config.tick_size
        else:
            order_price = best_bid + self.config.tick_size

        return self.round_to_tick(order_price)

    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str, price: Optional[Decimal] = None) -> OrderResult:
        """
        Place an open order (BaseExchangeClient interface)

        Args:
            contract_id: Contract symbol (e.g. "BTC-USD")
            quantity: Order quantity
            direction: 'long'/'short' OR 'buy'/'sell' (both supported)
            price: Limit price (optional, if None will use market order)
        """
        try:
            # 支持两种方向格式: long/short 或 buy/sell
            direction_lower = direction.lower()
            if direction_lower in ('long', 'buy'):
                side = 'buy'
            elif direction_lower in ('short', 'sell'):
                side = 'sell'
            else:
                raise ValueError(f"Invalid direction: {direction}")

            # 使用 http_client.place_order 并传入 auth_client 进行签名
            order_type = "limit" if price else "market"
            result = self.http_client.place_order(
                token=self.token,
                symbol=contract_id,
                side=side,
                order_type=order_type,
                qty=str(quantity),
                time_in_force="gtc",
                reduce_only=False,
                price=str(price) if price else None,
                auth=self.auth_client
            )

            # 解析响应 (code=0 表示成功)
            if result.get("code") != 0:
                return OrderResult(
                    success=False,
                    error_message=result.get("message", "Unknown error")
                )

            return OrderResult(
                success=True,
                order_id=result.get("request_id"),
                side=side,
                size=quantity,
                price=price,
                status="OPEN"
            )

        except Exception as e:
            self.logger.error(f"Exception placing order: {e}")
            traceback.print_exc()
            return OrderResult(
                success=False,
                error_message=str(e)
            )

    async def place_close_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        """Place a close order (BaseExchangeClient interface)"""
        # 对于 StandX，close order 和 open order 使用相同的 API
        direction = 'short' if side == 'buy' else 'long'  # 反向平仓
        return await self.place_open_order(contract_id, quantity, direction, price)

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order (BaseExchangeClient interface)"""
        try:
            # 使用 perp_http 的 cancel_orders 方法，需要签名
            self.http_client.cancel_orders(
                token=self.token,
                cl_ord_id_list=[order_id],
                auth=self.auth_client
            )
            self.logger.info(f"✅ Order cancelled: {order_id}")
            return OrderResult(
                success=True,
                order_id=order_id,
                status="CANCELED"
            )
        except ValueError as e:
            error_msg = str(e)
            self.logger.error(f"Order cancellation failed: {error_msg}")
            return OrderResult(
                success=False,
                order_id=order_id,
                error_message=error_msg
            )
        except Exception as e:
            self.logger.error(f"Exception cancelling order: {e}")
            return OrderResult(
                success=False,
                order_id=order_id,
                error_message=str(e)
            )

    @query_retry()
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Get order information (BaseExchangeClient interface)"""
        try:
            url = f"{self.base_url}/api/v1/perps/orders/{order_id}"
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )

            if not resp.ok:
                self.logger.error(f"Failed to get order info: {resp.status_code}")
                return None

            result = resp.json()
            if not result.get("success"):
                return None

            order = result.get("data", {})
            return OrderInfo(
                order_id=order.get("orderId", order.get("id")),
                side=order.get("side", ""),
                size=Decimal(str(order.get("size", 0))),
                price=Decimal(str(order.get("price", 0))),
                status=order.get("status", ""),
                filled_size=Decimal(str(order.get("filledSize", 0))),
                remaining_size=Decimal(str(order.get("remainingSize", 0)))
            )

        except Exception as e:
            self.logger.error(f"Exception getting order info: {e}")
            return None

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract (BaseExchangeClient interface)"""
        try:
            url = f"{self.base_url}/api/v1/perps/orders"
            params = {"symbol": contract_id, "status": "open"}
            resp = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )

            if not resp.ok:
                self.logger.error(f"Failed to get active orders: {resp.status_code}")
                return []

            result = resp.json()
            if not result.get("success"):
                return []

            orders = result.get("data", [])
            order_list = []
            for order in orders:
                order_list.append(OrderInfo(
                    order_id=order.get("orderId", order.get("id")),
                    side=order.get("side", ""),
                    size=Decimal(str(order.get("size", 0))),
                    price=Decimal(str(order.get("price", 0))),
                    status=order.get("status", ""),
                    filled_size=Decimal(str(order.get("filledSize", 0))),
                    remaining_size=Decimal(str(order.get("remainingSize", 0)))
                ))

            return order_list

        except Exception as e:
            self.logger.error(f"Exception getting active orders: {e}")
            return []

    @query_retry(default_return=Decimal('0'))
    async def get_account_positions(self) -> Decimal:
        """
        Get account positions (BaseExchangeClient interface)
        Returns total position for the configured symbol
        """
        try:
            url = f"{self.base_url}/api/query_positions"
            params = {"symbol": self.symbol}
            resp = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )

            if not resp.ok:
                self.logger.error(f"Failed to get positions: {resp.status_code} - {resp.text}")
                return Decimal('0')

            # StandX 返回 list of positions
            positions = resp.json()
            if not isinstance(positions, list):
                self.logger.error(f"Unexpected positions format: {positions}")
                return Decimal('0')

            # 找到对应 symbol 的持仓
            for pos in positions:
                if pos.get("symbol") == self.symbol and pos.get("status") == "open":
                    # qty 表示持仓量，正数表示多头，负数表示空头
                    qty = pos.get("qty", 0)
                    return Decimal(str(qty)) if qty else Decimal('0')

            return Decimal('0')

        except Exception as e:
            self.logger.error(f"Exception getting positions: {e}")
            return Decimal('0')

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID and tick size for the configured symbol (compatible with EdgeX)."""
        try:
            # StandX 使用 symbol 作为 contract_id (e.g., "BTC-USD")
            # 尝试从 API 获取市场信息
            url = f"{self.base_url}/api/query_symbol_price"
            params = {"symbol": self.symbol}
            resp = await asyncio.to_thread(
                lambda: requests.get(url, params=params, timeout=10)
            )

            if resp.ok:
                data = resp.json()
                # 根据 symbol 推断 tick_size
                # 大多数永续合约使用 0.1 作为 tick_size
                tick_size = Decimal('0.1')

                # 更新 config
                self.config.contract_id = self.symbol
                self.config.tick_size = tick_size

                self.logger.info(
                    f"Contract attributes loaded: symbol={self.symbol}, tick_size={tick_size}")
                return self.config.contract_id, self.config.tick_size
            else:
                raise ValueError(f"Failed to get market info: {resp.status_code}")

        except Exception as e:
            self.logger.error(f"Error getting contract attributes: {e}")
            # 使用默认值
            self.config.contract_id = self.symbol
            self.config.tick_size = Decimal('0.1')
            return self.config.contract_id, self.config.tick_size

    async def disconnect(self) -> None:
        """Disconnect from the exchange (BaseExchangeClient interface)"""
        try:
            if self.ws_manager:
                await self.ws_manager.stop()
                self.ws_manager = None
            self.logger.info("StandX client disconnected")
        except Exception as e:
            self.logger.error(f"Error disconnecting: {e}")