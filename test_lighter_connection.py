#!/usr/bin/env python3
"""
Test script to verify Lighter account connection.
"""

import os
import asyncio
from dotenv import load_dotenv
from lighter import SignerClient, ApiClient, Configuration
import lighter

# Load environment variables
load_dotenv()

def print_lighter_client_methods():
    """Print all available methods in Lighter SignerClient."""      
    python3 << 'EOF'    
    from lighter.signer_client import SignerClient
    import inspect

    # 获取所有公开方法
    methods = [m for m in dir(SignerClient) if not m.startswith('_')]

    print("=== All available methods in SignerClient ===")
    for method in sorted(methods):
        print(f"  - {method}")

    print("\n=== Methods containing 'get' ===")
    get_methods = [m for m in methods if 'get' in m.lower()]
    for method in sorted(get_methods):
        print(f"  - {method}")
    EOF



async def test_lighter_connection():
    """Test Lighter account connection and fetch basic info."""

    # Get credentials from environment
    api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY')
    account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
    base_url = "https://mainnet.zklighter.elliot.ai"

    print("=" * 60)
    print("Lighter Account Connection Test")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  Base URL: {base_url}")
    print(f"  Account Index: {account_index}")
    print(f"  API Key Index: {api_key_index}")
    print(f"  Private Key: {'*' * 20}...{api_key_private_key[-4:] if api_key_private_key else 'NOT SET'}")

    if not api_key_private_key:
        print("\n❌ Error: API_KEY_PRIVATE_KEY not set in environment")
        return False

    try:
        # Step 1: Initialize Lighter client
        print("\n" + "-" * 60)
        print("Step 1: Initializing Lighter SignerClient...")
        print("-" * 60)

        # Create api_private_keys dictionary with the index as key
        api_private_keys = {api_key_index: api_key_private_key}

        lighter_client = SignerClient(
            url=base_url,
            account_index=account_index,
            api_private_keys=api_private_keys,
        )

        # Step 2: Check client
        print("\nStep 2: Checking client connection...")
        err = lighter_client.check_client()
        if err is not None:
            print(f"❌ CheckClient error: {err}")
            return False

        print("✅ Client connection successful!")

        # Step 3: Create API client
        print("\n" + "-" * 60)
        print("Step 3: Creating API client...")
        print("-" * 60)

        api_client = ApiClient(configuration=Configuration(host=base_url))

        # Step 4: Test account API - Get account info
        print("\nStep 4: Fetching account information...")
        account_api = lighter.AccountApi(api_client)
        account_data = await account_api.account(by="index", value=str(account_index))

        if account_data and account_data.accounts:
            account = account_data.accounts[0]
            print(f"✅ Account found!")
            print(f"\n  Account Details:")
            # Print available attributes
            print(f"    Account Index: {account_index}")
            if hasattr(account, 'address'):
                print(f"    Address: {account.address}")
            if hasattr(account, 'wallet_address'):
                print(f"    Wallet Address: {account.wallet_address}")

            # Show positions
            if account.positions:
                print(f"\n  Positions ({len(account.positions)}):")
                for pos in account.positions:
                    if abs(float(pos.position)) > 0.0001:
                        print(f"    - Market ID {pos.market_id}: {pos.position} (Avg Price: {pos.avg_price})")
            else:
                print("\n  Positions: None")
        else:
            print("❌ No account data found")
            return False

        # Step 5: Test order API - Get available markets
        print("\n" + "-" * 60)
        print("Step 5: Fetching available markets...")
        print("-" * 60)

        order_api = lighter.OrderApi(api_client)
        order_books = await order_api.order_books()

        if order_books and order_books.order_books:
            print(f"✅ Found {len(order_books.order_books)} available markets:")
            for market in order_books.order_books[:5]:  # Show first 5 markets
                print(f"    - {market.symbol} (Market ID: {market.market_id})")
            if len(order_books.order_books) > 5:
                print(f"    ... and {len(order_books.order_books) - 5} more")
        else:
            print("❌ No markets found")
            return False

        # Step 6: Test authentication token generation
        print("\n" + "-" * 60)
        print("Step 6: Testing authentication token generation...")
        print("-" * 60)

        auth_token, error = lighter_client.create_auth_token_with_expiry()
        if error is not None:
            print(f"❌ Error creating auth token: {error}")
            return False

        print("✅ Authentication token generated successfully!")
        print(f"    Token: {auth_token[:20]}...{auth_token[-20:]}")

        # Step 7: Test getting active orders (if any)
        print("\n" + "-" * 60)
        print("Step 7: Checking for active orders...")
        print("-" * 60)

        # Test with first market ID
        if order_books.order_books:
            test_market_id = order_books.order_books[0].market_id
            orders_response = await order_api.account_active_orders(
                account_index=account_index,
                market_id=test_market_id,
                auth=auth_token
            )

            if orders_response and orders_response.orders:
                print(f"✅ Found {len(orders_response.orders)} active orders for market {test_market_id}")
                for order in orders_response.orders[:3]:  # Show first 3 orders
                    side = "SELL" if order.is_ask else "BUY"
                    print(f"    - Order {order.order_index}: {side} {order.remaining_base_amount} @ {order.price}")
            else:
                print(f"✅ No active orders found for market {test_market_id} (this is normal)")

        # Close API client
        await api_client.close()

        # Final summary
        print("\n" + "=" * 60)
        print("✅ All tests passed! Lighter account is properly configured.")
        print("=" * 60)

        return True

    except Exception as e:
        print(f"\n❌ Error during connection test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    result = asyncio.run(test_lighter_connection())
    exit(0 if result else 1)



