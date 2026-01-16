"""Main arbitrage trading bot for edgeX and Lighter exchanges."""
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

from lighter.signer_client import SignerClient
from edgex_sdk import Client, WebSocketManager

from .data_logger import DataLogger
from .order_book_manager import OrderBookManager
from .websocket_manager import WebSocketManagerWrapper
from .order_manager import OrderManager
from .position_tracker import PositionTracker
from .dynamic_threshold import DynamicThresholdCalculator


class Config:
    """Simple config class to wrap dictionary for edgeX client."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class EdgexArb:
    """Arbitrage trading bot: makes post-only orders on edgeX, and market orders on Lighter."""

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
        self.data_logger = DataLogger(exchange="edgex", ticker=ticker, logger=self.logger)
        self.order_book_manager = OrderBookManager(self.logger)
        self.ws_manager = WebSocketManagerWrapper(self.order_book_manager, self.logger)
        self.order_manager = OrderManager(self.order_book_manager, self.logger)

        # Initialize dynamic threshold calculator
        dynamic_window = int(os.getenv('DYNAMIC_THRESHOLD_WINDOW', '1000'))
        dynamic_interval = int(os.getenv('DYNAMIC_THRESHOLD_UPDATE_INTERVAL', '300'))
        dynamic_min = Decimal(os.getenv('DYNAMIC_THRESHOLD_MIN', '1.0'))
        dynamic_max = Decimal(os.getenv('DYNAMIC_THRESHOLD_MAX', '10.0'))
        dynamic_percentile = float(os.getenv('DYNAMIC_THRESHOLD_PERCENTILE', '0.70'))
        # 初始化了动态窗口，更新间隔，最小和最大阈值，以及百分位数      
        self.dynamic_threshold = DynamicThresholdCalculator(
            window_size=dynamic_window,
            update_interval=dynamic_interval,
            min_threshold=dynamic_min,
            max_threshold=dynamic_max,
            percentile=dynamic_percentile,
            logger=self.logger
        )

        # Initialize clients (will be set later)
        self.edgex_client = None
        self.edgex_ws_manager = None
        self.lighter_client = None

        # Configuration
        self.lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        self.account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX'))
        self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX'))
        self.edgex_account_id = os.getenv('EDGEX_ACCOUNT_ID')
        self.edgex_stark_private_key = os.getenv('EDGEX_STARK_PRIVATE_KEY')
        self.edgex_base_url = os.getenv('EDGEX_BASE_URL', 'https://pro.edgex.exchange')
        self.edgex_ws_url = os.getenv('EDGEX_WS_URL', 'wss://quote.edgex.exchange')

        # Contract/market info (will be set during initialization)
        self.edgex_contract_id = None
        self.edgex_tick_size = None
        self.lighter_market_index = None
        self.base_amount_multiplier = None
        self.price_multiplier = None
        self.tick_size = None

        # Position tracker (will be initialized after clients)
        self.position_tracker = None

        # BBO logging control (log every hour when no trades)
        self.last_bbo_log_time = None  # None means never logged, will trigger first log
        self.last_status_log_time = None  # None means never logged, will trigger first log
        self.last_skipped_log_time = None  # Control frequency of "opportunity skipped" logs
        self.bbo_log_interval = 3600  # 1 hour in seconds
        self.skipped_log_interval = 300  # 5 minutes for skipped opportunity logs

        # Price tolerance for trade execution (to avoid stale price trading)
        # If price moves more than this percentage, cancel the trade
        self.price_tolerance_pct = Decimal('0.05')  # 0.05% price change tolerance

        # Dynamic threshold configuration
        self.use_dynamic_threshold = os.getenv('USE_DYNAMIC_THRESHOLD', 'false').lower() == 'true'

        # Close threshold configuration (for closing positions with minimal profit)
        # When closing, we use a much lower threshold to allow quick exits
        self.close_threshold_multiplier = Decimal(os.getenv('CLOSE_THRESHOLD_MULTIPLIER', '0.1'))  # 10% of open threshold
        self.min_close_spread = Decimal(os.getenv('MIN_CLOSE_SPREAD', '0.0'))  # Minimum spread to close (0 = break-even)

        # Time-based close threshold configuration (progressive relaxation)
        self.enable_time_based_close = os.getenv('ENABLE_TIME_BASED_CLOSE', 'true').lower() == 'true'
        self.time_based_close_stage1_hours = float(os.getenv('TIME_BASED_CLOSE_STAGE1_HOURS', '1.0'))  # Stage 1: after 1 hour
        self.time_based_close_stage2_hours = float(os.getenv('TIME_BASED_CLOSE_STAGE2_HOURS', '2.0'))  # Stage 2: after 2 hours
        self.time_based_close_stage3_hours = float(os.getenv('TIME_BASED_CLOSE_STAGE3_HOURS', '3.0'))  # Stage 3: after 3 hours (force close)

        # Stage thresholds
        self.stage1_close_multiplier = Decimal(os.getenv('STAGE1_CLOSE_MULTIPLIER', '0.2'))  # Stage 1: 20% of open threshold
        self.stage1_min_spread = Decimal(os.getenv('STAGE1_MIN_SPREAD', '0.3'))  # Stage 1: require 0.3 profit

        self.stage2_close_multiplier = Decimal(os.getenv('STAGE2_CLOSE_MULTIPLIER', '0.1'))  # Stage 2: 10% of open threshold
        self.stage2_min_spread = Decimal(os.getenv('STAGE2_MIN_SPREAD', '0.0'))  # Stage 2: break-even

        self.stage3_close_multiplier = Decimal(os.getenv('STAGE3_CLOSE_MULTIPLIER', '0.05'))  # Stage 3: 5% of open threshold
        self.stage3_min_spread = Decimal(os.getenv('STAGE3_MIN_SPREAD', '-0.5'))  # Stage 3: allow small loss

        # Track position open time
        self.position_open_time = None  # Will be set when position is opened

        # Setup callbacks
        self._setup_callbacks()

    def _setup_logger(self):
        """Setup logging configuration."""
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/edgex_{self.ticker}_log.txt"

        self.logger = logging.getLogger(f"arbi_{self.ticker}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Disable verbose logging from external libraries
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('websockets').setLevel(logging.WARNING)

        # Create file handler
        file_handler = logging.FileHandler(self.log_filename)
        file_handler.setLevel(logging.INFO)

        # Increase buffer size to 64KB for better performance
        if hasattr(file_handler, 'stream') and hasattr(file_handler.stream, 'reconfigure'):
            try:
                file_handler.stream.reconfigure(buffering=65536)  # 64KB buffer
            except Exception:
                # If reconfigure not available (Python < 3.7), ignore
                pass

        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        # Create formatters
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
        console_formatter = logging.Formatter('%(levelname)s:%(name)s:[%(filename)s:%(lineno)d]:%(message)s')

        # Set timezone to UTC+8 (Beijing time)
        def beijing_time(*args):
            """Convert to Beijing time (UTC+8)."""
            import time as time_module
            utc_time = time_module.gmtime(args[0] if args else None)
            # Add 8 hours (28800 seconds) for Beijing timezone
            beijing_timestamp = (args[0] if args else time_module.time()) + 28800
            return time_module.gmtime(beijing_timestamp)

        file_formatter.converter = beijing_time
        console_formatter.converter = beijing_time

        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

    def _setup_callbacks(self):
        """Setup callback functions for order updates."""
        self.ws_manager.set_callbacks(
            on_lighter_order_filled=self._handle_lighter_order_filled,
            on_edgex_order_update=self._handle_edgex_order_update
        )
        self.order_manager.set_callbacks(
            on_order_filled=self._handle_lighter_order_filled
        )

    def _handle_lighter_order_filled(self, order_data: dict):
        """Handle Lighter order fill."""
        try:
            # Calculate average filled price if not already present
            if "avg_filled_price" not in order_data:
                filled_quote = Decimal(order_data.get("filled_quote_amount", 0))
                filled_base = Decimal(order_data.get("filled_base_amount", 0))
                if filled_base > 0:
                    order_data["avg_filled_price"] = filled_quote / filled_base
                else:
                    self.logger.error("❌ Cannot calculate avg price: filled_base_amount is 0")
                    return

            # Determine side and order type
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

            # Log trade to CSV
            self.data_logger.log_trade_to_csv(
                exchange='lighter',
                side=order_data['side'],
                price=str(avg_filled_price),
                quantity=str(filled_base_amount)
            )

            # Mark execution as complete
            self.order_manager.lighter_order_filled = True
            self.order_manager.order_execution_complete = True

        except Exception as e:
            self.logger.error(f"Error handling Lighter order result: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    def _handle_edgex_order_update(self, order: dict):
        """Handle EdgeX order update from WebSocket."""
        try:
            if order.get('contractId') != self.edgex_contract_id:
                return

            if order.get('clientOrderId') != self.order_manager.get_edgex_client_order_id():
                return

            order_id = order.get('id')
            status = order.get('status')
            side = order.get('side', '').lower()
            filled_size = Decimal(order.get('cumMatchSize', '0'))
            size = Decimal(order.get('size', '0'))
            price = order.get('price', '0')

            if side == 'buy':
                order_type = "OPEN"
            else:
                order_type = "CLOSE"

            if status == 'CANCELED' and filled_size > 0:
                status = 'FILLED'

            # Update order status
            self.order_manager.update_edgex_order_status(status)

            # Handle filled orders
            if status == 'FILLED' and filled_size > 0:
                self.logger.info(
                    f"✅ [EdgeX Filled] {side.upper()} {filled_size} @ {price} (order_id={order_id})")

                if side == 'buy':
                    if self.position_tracker:
                        self.position_tracker.update_edgex_position(filled_size)
                else:
                    if self.position_tracker:
                        self.position_tracker.update_edgex_position(-filled_size)

                self.logger.info(
                    f"[{order_id}] [{order_type}] [EdgeX] [{status}]: {filled_size} @ {price}")

                if filled_size > 0.0001:
                    # Log EdgeX trade to CSV
                    self.data_logger.log_trade_to_csv(
                        exchange='edgeX',
                        side=side,
                        price=str(price),
                        quantity=str(filled_size)
                    )

                # Trigger Lighter order placement
                self.logger.info(
                    f"🔄 [Trigger Hedge] EdgeX {side} filled, preparing Lighter hedge order...")

                self.order_manager.handle_edgex_order_update({
                    'order_id': order_id,
                    'side': side,
                    'status': status,
                    'size': size,
                    'price': price,
                    'contract_id': self.edgex_contract_id,
                    'filled_size': filled_size
                })
            elif status != 'FILLED':
                if status == 'OPEN':
                    self.logger.info(f"[{order_id}] [{order_type}] [EdgeX] [{status}]: {size} @ {price}")
                else:
                    self.logger.info(
                        f"[{order_id}] [{order_type}] [EdgeX] [{status}]: {filled_size} @ {price}")

        except Exception as e:
            self.logger.error(f"Error handling EdgeX order update: {e}")

    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler."""
        # Prevent multiple shutdown calls
        if self.stop_flag:
            return

        self.stop_flag = True

        if signum is not None:
            self.logger.info("\n🛑 Stopping...")
        else:
            self.logger.info("🛑 Stopping...")

        # Shutdown WebSocket connections
        try:
            if self.ws_manager:
                self.ws_manager.shutdown()
        except Exception as e:
            self.logger.error(f"Error shutting down WebSocket manager: {e}")

        # Close data logger
        try:
            if self.data_logger:
                self.data_logger.close()
        except Exception as e:
            self.logger.error(f"Error closing data logger: {e}")

        # Close logging handlers
        for handler in self.logger.handlers[:]:
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

        # Note: Async cleanup will be handled in run() finally block

    async def _async_cleanup(self):
        """Async cleanup for aiohttp sessions and other async resources."""
        if self._cleanup_done:
            return

        self._cleanup_done = True

        # Close EdgeX client (closes aiohttp sessions) with timeout
        try:
            if self.edgex_client:
                await asyncio.wait_for(
                    self.edgex_client.close(),
                    timeout=2.0
                )
                self.logger.info("🔌 EdgeX client closed")
        except asyncio.TimeoutError:
            self.logger.warning("⚠️ Timeout closing EdgeX client, forcing shutdown")
        except Exception as e:
            self.logger.error(f"Error closing EdgeX client: {e}")

        # Close EdgeX WebSocket manager connections
        try:
            if self.edgex_ws_manager:
                self.edgex_ws_manager.disconnect_all()
        except Exception as e:
            self.logger.error(f"Error disconnecting EdgeX WebSocket manager: {e}")

        # Flush all logging handlers before exit
        try:
            for handler in self.logger.handlers:
                if hasattr(handler, 'flush'):
                    handler.flush()
            self.logger.info("📝 All log handlers flushed")
        except Exception as e:
            # Use print as fallback since logger might be broken
            print(f"Error flushing log handlers: {e}")

        # Ensure data logger is closed (redundant safety check)
        try:
            if self.data_logger:
                self.data_logger.close()
        except Exception as e:
            print(f"Error in final data_logger close: {e}")

    def _get_time_based_close_thresholds(self, open_threshold: Decimal) -> tuple:
        """
        Calculate close thresholds based on position holding time.

        Returns:
            tuple: (close_threshold_multiplier, min_close_spread, stage_name)
        """
        if not self.enable_time_based_close or self.position_open_time is None:
            # Time-based close disabled or no position, use default
            return self.close_threshold_multiplier, self.min_close_spread, "default"

        # Calculate holding time in hours
        holding_time_hours = (time.time() - self.position_open_time) / 3600.0

        # Determine stage based on holding time
        if holding_time_hours >= self.time_based_close_stage3_hours:
            # Stage 3: Force close (allow small loss)
            return self.stage3_close_multiplier, self.stage3_min_spread, "stage3_force"
        elif holding_time_hours >= self.time_based_close_stage2_hours:
            # Stage 2: Break-even close
            return self.stage2_close_multiplier, self.stage2_min_spread, "stage2_breakeven"
        elif holding_time_hours >= self.time_based_close_stage1_hours:
            # Stage 1: Relaxed close (require some profit)
            return self.stage1_close_multiplier, self.stage1_min_spread, "stage1_relaxed"
        else:
            # Before stage 1: Use default (strict)
            return self.close_threshold_multiplier, self.min_close_spread, "default"

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def initialize_lighter_client(self):
        """Initialize the Lighter client."""
        if self.lighter_client is None:
            api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY')
            if not api_key_private_key:
                raise Exception("API_KEY_PRIVATE_KEY environment variable not set")

            # Create api_private_keys dictionary with the index as key
            api_private_keys = {self.api_key_index: api_key_private_key}

            self.lighter_client = SignerClient(
                url=self.lighter_base_url,
                account_index=self.account_index,
                api_private_keys=api_private_keys,
            )

            err = self.lighter_client.check_client()
            if err is not None:
                raise Exception(f"CheckClient error: {err}")

            self.logger.info("✅ Lighter client initialized successfully")
        return self.lighter_client

    def initialize_edgex_client(self):
        """Initialize the EdgeX client."""
        if not self.edgex_account_id or not self.edgex_stark_private_key:
            raise ValueError(
                "EDGEX_ACCOUNT_ID and EDGEX_STARK_PRIVATE_KEY must be set in environment variables")

        self.edgex_client = Client(
            base_url=self.edgex_base_url,
            account_id=int(self.edgex_account_id),
            stark_private_key=self.edgex_stark_private_key
        )

        self.edgex_ws_manager = WebSocketManager(
            base_url=self.edgex_ws_url,
            account_id=int(self.edgex_account_id),
            stark_pri_key=self.edgex_stark_private_key
        )

        self.logger.info("✅ EdgeX client initialized successfully")
        return self.edgex_client

    def get_lighter_market_config(self) -> Tuple[int, int, int, Decimal]:
        """Get Lighter market configuration."""
        url = f"{self.lighter_base_url}/api/v1/orderBooks"
        headers = {"accept": "application/json"}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            if not response.text.strip():
                raise Exception("Empty response from Lighter API")

            data = response.json()

            if "order_books" not in data:
                raise Exception("Unexpected response format")

            for market in data["order_books"]:
                if market["symbol"] == self.ticker:
                    price_multiplier = pow(10, market["supported_price_decimals"])
                    return (market["market_id"],
                            pow(10, market["supported_size_decimals"]),
                            price_multiplier,
                            Decimal("1") / (Decimal("10") ** market["supported_price_decimals"]))
            raise Exception(f"Ticker {self.ticker} not found")

        except Exception as e:
            self.logger.error(f"⚠️ Error getting market config: {e}")
            raise

    async def get_edgex_contract_info(self) -> Tuple[str, Decimal]:
        """Get EdgeX contract ID and tick size."""
        if not self.edgex_client:
            raise Exception("EdgeX client not initialized")

        response = await self.edgex_client.get_metadata()
        data = response.get('data', {})
        if not data:
            raise ValueError("Failed to get EdgeX metadata")

        contract_list = data.get('contractList', [])
        if not contract_list:
            raise ValueError("Failed to get EdgeX contract list")

        current_contract = None
        for c in contract_list:
            if c.get('contractName') == self.ticker + 'USD':
                current_contract = c
                break

        if not current_contract:
            raise ValueError(f"Failed to get contract ID for ticker {self.ticker}")

        contract_id = current_contract.get('contractId')
        min_quantity = Decimal(current_contract.get('minOrderSize'))
        tick_size = Decimal(current_contract.get('tickSize'))

        if self.order_quantity < min_quantity:
            raise ValueError(
                f"Order quantity is less than min quantity: {self.order_quantity} < {min_quantity}")

        return contract_id, tick_size

    async def trading_loop(self):
        """Main trading loop implementing the strategy."""
        self.logger.info(f"🚀 Starting arbitrage bot for {self.ticker}")

        # Initialize clients
        try:
            self.initialize_lighter_client()
            self.initialize_edgex_client()

            # Get contract info
            self.edgex_contract_id, self.edgex_tick_size = await self.get_edgex_contract_info()
            (self.lighter_market_index, self.base_amount_multiplier,
             self.price_multiplier, self.tick_size) = self.get_lighter_market_config()

            self.logger.info(
                f"Contract info loaded - EdgeX: {self.edgex_contract_id}, "
                f"Lighter: {self.lighter_market_index}")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize: {e}")
            return

        # Initialize position tracker
        self.position_tracker = PositionTracker(
            self.ticker,
            self.edgex_client,
            self.edgex_contract_id,
            self.lighter_base_url,
            self.account_index,
            self.logger
        )

        # Configure modules
        self.order_manager.set_edgex_config(
            self.edgex_client, self.edgex_contract_id, self.edgex_tick_size)
        self.order_manager.set_lighter_config(
            self.lighter_client, self.lighter_market_index,
            self.base_amount_multiplier, self.price_multiplier, self.tick_size)

        self.ws_manager.set_edgex_ws_manager(self.edgex_ws_manager, self.edgex_contract_id)
        self.ws_manager.set_lighter_config(
            self.lighter_client, self.lighter_market_index, self.account_index)

        # Setup EdgeX websocket
        try:
            await self.ws_manager.setup_edgex_websocket()
            self.logger.info("✅ EdgeX WebSocket connection established")

            # Wait for initial order book data
            self.logger.info("⏳ Waiting for initial EdgeX order book data...")
            timeout = 10
            start_time = time.time()
            while not self.order_book_manager.edgex_order_book_ready and not self.stop_flag:
                if time.time() - start_time > timeout:
                    self.logger.warning(
                        f"⚠️ Timeout waiting for WebSocket order book data after {timeout}s")
                    break
                await asyncio.sleep(0.5)

            if self.order_book_manager.edgex_order_book_ready:
                self.logger.info("✅ WebSocket order book data received")
            else:
                self.logger.warning("⚠️ WebSocket order book not ready, will use REST API fallback")

        except Exception as e:
            self.logger.error(f"❌ Failed to setup EdgeX websocket: {e}")
            return

        # Setup Lighter websocket
        try:
            self.ws_manager.start_lighter_websocket()
            self.logger.info("✅ Lighter WebSocket task started")

            # Wait for initial Lighter order book data
            self.logger.info("⏳ Waiting for initial Lighter order book data...")
            timeout = 10
            start_time = time.time()
            while (not self.order_book_manager.lighter_order_book_ready and
                   not self.stop_flag):
                if time.time() - start_time > timeout:
                    self.logger.warning(
                        f"⚠️ Timeout waiting for Lighter WebSocket order book data after {timeout}s")
                    break
                await asyncio.sleep(0.5)

            if self.order_book_manager.lighter_order_book_ready:
                self.logger.info("✅ Lighter WebSocket order book data received")
            else:
                self.logger.warning("⚠️ Lighter WebSocket order book not ready")

        except Exception as e:
            self.logger.error(f"❌ Failed to setup Lighter websocket: {e}")
            return

        await asyncio.sleep(5)

        # Get initial positions
        self.position_tracker.edgex_position = await self.position_tracker.get_edgex_position()
        self.position_tracker.lighter_position = await self.position_tracker.get_lighter_position()

        self.logger.info(f"📍 Starting main trading loop for {self.ticker}")

        # Main trading loop
        while not self.stop_flag:
            # Optimize: Try to get BBO from WebSocket cache first (synchronous, fast)
            ex_best_bid, ex_best_ask = self.order_book_manager.get_edgex_bbo()

            # If WebSocket data is not ready, fallback to REST API
            if not (self.order_book_manager.edgex_order_book_ready and
                    ex_best_bid and ex_best_ask and ex_best_bid > 0 and ex_best_ask > 0):
                try:
                    ex_best_bid, ex_best_ask = await asyncio.wait_for(
                        self.order_manager.fetch_edgex_bbo_prices(),
                        timeout=2.0  # Reduced from 5s to 2s
                    )
                except asyncio.TimeoutError:
                    self.logger.warning("⚠️ Timeout fetching EdgeX BBO prices")
                    await asyncio.sleep(0.1)  # Reduced from 0.5s to 0.1s
                    continue
                except Exception as e:
                    self.logger.error(f"⚠️ Error fetching EdgeX BBO prices: {e}")
                    await asyncio.sleep(0.1)  # Reduced from 0.5s to 0.1s
                    continue

            lighter_bid, lighter_ask = self.order_book_manager.get_lighter_bbo()

            # Calculate current spreads，每次的价差与阈值比较
            long_spread = (lighter_bid - ex_best_bid) if (lighter_bid and ex_best_bid) else Decimal('0')
            short_spread = (ex_best_ask - lighter_ask) if (ex_best_ask and lighter_ask) else Decimal('0')

            # Add spread observation to dynamic threshold calculator
            if lighter_bid and ex_best_bid and ex_best_ask and lighter_ask:
                self.dynamic_threshold.add_spread_observation(long_spread, short_spread)

            # Get current thresholds (dynamic or fixed)
            if self.use_dynamic_threshold:
                long_threshold, short_threshold = self.dynamic_threshold.get_thresholds()
            else:
                long_threshold, short_threshold = self.long_ex_threshold, self.short_ex_threshold

            # Get current position to determine if we're opening or closing
            current_position = self.position_tracker.get_current_edgex_position()

            # Calculate close thresholds based on holding time (if position exists)
            if current_position != 0:
                # Get time-based close thresholds
                close_multiplier, min_close_spread, stage_name = self._get_time_based_close_thresholds(short_threshold)
                long_close_threshold = max(long_threshold * close_multiplier, min_close_spread)
                short_close_threshold = max(short_threshold * close_multiplier, min_close_spread)

                # Calculate holding time for logging
                holding_time_hours = (time.time() - self.position_open_time) / 3600.0 if self.position_open_time else 0
            else:
                # No position, use default close thresholds
                long_close_threshold = max(long_threshold * self.close_threshold_multiplier, self.min_close_spread)
                short_close_threshold = max(short_threshold * self.close_threshold_multiplier, self.min_close_spread)
                stage_name = "default"
                holding_time_hours = 0

            # Determine if we should trade using current thresholds
            long_ex = False
            short_ex = False

            # Long opportunity: buy EdgeX, sell Lighter
            # - If position <= 0: we're opening or adding to long → use strict threshold
            # - If position > 0: we're already long, don't add more
            if lighter_bid and ex_best_bid and long_spread > long_threshold and current_position <= 0:
                long_ex = True

            # Short opportunity: sell EdgeX, buy Lighter
            # - If position >= 0: we're closing long or opening short → use relaxed threshold for closing
            # - If position < 0: we're already short, don't add more
            elif ex_best_ask and lighter_ask:
                if current_position > 0:
                    # We have long position, use relaxed close threshold
                    if short_spread > short_close_threshold:
                        short_ex = True
                elif current_position == 0:
                    # No position, opening short, use strict threshold
                    if short_spread > short_threshold:
                        short_ex = True

            # Check if we should log BBO data (only hourly to avoid spam)
            current_time = time.time()
            should_log_bbo = (
                self.last_bbo_log_time is None or  # First time logging
                (current_time - self.last_bbo_log_time >= self.bbo_log_interval)  # Hourly log
            )

            if should_log_bbo:
                # Log BBO data hourly
                self.data_logger.log_bbo_to_csv(
                    maker_bid=ex_best_bid,
                    maker_ask=ex_best_ask,
                    lighter_bid=lighter_bid if lighter_bid else Decimal('0'),
                    lighter_ask=lighter_ask if lighter_ask else Decimal('0'),
                    long_maker=long_ex,
                    short_maker=short_ex,
                    long_maker_threshold=self.long_ex_threshold,
                    short_maker_threshold=self.short_ex_threshold
                )
                self.last_bbo_log_time = current_time

            # Log status every hour when no trading opportunities
            if not long_ex and not short_ex and (
                self.last_status_log_time is None or
                (current_time - self.last_status_log_time >= self.bbo_log_interval)
            ):
                # Get current thresholds for logging
                if self.use_dynamic_threshold:
                    current_long_threshold, current_short_threshold = self.dynamic_threshold.get_thresholds()
                    threshold_mode = "dynamic"
                else:
                    current_long_threshold, current_short_threshold = self.long_ex_threshold, self.short_ex_threshold
                    threshold_mode = "fixed"

                self.logger.info(
                    f"📊 Hourly EX: bid={ex_best_bid}, ask={ex_best_ask} | "
                    f"LT: bid={lighter_bid}, ask={lighter_ask} | "
                    f"L spread={long_spread:.2f} (threshold={current_long_threshold:.2f} {threshold_mode}), "
                    f"S spread={short_spread:.2f} (threshold={current_short_threshold:.2f} {threshold_mode}) | "
                    f"EX position={self.position_tracker.get_current_edgex_position()}, "
                    f"LT position={self.position_tracker.lighter_position}"
                )
                self.last_status_log_time = current_time

            if self.stop_flag:
                break

            # Execute trades
            current_position = self.position_tracker.get_current_edgex_position()

            # Check long opportunity
            if long_ex:
                spread = lighter_bid - ex_best_bid
                if current_position < self.max_position:
                    # Can execute long trade
                    self.logger.info(
                        f"🔍 [OPPORTUNITY] Long EdgeX detected! "
                        f"Lighter_bid={lighter_bid} - EdgeX_bid={ex_best_bid} = {spread:.2f} > threshold={long_threshold:.2f}")
                    self.logger.info(
                        f"💡 [Strategy] Will BUY on EdgeX @ ~{ex_best_ask} (ask-tick), "
                        f"then SELL on Lighter @ ~{lighter_bid}")
                    self.logger.info(
                        f"⏱️ [Opportunity Prices] EdgeX: bid={ex_best_bid}, ask={ex_best_ask} | "
                        f"Lighter: bid={lighter_bid}, ask={lighter_ask}")
                    self.last_status_log_time = current_time  # Reset status log time after trade log
                    # Pass expected prices for validation
                    await self._execute_long_trade(expected_edgex_ask=ex_best_ask, expected_lighter_bid=lighter_bid)
                else:
                    # Already at max long position, only log occasionally to avoid spam
                    if (self.last_skipped_log_time is None or
                        (current_time - self.last_skipped_log_time >= self.skipped_log_interval)):
                        self.logger.info(
                            f"📊 [OPPORTUNITY SKIPPED] Long EdgeX - Position limit reached! "
                            f"EdgeX: bid={ex_best_bid}, ask={ex_best_ask} | "
                            f"Lighter: bid={lighter_bid}, ask={lighter_ask} | "
                            f"Spread={spread:.2f} > threshold={long_threshold:.2f} | "
                            f"Position={current_position}/{self.max_position}")
                        self.last_skipped_log_time = current_time
                    self.last_status_log_time = current_time
                    # Removed sleep - continue immediately to check for new opportunities

            # Check short opportunity
            elif short_ex:
                spread = ex_best_ask - lighter_ask
                # Determine if this is a close or open trade
                is_closing = current_position > 0
                used_threshold = short_close_threshold if is_closing else short_threshold
                action_type = "CLOSE LONG" if is_closing else "OPEN SHORT"

                if current_position > -1 * self.max_position:
                    # Can execute short trade
                    # Build log message with holding time if closing
                    if is_closing and self.enable_time_based_close:
                        time_info = f" | Holding: {holding_time_hours:.2f}h ({stage_name})"
                    else:
                        time_info = ""

                    self.logger.info(
                        f"🔍 [OPPORTUNITY] Short EdgeX detected ({action_type})! "
                        f"EdgeX_ask={ex_best_ask} - Lighter_ask={lighter_ask} = {spread:.2f} > threshold={used_threshold:.2f}{time_info}")
                    self.logger.info(
                        f"💡 [Strategy] Will SELL on EdgeX @ ~{ex_best_bid} (bid+tick), "
                        f"then BUY on Lighter @ ~{lighter_ask}")
                    self.logger.info(
                        f"⏱️ [Opportunity Prices] EdgeX: bid={ex_best_bid}, ask={ex_best_ask} | "
                        f"Lighter: bid={lighter_bid}, ask={lighter_ask} | "
                        f"Current position={current_position}")
                    self.last_status_log_time = current_time  # Reset status log time after trade log
                    # Pass expected prices for validation
                    await self._execute_short_trade(expected_edgex_bid=ex_best_bid, expected_lighter_ask=lighter_ask)
                else:
                    # Already at max short position, only log occasionally to avoid spam
                    if (self.last_skipped_log_time is None or
                        (current_time - self.last_skipped_log_time >= self.skipped_log_interval)):
                        self.logger.info(
                            f"📊 [OPPORTUNITY SKIPPED] Short EdgeX - Position limit reached! "
                            f"EdgeX: bid={ex_best_bid}, ask={ex_best_ask} | "
                            f"Lighter: bid={lighter_bid}, ask={lighter_ask} | "
                            f"Spread={spread:.2f} > threshold={short_threshold:.2f} | "
                            f"Position={current_position}/{-1 * self.max_position}")
                        self.last_skipped_log_time = current_time
                    self.last_status_log_time = current_time
                    # Removed sleep - continue immediately to check for new opportunities
            else:
                # No opportunity detected, add minimal sleep to prevent busy-waiting
                await asyncio.sleep(0.01)  # 10ms instead of 50ms

    async def _execute_long_trade(self, expected_edgex_ask=None, expected_lighter_bid=None):
        """Execute a long trade (buy on EdgeX, sell on Lighter)."""
        trade_start_time = time.time()
        self.logger.info(f"⏱️ LONG TRADE START")

        # Record position open time if opening a new position
        if self.position_tracker.get_current_edgex_position() == 0:
            self.position_open_time = time.time()
            self.logger.info(f"📍 Position open time recorded: {self.position_open_time}")

        if self.stop_flag:
            return

        # Use cached positions (updated by order callbacks)
        # Only query positions at startup or on errors
        self.logger.info(
            f"EdgeX position (cached): {self.position_tracker.edgex_position} | "
            f"Lighter position (cached): {self.position_tracker.lighter_position}")

        if abs(self.position_tracker.get_net_position()) > self.order_quantity * 2:
            self.logger.error(
                f"❌ Position diff is too large: {self.position_tracker.get_net_position()}")
            sys.exit(1)

        # Check price tolerance before placing order (for long trade)
        if expected_edgex_ask is not None:
            current_edgex_ask = self.order_book_manager.get_edgex_bbo()[1]
            if current_edgex_ask:
                price_change_pct = abs((current_edgex_ask - expected_edgex_ask) / expected_edgex_ask * 100)
                self.logger.info(
                    f"🔍 [Price Check] Expected EdgeX ask: {expected_edgex_ask}, "
                    f"Current: {current_edgex_ask}, Change: {price_change_pct:.3f}%")

                if price_change_pct > self.price_tolerance_pct:
                    self.logger.warning(
                        f"⚠️ Price moved too much! Change {price_change_pct:.3f}% > tolerance {self.price_tolerance_pct}%. "
                        f"Cancelling trade to avoid unfavorable execution.")
                    return

        self.order_manager.order_execution_complete = False
        self.order_manager.waiting_for_lighter_fill = False

        try:
            side = 'buy'
            order_start = time.time()
            order_filled = await self.order_manager.place_edgex_post_only_order(
                side, self.order_quantity, self.stop_flag)
            order_time = time.time() - order_start
            self.logger.info(f"⏱️ EdgeX order placement: {order_time:.3f}s")

            if not order_filled or self.stop_flag:
                return
        except Exception as e:
            if self.stop_flag:
                return
            self.logger.error(f"⚠️ Error in trading loop: {e}")
            self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
            sys.exit(1)

        start_time = time.time()
        while not self.order_manager.order_execution_complete and not self.stop_flag:
            if self.order_manager.waiting_for_lighter_fill:
                hedge_start = time.time()
                await self.order_manager.place_lighter_market_order(
                    self.order_manager.current_lighter_side,
                    self.order_manager.current_lighter_quantity,
                    self.order_manager.current_lighter_price,
                    self.stop_flag
                )
                hedge_time = time.time() - hedge_start
                self.logger.info(f"⏱️ Lighter hedge placement: {hedge_time:.3f}s")
                break

            await asyncio.sleep(0.01)
            if time.time() - start_time > 180:
                self.logger.error("❌ Timeout waiting for trade completion")
                break

        total_time = time.time() - trade_start_time
        self.logger.info(f"⏱️ LONG TRADE TOTAL EXECUTION: {total_time:.3f}s")

    async def _execute_short_trade(self, expected_edgex_bid=None, expected_lighter_ask=None):
        """Execute a short trade (sell on EdgeX, buy on Lighter)."""
        trade_start_time = time.time()
        self.logger.info(f"⏱️ SHORT TRADE START")

        # Check if this is closing a long position or opening a short position
        current_position = self.position_tracker.get_current_edgex_position()
        is_closing_long = current_position > 0

        # If opening a new short position, record open time
        if current_position == 0:
            self.position_open_time = time.time()
            self.logger.info(f"📍 Position open time recorded: {self.position_open_time}")
        # If closing long position, reset open time
        elif is_closing_long:
            if self.position_open_time:
                holding_duration = time.time() - self.position_open_time
                self.logger.info(f"📍 Closing position held for {holding_duration/3600:.2f} hours")
            self.position_open_time = None

        if self.stop_flag:
            return

        # Use cached positions (updated by order callbacks)
        # Only query positions at startup or on errors
        self.logger.info(
            f"EdgeX position (cached): {self.position_tracker.edgex_position} | "
            f"Lighter position (cached): {self.position_tracker.lighter_position}")

        if abs(self.position_tracker.get_net_position()) > self.order_quantity * 2:
            self.logger.error(
                f"❌ Position diff is too large: {self.position_tracker.get_net_position()}")
            sys.exit(1)

        # Check price tolerance before placing order (for short trade)
        if expected_edgex_bid is not None:
            current_edgex_bid = self.order_book_manager.get_edgex_bbo()[0]
            if current_edgex_bid:
                price_change_pct = abs((current_edgex_bid - expected_edgex_bid) / expected_edgex_bid * 100)
                self.logger.info(
                    f"🔍 [Price Check] Expected EdgeX bid: {expected_edgex_bid}, "
                    f"Current: {current_edgex_bid}, Change: {price_change_pct:.3f}%")

                if price_change_pct > self.price_tolerance_pct:
                    self.logger.warning(
                        f"⚠️ Price moved too much! Change {price_change_pct:.3f}% > tolerance {self.price_tolerance_pct}%. "
                        f"Cancelling trade to avoid unfavorable execution.")
                    return

        self.order_manager.order_execution_complete = False
        self.order_manager.waiting_for_lighter_fill = False

        try:
            side = 'sell'
            order_start = time.time()
            order_filled = await self.order_manager.place_edgex_post_only_order(
                side, self.order_quantity, self.stop_flag)
            order_time = time.time() - order_start
            self.logger.info(f"⏱️ EdgeX order placement: {order_time:.3f}s")

            if not order_filled or self.stop_flag:
                return
        except Exception as e:
            if self.stop_flag:
                return
            self.logger.error(f"⚠️ Error in trading loop: {e}")
            self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
            sys.exit(1)

        start_time = time.time()
        while not self.order_manager.order_execution_complete and not self.stop_flag:
            if self.order_manager.waiting_for_lighter_fill:
                hedge_start = time.time()
                await self.order_manager.place_lighter_market_order(
                    self.order_manager.current_lighter_side,
                    self.order_manager.current_lighter_quantity,
                    self.order_manager.current_lighter_price,
                    self.stop_flag
                )
                hedge_time = time.time() - hedge_start
                self.logger.info(f"⏱️ Lighter hedge placement: {hedge_time:.3f}s")
                break

            await asyncio.sleep(0.01)
            if time.time() - start_time > 180:
                self.logger.error("❌ Timeout waiting for trade completion")
                break

        total_time = time.time() - trade_start_time
        self.logger.info(f"⏱️ SHORT TRADE TOTAL EXECUTION: {total_time:.3f}s")

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
            # Ensure async cleanup is done with timeout
            try:
                await asyncio.wait_for(self._async_cleanup(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ Cleanup timeout, forcing exit")
            except Exception as e:
                self.logger.error(f"Error during cleanup: {e}")
