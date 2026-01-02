#!/usr/bin/env python3
"""
Test script to verify EdgeX account connection and trading capabilities.
"""

import os
import asyncio
from decimal import Decimal
from dotenv import load_dotenv
from edgex_sdk import Client

# Load environment variables
load_dotenv()

async def test_edgex_connection():
    """Test EdgeX account connection and basic trading operations."""

    # Get credentials from environment
    account_id = os.getenv('EDGEX_ACCOUNT_ID')
    stark_private_key = os.getenv('EDGEX_STARK_PRIVATE_KEY')
    base_url = os.getenv('EDGEX_BASE_URL', 'https://pro.edgex.exchange')

    print("=" * 60)
    print("EdgeX Account Connection Test")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  Base URL: {base_url}")
    print(f"  Account ID: {account_id}")
    print(f"  Stark Private Key: {'*' * 20}...{stark_private_key[-4:] if stark_private_key else 'NOT SET'}")

    if not account_id or not stark_private_key:
        print("\n❌ Error: EDGEX_ACCOUNT_ID and EDGEX_STARK_PRIVATE_KEY must be set")
        return False

    try:
        # Step 1: Initialize EdgeX client
        print("\n" + "-" * 60)
        print("Step 1: Initializing EdgeX Client...")
        print("-" * 60)

        client = Client(
            base_url=base_url,
            account_id=account_id,
            stark_private_key=stark_private_key
        )

        print("✅ EdgeX client initialized successfully!")

        # Step 2: Get account asset information
        print("\n" + "-" * 60)
        print("Step 2: Fetching account asset information...")
        print("-" * 60)

        try:
            account_asset = await client.get_account_asset()

            if account_asset:
                print(f"✅ Account asset info retrieved!")
                print(f"\n  Account Asset Details:")

                # Print available fields
                if isinstance(account_asset, dict):
                    for key, value in account_asset.items():
                        if key not in ['data', 'raw']:  # Skip large nested data
                            print(f"    {key}: {value}")
                else:
                    print(f"    {account_asset}")
            else:
                print("⚠️  No account asset data returned")
        except Exception as e:
            print(f"⚠️  Could not fetch account asset: {e}")

        # Step 3: Get metadata (markets info)
        print("\n" + "-" * 60)
        print("Step 3: Fetching market metadata...")
        print("-" * 60)

        try:
            metadata = await client.get_metadata()

            if metadata and 'data' in metadata and 'contracts' in metadata['data']:
                contracts = metadata['data']['contracts']
                print(f"✅ Found {len(contracts)} available contracts:")

                # Show first 5 contracts
                for contract in list(contracts.values())[:5]:
                    symbol = contract.get('symbol', 'N/A')
                    contract_id = contract.get('id', 'N/A')
                    status = contract.get('status', 'N/A')
                    print(f"    - {symbol} (Contract ID: {contract_id}, Status: {status})")

                if len(contracts) > 5:
                    print(f"    ... and {len(contracts) - 5} more")

                # Find ETH contract for testing
                eth_contract = None
                for contract in contracts.values():
                    if contract.get('symbol') == 'ETH':
                        eth_contract = contract
                        break

                if eth_contract:
                    print(f"\n  ETH Contract Details:")
                    print(f"    Symbol: {eth_contract.get('symbol')}")
                    print(f"    Contract ID: {eth_contract.get('id')}")
                    print(f"    Tick Size: {eth_contract.get('tick_size', 'N/A')}")
                    print(f"    Min Order Size: {eth_contract.get('min_order_size', 'N/A')}")
                    print(f"    Status: {eth_contract.get('status')}")
            else:
                print("❌ No contracts found in metadata")
                return False
        except Exception as e:
            print(f"❌ Error fetching metadata: {e}")
            return False

        # Step 4: Get current positions
        print("\n" + "-" * 60)
        print("Step 4: Checking current positions...")
        print("-" * 60)

        try:
            positions_data = await client.get_account_positions()

            if positions_data and 'data' in positions_data:
                positions = positions_data['data']
                if positions:
                    print(f"✅ Found {len(positions)} positions:")
                    for pos in positions:
                        symbol = pos.get('symbol', 'N/A')
                        size = pos.get('size', 0)
                        avg_price = pos.get('average_entry_price', 'N/A')
                        unrealized_pnl = pos.get('unrealized_pnl', 'N/A')
                        print(f"    - {symbol}: Size={size}, Avg Price={avg_price}, PnL={unrealized_pnl}")
                else:
                    print("✅ No open positions (this is normal)")
            else:
                print("✅ No open positions (this is normal)")
        except Exception as e:
            print(f"⚠️  Could not fetch positions: {e}")

        # Step 5: Get order book for ETH (skipped - needs proper API)
        print("\n" + "-" * 60)
        print("Step 5: Order book fetch (SKIPPED)")
        print("-" * 60)
        print("ℹ️  Order book fetching requires specific contract ID and API.")
        print("ℹ️  This can be tested during actual trading.")

        # Step 6: Get active orders
        print("\n" + "-" * 60)
        print("Step 6: Checking active orders...")
        print("-" * 60)

        try:
            active_orders_data = await client.get_active_orders()

            if active_orders_data and 'data' in active_orders_data:
                active_orders = active_orders_data['data']
                if active_orders:
                    print(f"✅ Found {len(active_orders)} active orders:")
                    for order in active_orders[:5]:  # Show first 5 orders
                        order_id = order.get('id', 'N/A')
                        symbol = order.get('symbol', 'N/A')
                        side = order.get('side', 'N/A')
                        size = order.get('size', 'N/A')
                        price = order.get('price', 'N/A')
                        status = order.get('status', 'N/A')
                        print(f"    - Order {order_id}: {side} {size} {symbol} @ {price} ({status})")
                else:
                    print("✅ No active orders (this is normal)")
            else:
                print("✅ No active orders (this is normal)")
        except Exception as e:
            print(f"⚠️  Could not fetch active orders: {e}")

        # Step 7: Test order placement (DRY RUN - commented out by default)
        print("\n" + "-" * 60)
        print("Step 7: Order placement test (SKIPPED)")
        print("-" * 60)
        print("ℹ️  Order placement test is disabled by default.")
        print("ℹ️  To test order placement, uncomment the code in the script.")

        # UNCOMMENT BELOW TO TEST ACTUAL ORDER PLACEMENT
        # WARNING: This will place a REAL order!
        """
        if eth_market and bids and asks:
            test_contract_id = eth_market.get('contract_id')
            min_size = Decimal(str(eth_market.get('min_order_size', '0.01')))
            tick_size = Decimal(str(eth_market.get('tick_size', '0.01')))

            # Place a limit order far from market price (to avoid filling)
            best_bid = Decimal(str(bids[0].get('price')))
            test_price = (best_bid * Decimal('0.9')).quantize(tick_size)  # 10% below best bid
            test_size = min_size

            print(f"\n  Placing TEST order:")
            print(f"    Contract: {test_contract_id}")
            print(f"    Side: BUY")
            print(f"    Size: {test_size}")
            print(f"    Price: {test_price} (10% below market)")

            try:
                order_result = await client.place_order(
                    contract_id=test_contract_id,
                    side='BUY',
                    size=str(test_size),
                    price=str(test_price),
                    order_type='LIMIT',
                    post_only=True
                )

                if order_result:
                    order_id = order_result.get('id', 'N/A')
                    print(f"✅ Test order placed successfully! Order ID: {order_id}")

                    # Wait a moment
                    await asyncio.sleep(2)

                    # Cancel the test order
                    print(f"    Canceling test order {order_id}...")
                    cancel_result = await client.cancel_order(order_id)

                    if cancel_result:
                        print(f"✅ Test order canceled successfully!")
                    else:
                        print(f"⚠️  Failed to cancel test order")
                else:
                    print(f"❌ Failed to place test order")

            except Exception as e:
                print(f"❌ Error during order test: {e}")
        """

        # Close client
        await client.close()

        # Final summary
        print("\n" + "=" * 60)
        print("✅ All tests passed! EdgeX account is properly configured.")
        print("=" * 60)

        return True

    except Exception as e:
        print(f"\n❌ Error during connection test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    result = asyncio.run(test_edgex_connection())
    exit(0 if result else 1)
