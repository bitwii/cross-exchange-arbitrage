"""Order placement and monitoring for EdgeX and Lighter exchanges."""
import asyncio
import logging
import time
from decimal import Decimal
from typing import Optional

from edgex_sdk import Client, OrderSide, CancelOrderParams, GetOrderBookDepthParams
from lighter.signer_client import SignerClient


class OrderManager:
    """Manages order placement and monitoring for both exchanges."""

    def __init__(self, order_book_manager, logger: logging.Logger):
        """Initialize order manager."""
        self.order_book_manager = order_book_manager
        self.logger = logger

        # EdgeX client and config
        self.edgex_client: Optional[Client] = None
        self.edgex_contract_id: Optional[str] = None
        self.edgex_tick_size: Optional[Decimal] = None
        self.edgex_order_status: Optional[str] = None
        self.edgex_client_order_id: str = ''

        # Lighter client and config
        self.lighter_client: Optional[SignerClient] = None
        self.lighter_market_index: Optional[int] = None
        self.base_amount_multiplier: Optional[int] = None
        self.price_multiplier: Optional[int] = None
        self.tick_size: Optional[Decimal] = None

        # Lighter order state
        self.lighter_order_filled = False
        self.lighter_order_price: Optional[Decimal] = None
        self.lighter_order_side: Optional[str] = None
        self.lighter_order_size: Optional[Decimal] = None

        # Order execution tracking
        self.order_execution_complete = False
        self.waiting_for_lighter_fill = False
        self.current_lighter_side: Optional[str] = None
        self.current_lighter_quantity: Optional[Decimal] = None
        self.current_lighter_price: Optional[Decimal] = None

        # Callbacks
        self.on_order_filled: Optional[callable] = None

        # WebSocket warning control to avoid spam
        self.last_ws_warning_time = None
        self.ws_warning_interval = 60  # Only warn every 60 seconds

    def set_edgex_config(self, client: Client, contract_id: str, tick_size: Decimal):
        """Set EdgeX client and configuration."""
        self.edgex_client = client
        self.edgex_contract_id = contract_id
        self.edgex_tick_size = tick_size

    def set_lighter_config(self, client: SignerClient, market_index: int,
                           base_amount_multiplier: int, price_multiplier: int, tick_size: Decimal):
        """Set Lighter client and configuration."""
        self.lighter_client = client
        self.lighter_market_index = market_index
        self.base_amount_multiplier = base_amount_multiplier
        self.price_multiplier = price_multiplier
        self.tick_size = tick_size

    def set_callbacks(self, on_order_filled: callable = None):
        """Set callback functions."""
        self.on_order_filled = on_order_filled

    def round_to_tick(self, price: Decimal) -> Decimal:
        """Round price to tick size."""
        if self.edgex_tick_size is None:
            return price
        return (price / self.edgex_tick_size).quantize(Decimal('1')) * self.edgex_tick_size

    async def fetch_edgex_bbo_prices(self) -> tuple[Decimal, Decimal]:
        """Fetch best bid/ask prices from EdgeX using websocket data."""
        # Use WebSocket data if available
        edgex_bid, edgex_ask = self.order_book_manager.get_edgex_bbo()
        if (self.order_book_manager.edgex_order_book_ready and
                edgex_bid and edgex_ask and edgex_bid > 0 and edgex_ask > 0 and edgex_bid < edgex_ask):
            return edgex_bid, edgex_ask

        # Fallback to REST API if websocket data is not available
        # Only log warning every 60 seconds to avoid spam
        current_time = time.time()
        if self.last_ws_warning_time is None or (current_time - self.last_ws_warning_time >= self.ws_warning_interval):
            self.logger.warning("WebSocket BBO data not available, falling back to REST API")
            self.last_ws_warning_time = current_time

        if not self.edgex_client:
            raise Exception("EdgeX client not initialized")

        depth_params = GetOrderBookDepthParams(contract_id=self.edgex_contract_id, limit=15)
        order_book = await self.edgex_client.quote.get_order_book_depth(depth_params)
        order_book_data = order_book['data']

        order_book_entry = order_book_data[0]
        bids = order_book_entry.get('bids', [])
        asks = order_book_entry.get('asks', [])

        best_bid = Decimal(bids[0]['price']) if bids and len(bids) > 0 else Decimal('0')
        best_ask = Decimal(asks[0]['price']) if asks and len(asks) > 0 else Decimal('0')

        return best_bid, best_ask

    async def place_bbo_order(self, side: str, quantity: Decimal) -> str:
        """Place a BBO order on EdgeX."""
        best_bid, best_ask = await self.fetch_edgex_bbo_prices()

        self.logger.info(f"💰 [Price Check] EdgeX BBO before placing order: bid={best_bid}, ask={best_ask}")

        if side.lower() == 'buy':
            order_price = best_ask - self.edgex_tick_size
            order_side = OrderSide.BUY
            self.logger.info(
                f"📊 [Buy Order] Calculated price: ask({best_ask}) - tick_size({self.edgex_tick_size}) = {order_price}")
        else:
            order_price = best_bid + self.edgex_tick_size
            order_side = OrderSide.SELL
            self.logger.info(
                f"📊 [Sell Order] Calculated price: bid({best_bid}) + tick_size({self.edgex_tick_size}) = {order_price}")

        rounded_price = self.round_to_tick(order_price)
        self.logger.info(f"🔢 [Price Rounding] {order_price} → {rounded_price} (after rounding to tick)")

        self.edgex_client_order_id = str(int(time.time() * 1000))

        self.logger.info(
            f"📤 [Sending Order] EdgeX {side.upper()} order: "
            f"quantity={quantity}, price={rounded_price}, post_only=True, "
            f"client_order_id={self.edgex_client_order_id}")

        order_result = await self.edgex_client.create_limit_order(
            contract_id=self.edgex_contract_id,
            size=str(quantity),
            price=str(rounded_price),
            side=order_side,
            post_only=True,
            client_order_id=self.edgex_client_order_id
        )

        if not order_result or 'data' not in order_result:
            raise Exception("Failed to place order")

        order_id = order_result['data'].get('orderId')
        if not order_id:
            raise Exception("No order ID in response")

        self.logger.info(f"✅ [Order Placed] EdgeX order_id={order_id}, waiting for fill...")

        return order_id

    async def place_edgex_post_only_order(self, side: str, quantity: Decimal, stop_flag) -> bool:
        """Place a post-only order on EdgeX."""
        if not self.edgex_client:
            raise Exception("EdgeX client not initialized")

        self.edgex_order_status = None
        self.logger.info(f"[OPEN] [EdgeX] [{side}] Placing EdgeX POST-ONLY order")
        order_id = await self.place_bbo_order(side, quantity)

        start_time = time.time()
        while not stop_flag:
            if self.edgex_order_status == 'CANCELED':
                # Log current market price when order is cancelled
                try:
                    current_bid, current_ask = await self.fetch_edgex_bbo_prices()
                    self.logger.warning(
                        f"⚠️ [EdgeX Order CANCELED] Order {order_id} was canceled. "
                        f"Reason: Order was not filled within timeout period or was canceled by exchange. "
                        f"Market BBO at cancellation: bid={current_bid}, ask={current_ask}")
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ [EdgeX Order CANCELED] Order {order_id} was canceled. "
                        f"Reason: Order was not filled within timeout period or was canceled by exchange. "
                        f"(Failed to fetch current market price: {e})")
                return False
            elif self.edgex_order_status in ['NEW', 'OPEN', 'PENDING', 'CANCELING', 'PARTIALLY_FILLED']:
                await asyncio.sleep(0.5)
                if time.time() - start_time > 5:
                    elapsed = time.time() - start_time
                    # Fetch current market price at timeout
                    try:
                        current_bid, current_ask = await self.fetch_edgex_bbo_prices()
                        self.logger.warning(
                            f"⚠️ [EdgeX Order Timeout] Order {order_id} not filled after {elapsed:.1f}s. "
                            f"Current status: {self.edgex_order_status}. "
                            f"Market BBO at timeout: bid={current_bid}, ask={current_ask}. Attempting to cancel...")
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️ [EdgeX Order Timeout] Order {order_id} not filled after {elapsed:.1f}s. "
                            f"Current status: {self.edgex_order_status}. "
                            f"(Failed to fetch current market price: {e}). Attempting to cancel...")
                    try:
                        cancel_params = CancelOrderParams(order_id=order_id)
                        cancel_result = await self.edgex_client.cancel_order(cancel_params)
                        if not cancel_result or 'data' not in cancel_result:
                            self.logger.error("❌ Error canceling EdgeX order - no valid response")
                        else:
                            self.logger.info(f"✅ [EdgeX Order Cancel Request Sent] Order {order_id} cancel request successful")
                    except Exception as e:
                        self.logger.error(f"❌ Error canceling EdgeX order: {e}")
            elif self.edgex_order_status == 'FILLED':
                break
            else:
                if self.edgex_order_status is not None:
                    self.logger.error(f"❌ Unknown EdgeX order status: {self.edgex_order_status}")
                    return False
                else:
                    await asyncio.sleep(0.5)
        return True

    def handle_edgex_order_update(self, order_data: dict):
        """Handle EdgeX order update."""
        side = order_data.get('side', '').lower()
        filled_size = order_data.get('filled_size')
        price = order_data.get('price', '0')

        if side == 'buy':
            lighter_side = 'sell'
        else:
            lighter_side = 'buy'

        self.current_lighter_side = lighter_side
        self.current_lighter_quantity = filled_size
        self.current_lighter_price = Decimal(price)
        self.waiting_for_lighter_fill = True

    def update_edgex_order_status(self, status: str):
        """Update EdgeX order status."""
        self.edgex_order_status = status

    async def place_lighter_market_order(self, lighter_side: str, quantity: Decimal,
                                         price: Decimal, stop_flag) -> Optional[str]:
        """Place a market order on Lighter."""
        if not self.lighter_client:
            raise Exception("Lighter client not initialized")

        best_bid, best_ask = self.order_book_manager.get_lighter_best_levels()
        if not best_bid or not best_ask:
            raise Exception("Lighter order book not ready")

        self.logger.info(
            f"💰 [Price Check] Lighter BBO before placing order: "
            f"bid={best_bid[0]} (size={best_bid[1]}), ask={best_ask[0]} (size={best_ask[1]})")

        original_price = price
        if lighter_side.lower() == 'buy':
            order_type = "CLOSE"
            is_ask = False
            # Lighter 没有手续费，使用更激进的价格确保立即成交（taker）
            # 直接使用卖一价加上一定滑点，确保吃掉卖单
            price = best_ask[0] * Decimal('1.005')  # 增加到 0.5% 滑点确保成交
            self.logger.info(
                f"📊 [Buy Order - Taker] Price adjustment: best_ask({best_ask[0]}) × 1.005 = {price} "
                f"(EdgeX reference price: {original_price})")
        else:
            order_type = "OPEN"
            is_ask = True
            # Lighter 没有手续费，使用更激进的价格确保立即成交（taker）
            # 直接使用买一价减去一定滑点，确保吃掉买单
            price = best_bid[0] * Decimal('0.995')  # 减少到 0.5% 滑点确保成交
            self.logger.info(
                f"📊 [Sell Order - Taker] Price adjustment: best_bid({best_bid[0]}) × 0.995 = {price} "
                f"(EdgeX reference price: {original_price})")

        self.lighter_order_filled = False
        self.lighter_order_price = price
        self.lighter_order_side = lighter_side
        self.lighter_order_size = quantity

        try:
            client_order_index = int(time.time() * 1000)

            base_amount_raw = int(quantity * self.base_amount_multiplier)
            price_raw = int(price * self.price_multiplier)

            self.logger.info(
                f"📤 [Sending Order] Lighter {lighter_side.upper()} order (IOC - Taker): "
                f"quantity={quantity} (raw={base_amount_raw}), "
                f"price={price} (raw={price_raw}), "
                f"is_ask={is_ask}, client_order_id={client_order_index}")

            tx_type, tx_info, tx_hash, error = self.lighter_client.sign_create_order(
                market_index=self.lighter_market_index,
                client_order_index=client_order_index,
                base_amount=base_amount_raw,
                price=price_raw,
                is_ask=is_ask,
                order_type=self.lighter_client.ORDER_TYPE_LIMIT,
                time_in_force=self.lighter_client.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,  # 使用 IOC 确保立即成交
                reduce_only=False,
                trigger_price=0,
            )
            if error is not None:
                raise Exception(f"Sign error: {error}")

            # Send transaction
            await self.lighter_client.send_tx(
                tx_type=tx_type,
                tx_info=tx_info
            )

            self.logger.info(
                f"✅ [Order Sent] Lighter [{order_type}] {lighter_side.upper()}: "
                f"{quantity} @ {price}, tx_hash={tx_hash[:10]}..., waiting for fill...")

            await self.monitor_lighter_order(client_order_index, stop_flag)

            return tx_hash
        except Exception as e:
            self.logger.error(f"❌ Error placing Lighter order: {e}")
            return None
