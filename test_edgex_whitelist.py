#!/usr/bin/env python3
"""
Minimal test script to check if EdgeX account is whitelisted.
"""

import os
import asyncio
from dotenv import load_dotenv
from edgex_sdk import Client

# Load environment variables
load_dotenv()

async def test_whitelist():
    """Test if account is whitelisted on EdgeX."""

    account_id = os.getenv('EDGEX_ACCOUNT_ID')
    stark_private_key = os.getenv('EDGEX_STARK_PRIVATE_KEY')
    base_url = os.getenv('EDGEX_BASE_URL', 'https://pro.edgex.exchange')

    print("=" * 60)
    print("EdgeX Account Whitelist Test")
    print("=" * 60)
    print(f"\nAccount ID: {account_id}")
    print(f"Base URL: {base_url}\n")

    if not account_id or not stark_private_key:
        print("❌ Missing credentials in .env file")
        return False

    try:
        # Initialize client
        client = Client(
            base_url=base_url,
            account_id=account_id,
            stark_private_key=stark_private_key
        )

        print("Testing API access...")

        # Try to get account asset info (requires whitelist)
        result = await client.get_account_asset()

        # If we get here without exception, account is whitelisted
        print("\n✅ SUCCESS: Account is whitelisted!")
        print(f"\nResponse data: {result}")

        await client.close()
        return True

    except Exception as e:
        error_str = str(e)

        # Check if it's a whitelist error
        if 'WHITELIST' in error_str.upper() or 'whitelist' in error_str:
            print("\n❌ FAILED: Account is NOT whitelisted")
            print(f"\nError: {e}")
            print("\n⚠️  You need to contact EdgeX administrator to add your account to the whitelist.")
        else:
            print(f"\n❌ FAILED: {e}")

        return False

if __name__ == "__main__":
    result = asyncio.run(test_whitelist())
    exit(0 if result else 1)
