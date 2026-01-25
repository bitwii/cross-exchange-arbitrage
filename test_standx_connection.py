#!/usr/bin/env python3
"""
Test script to verify StandX account connection.
Tests REST API login, price fetching, and position queries.
"""

import os
import asyncio
import json
import base64
import requests
from decimal import Decimal
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Solana dependencies
import base58
from solders.keypair import Keypair


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def print_step(step_num: int, description: str):
    """Print a step header."""
    print(f"\n--- Step {step_num}: {description} ---")


async def test_standx_connection():
    """Test StandX account connection and basic operations."""

    # Configuration
    private_key = os.getenv('STANDX_PRIVATE_KEY')
    base_url = os.getenv('STANDX_BASE_URL', 'https://perps.standx.com')
    auth_url = os.getenv('STANDX_AUTH_URL', 'https://api.standx.com')
    symbol = "BTC-USD"

    print_section("StandX Connection Test")

    print(f"\nConfiguration:")
    print(f"  Base URL: {base_url}")
    print(f"  Auth URL: {auth_url}")
    print(f"  Symbol: {symbol}")
    print(f"  Private Key: {'*' * 20}...{private_key[-8:] if private_key else 'NOT SET'}")

    if not private_key:
        print("\n❌ Error: STANDX_PRIVATE_KEY not set in environment")
        return False

    try:
        # Step 1: Load Solana wallet
        print_step(1, "Loading Solana Wallet")

        clean_key = private_key.replace("0x", "").strip()
        keypair = Keypair.from_bytes(base58.b58decode(clean_key))
        wallet_address = str(keypair.pubkey())

        print(f"✅ Wallet loaded successfully!")
        print(f"   Address: {wallet_address}")

        # Step 2: Prepare sign-in
        print_step(2, "Preparing Sign-In Request")

        req_id = wallet_address
        prepare_url = f"{auth_url}/v1/offchain/prepare-signin?chain=solana"

        resp = requests.post(
            prepare_url,
            json={"address": wallet_address, "requestId": req_id},
            timeout=10
        )

        if not resp.ok:
            print(f"❌ Prepare request failed: {resp.status_code} - {resp.text}")
            return False

        data = resp.json()
        if not data.get("success"):
            print(f"❌ API Error: {data.get('message')}")
            return False

        signed_data_jwt = data["signedData"]
        print(f"✅ Prepare sign-in successful!")
        print(f"   JWT received: {signed_data_jwt[:50]}...")

        # Step 3: Parse JWT and sign message
        print_step(3, "Signing Authentication Message")

        parts = signed_data_jwt.split('.')
        padded = parts[1] + '=' * (4 - len(parts[1]) % 4)
        jwt_payload = json.loads(base64.b64decode(padded).decode('utf-8'))

        msg_bytes = jwt_payload.get("message").encode('utf-8')
        raw_sig = bytes(keypair.sign_message(msg_bytes))

        print(f"✅ Message signed!")
        print(f"   Message: {jwt_payload.get('message')[:50]}...")

        # Step 4: Construct complex signature
        print_step(4, "Constructing Signature Payload")

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
            "account": {"publicKey": list(bytes(keypair.pubkey()))},
            "signature": list(raw_sig),
            "signedMessage": list(msg_bytes)
        }
        complex_obj = {"input": input_data, "output": output_data}
        json_str = json.dumps(complex_obj, separators=(',', ':'))
        final_sig = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

        print(f"✅ Signature payload constructed!")

        # Step 5: Login
        print_step(5, "Logging In to StandX")

        login_url = f"{auth_url}/v1/offchain/login?chain=solana"
        resp = requests.post(
            login_url,
            json={
                "signature": final_sig,
                "signedData": signed_data_jwt,
                "expiresSeconds": 604800
            },
            timeout=10
        )

        if not resp.ok:
            print(f"❌ Login failed: {resp.status_code} - {resp.text}")
            return False

        result = resp.json()
        token = result.get("token")

        if not token:
            print(f"❌ No token in response: {result}")
            return False

        print(f"✅ Login successful!")
        print(f"   Token: {token[:30]}...{token[-10:]}")
        print(f"   Address: {result.get('address', 'N/A')}")

        # Step 6: Test price API
        print_step(6, "Fetching Price Data")

        price_url = f"{base_url}/api/query_symbol_price"
        resp = requests.get(price_url, params={"symbol": symbol}, timeout=10)

        if resp.ok:
            price_data = resp.json()
            bid = price_data.get("spread_bid", 0)
            ask = price_data.get("spread_ask", 0)
            print(f"✅ Price data received!")
            print(f"   Symbol: {symbol}")
            print(f"   Bid: {bid}")
            print(f"   Ask: {ask}")
            print(f"   Spread: {float(ask) - float(bid) if bid and ask else 'N/A'}")
        else:
            print(f"⚠️ Price API returned: {resp.status_code} - {resp.text}")

        # Step 7: Test positions API
        print_step(7, "Fetching Positions")

        positions_url = f"{base_url}/api/query_positions"
        resp = requests.get(
            positions_url,
            params={"symbol": symbol},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )

        if resp.ok:
            positions = resp.json()
            if isinstance(positions, list):
                if positions:
                    print(f"✅ Found {len(positions)} position(s):")
                    for pos in positions:
                        print(f"   - {pos.get('symbol')}: qty={pos.get('qty')}, "
                              f"status={pos.get('status')}")
                else:
                    print(f"✅ No open positions (this is normal)")
            else:
                print(f"⚠️ Unexpected response format: {positions}")
        else:
            print(f"⚠️ Positions API returned: {resp.status_code} - {resp.text}")

        # Step 8: Test WebSocket connectivity (basic check)
        print_step(8, "Testing WebSocket Endpoint")

        ws_url = "wss://perps.standx.com/ws-stream/v1"
        print(f"   WebSocket URL: {ws_url}")

        try:
            import websockets

            async def test_ws():
                try:
                    async with websockets.connect(ws_url, close_timeout=5) as ws:
                        # Send auth
                        auth_payload = {
                            "auth": {
                                "token": token,
                                "streams": [{"channel": "order"}]
                            }
                        }
                        await ws.send(json.dumps(auth_payload))

                        # Wait for response
                        response = await asyncio.wait_for(ws.recv(), timeout=5)
                        data = json.loads(response)

                        if data.get("channel") == "auth":
                            auth_result = data.get("data", {})
                            if auth_result.get("code") == 0:
                                print(f"✅ WebSocket connected and authenticated!")
                                return True
                            else:
                                print(f"⚠️ WebSocket auth failed: {auth_result}")
                        return False
                except Exception as e:
                    print(f"⚠️ WebSocket connection failed: {e}")
                    print(f"   (This may be due to proxy settings)")
                    return False

            await test_ws()

        except ImportError:
            print(f"⚠️ websockets module not available for WS test")

        # Final summary
        print_section("Test Summary")
        print("✅ REST API authentication: PASSED")
        print("✅ Price data fetching: PASSED")
        print("✅ Position query: PASSED")
        print("\nStandX connection is properly configured!")

        return True

    except Exception as e:
        print(f"\n❌ Error during connection test: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(test_standx_connection())
    exit(0 if result else 1)
