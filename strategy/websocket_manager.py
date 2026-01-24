"""WebSocket management for EdgeX and Lighter exchanges."""
import asyncio
import json
import logging
import time
import traceback
import websockets
from typing import Callable, Optional

from edgex_sdk import WebSocketManager


class WebSocketManagerWrapper:
    """Manages WebSocket connections for both exchanges."""

    def __init__(self, order_book_manager, logger: logging.Logger):
        """Initialize WebSocket manager."""
        self.order_book_manager = order_book_manager
        self.logger = logger
        self.stop_flag = False

        # EdgeX WebSocket
        self.edgex_ws_manager: Optional[WebSocketManager] = None
        self.edgex_contract_id: Optional[str] = None

        # Lighter WebSocket
        self.lighter_ws_task: Optional[asyncio.Task] = None
        self.lighter_client = None
        self.lighter_market_index: Optional[int] = None
        self.account_index: Optional[int] = None

        # Callbacks
        self.on_lighter_order_filled: Optional[Callable] = None
        self.on_edgex_order_update: Optional[Callable] = None

    def set_edgex_ws_manager(self, ws_manager: WebSocketManager, contract_id: str):
        """Set EdgeX WebSocket manager and contract ID."""
        self.edgex_ws_manager = ws_manager
        self.edgex_contract_id = contract_id

    def set_lighter_config(self, client, market_index: int, account_index: int):
        """Set Lighter client and configuration."""
        self.lighter_client = client
        self.lighter_market_index = market_index
        self.account_index = account_index

    def set_callbacks(self, on_lighter_order_filled: Callable = None,
                      on_edgex_order_update: Callable = None):
        """Set callback functions for order updates."""
        self.on_lighter_order_filled = on_lighter_order_filled
        self.on_edgex_order_update = on_edgex_order_update

    # EdgeX WebSocket methods
    def handle_edgex_order_book_update(self, message):
        """Handle EdgeX order book updates from WebSocket."""
        try:
            if isinstance(message, str):
                message = json.loads(message)

            self.logger.debug(f"Received EdgeX depth message: {message}")

            # Check if this is a quote-event message with depth data
            if message.get("type") == "quote-event":
                content = message.get("content", {})
                channel = message.get("channel", "")

                if channel.startswith("depth."):
                    data = content.get('data', [])
                    if data and len(data) > 0:
                        order_book_data = data[0]
                        depth_type = order_book_data.get('depthType', '')

                        # Handle SNAPSHOT (full data) or CHANGED (incremental updates)
                        if depth_type in ['SNAPSHOT', 'CHANGED']:
                            bids = order_book_data.get('bids', [])
                            asks = order_book_data.get('asks', [])
                            self.order_book_manager.update_edgex_order_book(bids, asks)

        except Exception as e:
            self.logger.error(f"Error handling EdgeX order book update: {e}")
            self.logger.error(f"Message content: {message}")

    async def setup_edgex_websocket(self):
        """Setup EdgeX websocket for order updates and order book data."""
        if not self.edgex_ws_manager:
            raise Exception("EdgeX WebSocket manager not initialized")

        def order_update_handler(message):
            """Handle order updates from EdgeX WebSocket."""
            if isinstance(message, str):
                message = json.loads(message)

            content = message.get("content", {})
            event = content.get("event", "")
            try:
                if event == "ORDER_UPDATE":
                    data = content.get('data', {})
                    orders = data.get('order', [])

                    if orders and len(orders) > 0:
                        # EdgeX returns TWO filled events for the same order; skip the second one
                        # The second one has collateral data, so we filter it out
                        order_status = orders[0].get('status', '')
                        if order_status == "FILLED" and len(data.get('collateral', [])):
                            return  # Skip duplicate FILLED event

                        for order in orders:
                            if order.get('contractId') != self.edgex_contract_id:
                                continue

                            if self.on_edgex_order_update:
                                self.on_edgex_order_update(order)

            except Exception as e:
                self.logger.error(f"Error handling EdgeX order update: {e}")

        try:
            # Setup order update handler
            private_client = self.edgex_ws_manager.get_private_client()
            private_client.on_message("trade-event", order_update_handler)
            self.logger.info("✅ EdgeX WebSocket order update handler set up")

            # Connect to EdgeX WebSocket
            self.edgex_ws_manager.connect_public()
            self.edgex_ws_manager.connect_private()
            # self.logger.info("✅ EdgeX WebSocket connection established")

            # Setup public client for market data
            public_client = self.edgex_ws_manager.get_public_client()

            # Register handler for depth messages
            public_client.on_message("depth", self.handle_edgex_order_book_update)
            # self.logger.info("✅ EdgeX WebSocket depth handler registered")

            # Subscribe to depth channel after connection is established
            public_client.subscribe(f"depth.{self.edgex_contract_id}.15")
            self.logger.info(f"✅ Subscribed to depth channel: depth.{self.edgex_contract_id}.15")

        except Exception as e:
            self.logger.error(f"Could not setup EdgeX WebSocket handlers: {e}")

    # Lighter WebSocket methods
    async def request_fresh_snapshot(self, ws):
        """Request fresh order book snapshot."""
        await ws.send(json.dumps({
            "type": "subscribe",
            "channel": f"order_book/{self.lighter_market_index}"
        }))

    async def handle_lighter_ws(self):
        """Handle Lighter WebSocket connection and messages."""
        url = "wss://mainnet.zklighter.elliot.ai/stream"
        cleanup_counter = 0

        while not self.stop_flag:
            timeout_count = 0
            try:
                # Reset order book state before connecting
                await self.order_book_manager.reset_lighter_order_book()

                async with websockets.connect(url) as ws:
                    # Subscribe to order book updates
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "channel": f"order_book/{self.lighter_market_index}"
                    }))

                    # Subscribe to account orders updates
                    account_orders_channel = f"account_orders/{self.lighter_market_index}/{self.account_index}"

                    # Track subscription state to prevent duplicate subscriptions
                    is_subscribed = False

                    # Function to subscribe to account orders with auth token
                    async def subscribe_account_orders():
                        nonlocal is_subscribed

                        # Skip if already subscribed
                        if is_subscribed:
                            self.logger.debug("⏭️ Already subscribed, skipping duplicate subscription")
                            return True

                        try:
                            # Use default expiry time (SDK handles it automatically)
                            self.logger.info(f"🔑 Creating auth token for channel: {account_orders_channel}")
                            auth_token, err = self.lighter_client.create_auth_token_with_expiry()
                            if err is not None:
                                self.logger.warning(f"⚠️ Failed to create auth token: {err}")
                                return False
                            else:
                                auth_message = {
                                    "type": "subscribe",
                                    "channel": account_orders_channel,
                                    "auth": auth_token
                                }
                                self.logger.info(f"🔑 Auth token created successfully")
                                await ws.send(json.dumps(auth_message))
                                is_subscribed = True  # Mark as subscribed
                                self.logger.info("✅ Subscribed to account orders with auth token")
                                return True
                        except Exception as e:
                            self.logger.warning(f"⚠️ Error creating auth token: {e}")
                            return False

                    # Initial subscription (one-time, remains active until WebSocket disconnects)
                    await subscribe_account_orders()

                    while not self.stop_flag:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1)

                            try:
                                data = json.loads(msg)
                                # Log all message types for debugging (except frequent order_book updates)
                                msg_type = data.get("type", "UNKNOWN")
                                if msg_type not in ["update/order_book", "ping"]:
                                    self.logger.info(f"📬 [Lighter WS] Received message type: {msg_type}")
                                    # If message type is UNKNOWN, log the full message to debug
                                    if msg_type == "UNKNOWN":
                                        self.logger.warning(f"⚠️ [Lighter WS] UNKNOWN message content: {data}")
                            except json.JSONDecodeError as e:
                                self.logger.warning(f"⚠️ JSON parsing error: {e}")
                                continue

                            timeout_count = 0

                            async with self.order_book_manager.lighter_order_book_lock:
                                if data.get("type") == "subscribed/order_book":
                                    # Initial snapshot
                                    self.order_book_manager.lighter_order_book["bids"].clear()
                                    self.order_book_manager.lighter_order_book["asks"].clear()

                                    order_book = data.get("order_book", {})
                                    if order_book and "offset" in order_book:
                                        self.order_book_manager.lighter_order_book_offset = order_book["offset"]
                                        self.logger.info(
                                            f"✅ Initial order book offset: "
                                            f"{self.order_book_manager.lighter_order_book_offset}")

                                    bids = order_book.get("bids", [])
                                    asks = order_book.get("asks", [])

                                    self.order_book_manager.update_lighter_order_book("bids", bids)
                                    self.order_book_manager.update_lighter_order_book("asks", asks)
                                    self.order_book_manager.lighter_snapshot_loaded = True
                                    self.order_book_manager.lighter_order_book_ready = True
                                    self.order_book_manager.update_lighter_bbo()

                                    self.logger.info(
                                        f"✅ Lighter order book snapshot loaded with "
                                        f"{len(self.order_book_manager.lighter_order_book['bids'])} bids and "
                                        f"{len(self.order_book_manager.lighter_order_book['asks'])} asks")

                                elif data.get("type") == "subscribed/account_orders":
                                    # Account orders subscription confirmed
                                    channel = data.get("channel", "UNKNOWN")
                                    self.logger.info(f"✅ [Lighter WS] Account orders subscription confirmed for channel: {channel}")

                                elif (data.get("type") == "update/order_book" and
                                      self.order_book_manager.lighter_snapshot_loaded):
                                    order_book = data.get("order_book", {})
                                    if not order_book or "offset" not in order_book:
                                        self.logger.warning("⚠️ Order book update missing offset, skipping")
                                        continue

                                    new_offset = order_book["offset"]

                                    if not self.order_book_manager.validate_order_book_offset(new_offset):
                                        self.order_book_manager.lighter_order_book_sequence_gap = True
                                        break

                                    self.order_book_manager.update_lighter_order_book(
                                        "bids", order_book.get("bids", []))
                                    self.order_book_manager.update_lighter_order_book(
                                        "asks", order_book.get("asks", []))

                                    if not self.order_book_manager.validate_order_book_integrity():
                                        self.logger.warning(
                                            "🔄 Order book integrity check failed, requesting fresh snapshot...")
                                        break

                                    self.order_book_manager.update_lighter_bbo()

                                elif data.get("type") == "ping":
                                    await ws.send(json.dumps({"type": "pong"}))

                                elif data.get("type") == "update/account_orders":
                                    orders = data.get("orders", {}).get(str(self.lighter_market_index), [])
                                    self.logger.info(
                                        f"📨 [Lighter Order Update] Received {len(orders)} order(s) for market {self.lighter_market_index}")
                                    for order in orders:
                                        order_status = order.get("status")
                                        client_order_id = order.get("client_order_id", "UNKNOWN")
                                        self.logger.info(
                                            f"📋 [Lighter Order] client_order_id={client_order_id}, status={order_status}")
                                        if order_status == "filled" and self.on_lighter_order_filled:
                                            self.on_lighter_order_filled(order)
                                        elif order_status and order_status != "filled":
                                            self.logger.warning(
                                                f"⚠️ [Lighter Order] Received order with status '{order_status}' "
                                                f"(not 'filled'), order data: {order}")

                                elif (data.get("type") == "update/order_book" and
                                      not self.order_book_manager.lighter_snapshot_loaded):
                                    continue

                            cleanup_counter += 1
                            if cleanup_counter >= 1000:
                                cleanup_counter = 0

                            if self.order_book_manager.lighter_order_book_sequence_gap:
                                try:
                                    await self.request_fresh_snapshot(ws)
                                    self.order_book_manager.lighter_order_book_sequence_gap = False
                                except Exception as e:
                                    self.logger.error(f"⚠️ Failed to request fresh snapshot: {e}")
                                    break

                        except asyncio.TimeoutError:
                            timeout_count += 1
                            if timeout_count % 3 == 0:
                                self.logger.warning(
                                    f"⏰ No message from Lighter websocket for {timeout_count} seconds")
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            self.logger.warning(f"⚠️ Lighter websocket connection closed: {e}")
                            break
                        except websockets.exceptions.WebSocketException as e:
                            self.logger.warning(f"⚠️ Lighter websocket error: {e}")
                            break
                        except Exception as e:
                            self.logger.error(f"⚠️ Error in Lighter websocket: {e}")
                            self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
                            break
            except Exception as e:
                self.logger.error(f"⚠️ Failed to connect to Lighter websocket: {e}")

            await asyncio.sleep(2)

    def start_lighter_websocket(self):
        """Start Lighter WebSocket task."""
        if self.lighter_ws_task is None or self.lighter_ws_task.done():
            self.lighter_ws_task = asyncio.create_task(self.handle_lighter_ws())
            self.logger.info("✅ Lighter WebSocket task started")

    def shutdown(self):
        """Shutdown WebSocket connections."""
        # Close EdgeX WebSocket connections
        if self.edgex_ws_manager:
            try:
                self.edgex_ws_manager.disconnect_all()
                self.logger.info("🔌 EdgeX WebSocket connections disconnected")
            except Exception as e:
                self.logger.error(f"Error disconnecting EdgeX WebSocket: {e}")

        # Cancel Lighter WebSocket task
        if self.lighter_ws_task and not self.lighter_ws_task.done():
            try:
                self.lighter_ws_task.cancel()
                self.logger.info("🔌 Lighter WebSocket task cancelled")
            except Exception as e:
                self.logger.error(f"Error cancelling Lighter WebSocket task: {e}")

        self.stop_flag = True
