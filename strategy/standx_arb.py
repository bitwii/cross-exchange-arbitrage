"""Main arbitrage trading bot for StandX and Lighter exchanges."""
import asyncio
import signal
import logging
import os
import sys
import time
import requests
import traceback
from decimal import Decimal
from typing import Tuple
from datetime import datetime
import pytz

# Lighter 客户端
from lighter.signer_client import SignerClient

# StandX 客户端
try:
    from exchanges.standx import StandXClient
except ImportError:
    # 后备导入，适配不同的运行环境
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    from exchanges.standx import StandXClient

from .data_logger import DataLogger
from .order_book_manager import OrderBookManager
from .websocket_manager import WebSocketManagerWrapper
from .order_manager import OrderManager
from .position_tracker import PositionTracker


class Config:
    """Simple config class to wrap dictionary."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class StandxArb:
    """
    Arbitrage trading bot: 
    - Maker (Post-Only) orders on StandX
    - Taker (Market) orders on Lighter
    """

    def __init__(self, ticker: str, order_quantity: Decimal,
                 fill_timeout: int = 5, max_position: Decimal = Decimal('0'),
                 long_ex_threshold: Decimal = Decimal('10'),
                 short_ex_threshold: Decimal = Decimal('10')):
        """Initialize the arbitrage trading bot."""
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.max_position = max_position
        self.stop_flag = False
        self._cleanup_done = False

        self.long_ex_threshold = long_ex_threshold
        self.short_ex_threshold = short_ex_threshold

        # Setup logger
        self._setup_logger()

        # Initialize modules
        # exchange="standx" 用于区分日志 CSV
        self.data_logger = DataLogger(exchange="standx", ticker=ticker, logger=self.logger)
        self.order_book_manager = OrderBookManager(self.logger)
        self.ws_manager = WebSocketManagerWrapper(self.order_book_manager, self.logger)
        self.order_manager = OrderManager(self.order_book_manager, self.logger)

        # Initialize clients (will be set later)
        self.standx_client = None
        self.lighter_client = None

        # Configuration
        self.lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        self.account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', 0))
        self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', 0))
        
        # StandX Config
        self.standx_private_key = os.getenv('STANDX_PRIVATE_KEY')
        self.standx_base_url = os.getenv('STANDX_BASE_URL', 'https://perps.standx.com')
        self.standx_auth_url = os.getenv('STANDX_AUTH_URL', 'https://api.standx.com')

        # Contract/market info
        self.standx_symbol = ticker + "-USD" # 假设 StandX 格式为 BTC-USD
        self.standx_tick_size = Decimal("0.1") # 默认值，初始化时会尝试更新
        self.lighter_market_index = None
        self.base_amount_multiplier = None
        self.price_multiplier = None
        self.tick_size = None

        # Position tracker
        self.position_tracker = None

        # BBO logging control
        self.last_bbo_log_time = None
        self.last_status_log_time = None
        self.bbo_log_interval = 3600

        # Price tolerance
        self.price_tolerance_pct = Decimal('0.05')

        # Setup callbacks
        self._setup_callbacks()

    def _setup_logger(self):
        """Setup logging configuration."""
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/standx_{self.ticker}_log.txt"

        self.logger = logging.getLogger(f"arbi_standx_{self.ticker}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Disable verbose logging
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('websockets').setLevel(logging.WARNING)

        # Handlers
        file_handler = logging.FileHandler(self.log_filename)
        file_handler.setLevel(logging.INFO)
        
        # Buffer optimization
        if hasattr(file_handler, 'stream') and hasattr(file_handler.stream, 'reconfigure'):
            try:
                file_handler.stream.reconfigure(buffering=65536)
            except Exception:
                pass

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        # Formatters
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
        console_formatter = logging.Formatter('%(levelname)s:%(name)s:[%(filename)s:%(lineno)d]:%(message)s')

        # Timezone UTC+8
        def beijing_time(*args):
            import time as time_module
            utc_time = time_module.gmtime(args[0] if args else None)
            beijing_timestamp = (args[0] if args else time_module.time()) + 28800
            return time_module.gmtime(beijing_timestamp)

        file_formatter.converter = beijing_time
        console_formatter.converter = beijing_time

        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

    def _setup_callbacks(self):
        """Setup callback functions for order updates."""
        # Lighter 回调通过 WS Manager 处理
        self.ws_manager.set_callbacks(
            on_lighter_order_filled=self._handle_lighter_order_filled,
            # StandX 的 WS 回调直接绑定在 StandXClient 上，此处无需设置
            on_edgex_order_update=None 
        )
        self.order_manager.set_callbacks(
            on_order_filled=self._handle_lighter_order_filled
        )

    def _handle_lighter_order_filled(self, order_data: dict):
        """Handle Lighter order fill."""
        try:
            if "avg_filled_price" not in order_data:
                filled_quote = Decimal(order_data.get("filled_quote_amount", 0))
                filled_base = Decimal(order_data.get("filled_base_amount", 0))
                if filled_base > 0:
                    order_data["avg_filled_price"] = filled_quote / filled_base
                else:
                    self.logger.error("❌ Cannot calculate avg price: filled_base_amount is 0")
                    return

            if order_data.get("is_ask") or order_data.get("side") == "SELL":
                order_data["side"] = "SHORT"
                order_type = "OPEN"
                if self.position_tracker:
                    filled_amount = Decimal(str(order_data.get("filled_base_amount", 0)))
                    self.position_tracker.update_lighter_position(-filled_amount)
            else:
                order_data["side"] = "LONG"
                order_type = "CLOSE"
                if self.position_tracker:
                    filled_amount = Decimal(str(order_data.get("filled_base_amount", 0)))
                    self.position_tracker.update_lighter_position(filled_amount)

            client_order_index = order_data.get("client_order_id", "UNKNOWN")
            filled_base_amount = order_data.get("filled_base_amount", 0)
            avg_filled_price = order_data.get("avg_filled_price", 0)

            self.logger.info(
                f"[{client_order_index}] [{order_type}] [Lighter] [FILLED]: "
                f"{filled_base_amount} @ {avg_filled_price}")

            self.data_logger.log_trade_to_csv(
                exchange='lighter',
                side=order_data['side'],
                price=str(avg_filled_price),
                quantity=str(filled_base_amount)
            )

            self.order_manager.lighter_order_filled = True
            self.order_manager.order_execution_complete = True

        except Exception as e:
            self.logger.error(f"Error handling Lighter order result: {e}")
            traceback.print_exc()

    def _handle_standx_order_update(self, order: dict):
        """
        Handle StandX order update from WebSocket.
        Triggered by StandXClient.
        """
        try:
            # 过滤合约
            if order.get('contract_id') and order.get('contract_id') != self.standx_symbol:
                return

            # 如果 OrderManager 依赖 client_order_id 匹配，需确保 StandXClient 正确解析并返回
            # 这里我们简化逻辑，只要是 Filled 就触发对冲
            
            order_id = order.get('order_id')
            status = order.get('status')
            side = order.get('side', '').lower()
            filled_size = Decimal(str(order.get('filled_size', '0')))
            size = Decimal(str(order.get('size', '0')))
            price = order.get('price', '0')

            # Determine Order Type (Open/Close) logic
            if side == 'buy':
                order_type = "OPEN"
            else:
                order_type = "CLOSE"

            if status == 'CANCELED' and filled_size > 0:
                status = 'FILLED'

            # 模拟 EdgeX 的状态更新逻辑给 OrderManager
            # OrderManager 可能用 update_edgex_order_status 记录状态
            self.order_manager.update_edgex_order_status(status)

            if status == 'FILLED' and filled_size > 0:
                self.logger.info(
                    f"✅ [StandX Filled] {side.upper()} {filled_size} @ {price} (id={order_id})")

                if self.position_tracker:
                    if side == 'buy':
                        self.position_tracker.update_edgex_position(filled_size)
                    else:
                        self.position_tracker.update_edgex_position(-filled_size)

                self.logger.info(
                    f"[{order_id}] [{order_type}] [StandX] [{status}]: {filled_size} @ {price}")

                if filled_size > 0.0001:
                    self.data_logger.log_trade_to_csv(
                        exchange='standx',
                        side=side,
                        price=str(price),
                        quantity=str(filled_size)
                    )

                # 触发 Lighter 对冲
                self.logger.info(
                    f"🔄 [Trigger Hedge] StandX {side} filled, preparing Lighter hedge order...")

                # 复用 handle_edgex_order_update 触发对冲逻辑
                self.order_manager.handle_edgex_order_update({
                    'order_id': order_id,
                    'side': side,
                    'status': status,
                    'size': size,
                    'price': price,
                    'contract_id': self.standx_symbol,
                    'filled_size': filled_size
                })
            
            elif status == 'OPEN':
                self.logger.info(f"[{order_id}] [{order_type}] [StandX] [{status}]: {size} @ {price}")

        except Exception as e:
            self.logger.error(f"Error handling StandX order update: {e}")
            traceback.print_exc()

    def shutdown(self, signum=None, frame=None):
        if self.stop_flag: return
        self.stop_flag = True
        self.logger.info("\n🛑 Stopping...")

        try:
            if self.ws_manager: self.ws_manager.shutdown()
        except: pass

        try:
            if self.data_logger: self.data_logger.close()
        except: pass
        
        # Async cleanup will be handled in run()

    async def _async_cleanup(self):
        if self._cleanup_done: return
        self._cleanup_done = True

        try:
            if self.standx_client:
                await self.standx_client.disconnect()
                self.logger.info("🔌 StandX client closed")
        except Exception as e:
            self.logger.error(f"Error closing StandX client: {e}")

    def setup_signal_handlers(self):
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def initialize_lighter_client(self):
        if self.lighter_client is None:
            api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY')
            if not api_key_private_key:
                raise Exception("API_KEY_PRIVATE_KEY not set")

            api_private_keys = {self.api_key_index: api_key_private_key}
            self.lighter_client = SignerClient(
                url=self.lighter_base_url,
                account_index=self.account_index,
                api_private_keys=api_private_keys,
            )
            if err := self.lighter_client.check_client():
                raise Exception(f"Lighter CheckClient error: {err}")

            self.logger.info("✅ Lighter client initialized")
        return self.lighter_client

    def initialize_standx_client(self):
        if not self.standx_private_key:
            raise ValueError("STANDX_PRIVATE_KEY must be set")

        config = {
            "private_key": self.standx_private_key,
            "chain": "solana",
            "base_url": self.standx_base_url,
            "auth_url": self.standx_auth_url,
            "symbol": self.standx_symbol,
            "tick_size": self.standx_tick_size
        }
        
        self.standx_client = StandXClient(config)
        # 绑定 WS 回调
        self.standx_client.setup_order_update_handler(self._handle_standx_order_update)
        
        self.logger.info("✅ StandX client initialized")
        return self.standx_client

    def get_lighter_market_config(self) -> Tuple[int, int, int, Decimal]:
        url = f"{self.lighter_base_url}/api/v1/orderBooks"
        try:
            response = requests.get(url, headers={"accept": "application/json"}, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            for market in data.get("order_books", []):
                if market["symbol"] == self.ticker:
                    price_multiplier = pow(10, market["supported_price_decimals"])
                    return (market["market_id"],
                            pow(10, market["supported_size_decimals"]),
                            price_multiplier,
                            Decimal("1") / (Decimal("10") ** market["supported_price_decimals"]))
            raise Exception(f"Ticker {self.ticker} not found on Lighter")
        except Exception as e:
            self.logger.error(f"⚠️ Error getting market config: {e}")
            raise

    async def trading_loop(self):
        """Main trading loop."""
        self.logger.info(f"🚀 Starting StandX arbitrage bot for {self.ticker}")

        try:
            self.initialize_lighter_client()
            self.initialize_standx_client()
            
            # Connect StandX (Login + WS)
            await self.standx_client.connect()

            # Lighter Config
            (self.lighter_market_index, self.base_amount_multiplier,
             self.price_multiplier, self.tick_size) = self.get_lighter_market_config()

            # Try to update StandX Tick Size
            try:
                ticker_info = self.standx_client.get_ticker(self.standx_symbol)
                # 简单的 tick size 推断或 hardcode
                # self.standx_tick_size = ... 
                pass
            except:
                pass

            self.logger.info(f"Info loaded - StandX: {self.standx_symbol}, Lighter ID: {self.lighter_market_index}")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize: {e}")
            traceback.print_exc()
            return

        # Initialize position tracker
        # 我们传入 standx_client，假设 PositionTracker 使用 duck typing 兼容 BaseExchangeClient 接口
        self.position_tracker = PositionTracker(
            self.ticker,
            self.standx_client,
            self.standx_symbol,
            self.lighter_base_url,
            self.account_index,
            self.logger
        )

        # Configure modules
        # OrderManager 同样需要兼容 StandXClient
        self.order_manager.set_edgex_config(
            self.standx_client, self.standx_symbol, self.standx_tick_size)
        self.order_manager.set_lighter_config(
            self.lighter_client, self.lighter_market_index,
            self.base_amount_multiplier, self.price_multiplier, self.tick_size)

        # Start Lighter WS (StandX WS started in connect())
        self.ws_manager.set_lighter_config(
            self.lighter_client, self.lighter_market_index, self.account_index)
        self.ws_manager.start_lighter_websocket()

        await asyncio.sleep(5)

        # Initial positions
        self.position_tracker.edgex_position = await self.position_tracker.get_edgex_position()
        self.position_tracker.lighter_position = await self.position_tracker.get_lighter_position()

        self.logger.info(f"📍 Starting main trading loop")

        while not self.stop_flag:
            # 1. Fetch StandX BBO
            try:
                # 使用 StandXClient 的 get_ticker 获取价格
                # 注意：EdgeX 是通过 OrderManager 的 fetch_edgex_bbo_prices 封装调用的
                # 这里我们直接调用 client，或者确保 OrderManager 兼容
                ticker_data = self.standx_client.get_ticker(self.standx_symbol)
                ex_best_bid = Decimal(str(ticker_data.get('bid_price') or 0))
                ex_best_ask = Decimal(str(ticker_data.get('ask_price') or 0))
                
                if ex_best_bid <= 0 or ex_best_ask <= 0:
                    # self.logger.warning("StandX BBO not ready")
                    await asyncio.sleep(0.5)
                    continue
            except Exception as e:
                self.logger.error(f"Error fetching StandX BBO: {e}")
                await asyncio.sleep(0.5)
                continue

            # 2. Fetch Lighter BBO
            lighter_bid, lighter_ask = self.order_book_manager.get_lighter_bbo()

            # 3. Strategy Logic
            long_ex = False
            short_ex = False
            
            # Logic: Buy StandX (Maker), Sell Lighter (Taker)
            if (lighter_bid and ex_best_bid and 
                lighter_bid - ex_best_bid > self.long_ex_threshold):
                long_ex = True
                
            # Logic: Sell StandX (Maker), Buy Lighter (Taker)
            elif (ex_best_ask and lighter_ask and 
                  ex_best_ask - lighter_ask > self.short_ex_threshold):
                short_ex = True

            # Logging
            current_time = time.time()
            if (long_ex or short_ex or 
                self.last_status_log_time is None or 
                (current_time - self.last_status_log_time >= self.bbo_log_interval)):
                
                long_spread = (lighter_bid - ex_best_bid) if (lighter_bid and ex_best_bid) else Decimal('0')
                short_spread = (ex_best_ask - lighter_ask) if (ex_best_ask and lighter_ask) else Decimal('0')
                
                self.logger.info(
                    f"📊 ST: {ex_best_bid}/{ex_best_ask} | LT: {lighter_bid}/{lighter_ask} | "
                    f"L_Spr: {long_spread:.2f} | S_Spr: {short_spread:.2f} | "
                    f"Pos: ST={self.position_tracker.get_current_edgex_position()} LT={self.position_tracker.lighter_position}"
                )
                self.last_status_log_time = current_time

            if self.stop_flag: break

            # Execute Trades
            current_position = self.position_tracker.get_current_edgex_position()

            if long_ex:
                if current_position < self.max_position:
                    self.logger.info(f"🚀 OPPORTUNITY: Long StandX (Spread: {lighter_bid - ex_best_bid:.2f})")
                    # 做多 StandX: 挂买单 @ Ask 附近
                    await self._execute_trade('buy', ex_best_ask, lighter_bid)
                else:
                    self.logger.info("⚠️ Max Long Position Reached")
                    self.last_status_log_time = current_time
                    await asyncio.sleep(1)

            elif short_ex:
                if current_position > -1 * self.max_position:
                    self.logger.info(f"🚀 OPPORTUNITY: Short StandX (Spread: {ex_best_ask - lighter_ask:.2f})")
                    # 做空 StandX: 挂卖单 @ Bid 附近
                    await self._execute_trade('sell', ex_best_bid, lighter_ask)
                else:
                    self.logger.info("⚠️ Max Short Position Reached")
                    self.last_status_log_time = current_time
                    await asyncio.sleep(1)
            else:
                await asyncio.sleep(0.05)

    async def _execute_trade(self, side: str, expected_price: Decimal, hedge_price: Decimal):
        """Execute trade pair (StandX Maker -> Lighter Taker)."""
        self.order_manager.order_execution_complete = False
        self.order_manager.waiting_for_lighter_fill = False
        
        try:
            self.logger.info(f"1️⃣ Placing StandX {side.upper()} Order...")
            
            # 价格微调：尝试做 Maker
            if side == 'buy':
                price = expected_price - self.standx_tick_size
            else:
                price = expected_price + self.standx_tick_size
            
            # 使用 StandXClient 下单
            # BaseExchangeClient 接口参数: contract_id, quantity, direction, price (optional)
            direction = 'long' if side == 'buy' else 'short'

            # 调用 place_open_order 并传入计算好的 price
            res = await self.standx_client.place_open_order(
                self.standx_symbol,
                self.order_quantity,
                direction,
                price  # 传入限价单价格
            )
            
            if not res.success:
                self.logger.error(f"❌ StandX Order Failed: {res.error_message}")
                return

            self.logger.info(f"✅ StandX Order Placed: {res.order_id}")
            
            # 记录下单以便 WS 回调处理 (OrderManager 可能需要知道此 ID)
            # self.order_manager.register_active_order(res.order_id) # 假设有此方法
            
            # 等待成交 (WS 回调会更新 order_manager.waiting_for_lighter_fill)
            wait_start = time.time()
            while not self.order_manager.waiting_for_lighter_fill and not self.stop_flag:
                await asyncio.sleep(0.01)
                if time.time() - wait_start > self.fill_timeout:
                    self.logger.warning("⏳ StandX Order Timeout, Cancelling...")
                    await self.standx_client.cancel_order(res.order_id)
                    return

            # 执行对冲
            if self.order_manager.waiting_for_lighter_fill:
                self.logger.info("2️⃣ Placing Lighter Hedge Order...")
                await self.order_manager.place_lighter_market_order(
                    self.order_manager.current_lighter_side,
                    self.order_manager.current_lighter_quantity,
                    self.order_manager.current_lighter_price,
                    self.stop_flag
                )

        except Exception as e:
            self.logger.error(f"Trade Execution Error: {e}")
            traceback.print_exc()

    async def run(self):
        """Run the arbitrage bot."""
        self.setup_signal_handlers()

        try:
            await self.trading_loop()
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Received interrupt signal...")
        except asyncio.CancelledError:
            self.logger.info("\n🛑 Task cancelled...")
        finally:
            self.logger.info("🔄 Cleaning up...")
            self.shutdown()
            try:
                await asyncio.wait_for(self._async_cleanup(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ Cleanup timeout, forcing exit")
            except Exception as e:
                self.logger.error(f"Error during cleanup: {e}")