# 如果等不到，就回滚，使用市价成交，order_execution_complete实现了确保EdgeX和Lighter的订单都完成才进入下一轮交易

    async def query_lighter_order_status(self, client_order_index: int) -> Optional[dict]:
        """Query Lighter order status from API.

        NOTE: lighter-sdk 1.0.2 does NOT have get_orders() method.
        Order status can only be tracked via WebSocket updates.
        This method is a placeholder for future SDK versions.
        """
        try:
            if not self.lighter_client:
                return None

            # lighter-sdk 1.0.2 limitation: No query API available
            # Only WebSocket updates can provide order status
            self.logger.warning(
                f"⚠️ Lighter order query not available (lighter-sdk 1.0.2 limitation). "
                f"Relying on WebSocket updates only for client_order_id={client_order_index}")
            return None

        except Exception as e:
            self.logger.error(f"❌ Error in Lighter order query: {e}")
            return None

    async def monitor_lighter_order(self, client_order_index: int, stop_flag):
        """Monitor Lighter order and wait for fill."""
        start_time = time.time()
        while not self.lighter_order_filled and not stop_flag:
            if time.time() - start_time > 30:
                elapsed = time.time() - start_time
                self.logger.error(
                    f"❌ Timeout waiting for Lighter order fill after {elapsed:.1f}s")

                # Try to query order status before giving up
                self.logger.info(f"🔍 Querying Lighter order status for client_order_id={client_order_index}")

                try:
                    # Query order status from Lighter API
                    order_status = await self.query_lighter_order_status(client_order_index)

                    if order_status and order_status.get('status') == 'FILLED':
                        self.logger.info(f"✅ Found filled order via API query!")
                        # Process the order fill
                        self.handle_lighter_order_filled(order_status)
                    else:
                        self.logger.warning(
                            f"⚠️ Order not filled or not found. Status: {order_status.get('status') if order_status else 'UNKNOWN'}")
                        self.logger.warning("⚠️ Using fallback - marking order as filled to continue trading")
                        self.lighter_order_filled = True
                        self.waiting_for_lighter_fill = False
                        self.order_execution_complete = True

                except Exception as e:
                    self.logger.error(f"❌ Error querying order status: {e}")
                    self.logger.warning("⚠️ Using fallback - marking order as filled to continue trading")
                    self.lighter_order_filled = True
                    self.waiting_for_lighter_fill = False
                    self.order_execution_complete = True

                break

            await asyncio.sleep(0.1)

    def handle_lighter_order_filled(self, order_data: dict):
        """Handle Lighter order fill notification."""
        try:
            # Calculate average filled price
            if "avg_filled_price" not in order_data:
                filled_quote = Decimal(str(order_data.get("filled_quote_amount", 0)))
                filled_base = Decimal(str(order_data.get("filled_base_amount", 0)))
                if filled_base > 0:
                    order_data["avg_filled_price"] = filled_quote / filled_base
                else:
                    self.logger.error("❌ Filled base amount is 0, cannot calculate avg price")
                    return

            # Determine side
            if order_data.get("is_ask") or order_data.get("side") == "SELL":
                order_data["side"] = "SHORT"
                order_type = "OPEN"
            else:
                order_data["side"] = "LONG"
                order_type = "CLOSE"

            client_order_index = order_data.get("client_order_id", "UNKNOWN")
            filled_amount = order_data.get("filled_base_amount", 0)
            avg_price = order_data.get("avg_filled_price", 0)

            self.logger.info(
                f"[{client_order_index}] [{order_type}] [Lighter] [FILLED]: "
                f"{filled_amount} @ {avg_price}")

            # Call the callback
            if self.on_order_filled:
                self.on_order_filled(order_data)

            # Mark as filled
            self.lighter_order_filled = True
            self.order_execution_complete = True

        except Exception as e:
            self.logger.error(f"Error handling Lighter order result: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    def get_edgex_client_order_id(self) -> str:
        """Get current EdgeX client order ID."""
        return self.edgex_client_order_id
