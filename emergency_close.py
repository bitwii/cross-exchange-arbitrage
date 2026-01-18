#!/usr/bin/env python3
"""
紧急平仓脚本
用于在程序崩溃后手动平仓
"""
import asyncio
import os
import sys
from decimal import Decimal
import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

async def emergency_close_edgex():
    """紧急平 EdgeX 仓位"""
    print("\n" + "="*60)
    print("EdgeX 紧急平仓")
    print("="*60)

    try:
        from edgex_sdk import Client, OrderSide, GetOrderBookDepthParams

        # 初始化 EdgeX 客户端
        edgex_account_id = os.getenv('EDGEX_ACCOUNT_ID')
        edgex_stark_private_key = os.getenv('EDGEX_STARK_PRIVATE_KEY')
        edgex_base_url = os.getenv('EDGEX_BASE_URL', 'https://pro.edgex.exchange')

        if not edgex_account_id or not edgex_stark_private_key:
            print("❌ EdgeX 配置缺失")
            return

        client = Client(
            account_id=edgex_account_id,
            stark_private_key=edgex_stark_private_key,
            base_url=edgex_base_url
        )

        # 获取合约信息
        contracts = await client.get_contracts()
        eth_contract = None
        for contract in contracts['data']['contractList']:
            if contract['symbol'] == 'ETH-PERP':
                eth_contract = contract
                break

        if not eth_contract:
            print("❌ 未找到 ETH-PERP 合约")
            return

        contract_id = eth_contract['contractId']
        print(f"✅ 合约ID: {contract_id}")

        # 检查持仓
        positions_data = await client.get_account_positions()
        if not positions_data or 'data' not in positions_data:
            print("❌ 无法获取持仓信息")
            return

        positions = positions_data.get('data', {}).get('positionList', [])
        eth_position = None

        for p in positions:
            if isinstance(p, dict) and p.get('contractId') == contract_id:
                eth_position = p
                break

        if not eth_position:
            print("✅ 没有持仓，无需平仓")
            await client.close()
            return

        open_size = Decimal(eth_position.get('openSize', 0))
        print(f"📊 当前持仓: {open_size}")

        if abs(open_size) < Decimal('0.001'):
            print("✅ 持仓量太小，无需平仓")
            await client.close()
            return

        # 确认平仓
        print(f"\n⚠️ 即将平仓 {abs(open_size)} ETH")
        confirm = input("确认平仓？(yes/no): ")
        if confirm.lower() != 'yes':
            print("❌ 取消平仓")
            await client.close()
            return

        # 获取当前市场价格
        depth_params = GetOrderBookDepthParams(contract_id=contract_id, limit=5)
        order_book = await client.quote.get_order_book_depth(depth_params)
        order_book_data = order_book['data'][0]

        bids = order_book_data.get('bids', [])
        asks = order_book_data.get('asks', [])

        best_bid = Decimal(bids[0]['price']) if bids else None
        best_ask = Decimal(asks[0]['price']) if asks else None

        if not best_bid or not best_ask:
            print("❌ 无法获取市场价格")
            await client.close()
            return

        print(f"📊 当前市场价格: bid={best_bid}, ask={best_ask}")

        # 确定平仓方向和价格
        if open_size > 0:
            # 多头持仓，需要卖出平仓
            side = OrderSide.SELL
            close_price = best_bid  # 使用买一价确保成交
            print(f"🔄 平多头仓位: SELL {abs(open_size)} @ {close_price}")
        else:
            # 空头持仓，需要买入平仓
            side = OrderSide.BUY
            close_price = best_ask  # 使用卖一价确保成交
            print(f"🔄 平空头仓位: BUY {abs(open_size)} @ {close_price}")

        # 下单平仓（不使用 post_only，确保成交）
        print("📤 提交平仓订单...")
        order_result = await client.create_limit_order(
            contract_id=contract_id,
            size=str(abs(open_size)),
            price=str(close_price),
            side=side,
            post_only=False  # 不使用 post_only，确保成交
        )

        if order_result and 'data' in order_result:
            order_id = order_result['data'].get('orderId')
            print(f"✅ 平仓订单已提交: {order_id}")
            print("⏳ 等待订单成交...")

            # 等待订单成交
            await asyncio.sleep(3)

            # 再次检查持仓
            positions_data = await client.get_account_positions()
            if positions_data and 'data' in positions_data:
                positions = positions_data.get('data', {}).get('positionList', [])
                for p in positions:
                    if isinstance(p, dict) and p.get('contractId') == contract_id:
                        new_size = Decimal(p.get('openSize', 0))
                        print(f"📊 平仓后持仓: {new_size}")

                        if abs(new_size) < Decimal('0.001'):
                            print("✅ 平仓成功！")
                        else:
                            print(f"⚠️ 警告：仓位未完全平仓，剩余 {new_size}")
                        break
        else:
            print("❌ 平仓订单提交失败")

        await client.close()

    except Exception as e:
        print(f"❌ EdgeX 平仓失败: {e}")
        import traceback
        traceback.print_exc()

async def emergency_close_lighter():
    """紧急平 Lighter 仓位"""
    print("\n" + "="*60)
    print("Lighter 紧急平仓")
    print("="*60)

    try:
        from lighter.signer_client import SignerClient

        # 初始化 Lighter 客户端
        lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX'))
        api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX'))

        client = SignerClient(lighter_base_url, account_index, api_key_index)

        # 获取持仓
        url = f"{lighter_base_url}/api/v1/account"
        headers = {"accept": "application/json"}
        parameters = {"by": "index", "value": account_index}

        response = requests.get(url, headers=headers, params=parameters, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'accounts' not in data or not data['accounts']:
            print("❌ 未找到账户信息")
            return

        positions = data['accounts'][0].get('positions', [])
        eth_position = None

        for position in positions:
            if position.get('symbol') == 'ETH':
                eth_position = position
                break

        if not eth_position:
            print("✅ 没有持仓，无需平仓")
            return

        pos_size = Decimal(eth_position['position']) * eth_position['sign']
        print(f"📊 当前持仓: {pos_size}")

        if abs(pos_size) < Decimal('0.001'):
            print("✅ 持仓量太小，无需平仓")
            return

        # 确认平仓
        print(f"\n⚠️ 即将平仓 {abs(pos_size)} ETH")
        confirm = input("确认平仓？(yes/no): ")
        if confirm.lower() != 'yes':
            print("❌ 取消平仓")
            return

        # 获取市场信息
        markets_url = f"{lighter_base_url}/api/v1/markets"
        markets_response = requests.get(markets_url, headers=headers, timeout=10)
        markets_response.raise_for_status()
        markets_data = markets_response.json()

        eth_market = None
        for market in markets_data.get('markets', []):
            if market.get('symbol') == 'ETH':
                eth_market = market
                break

        if not eth_market:
            print("❌ 未找到 ETH 市场")
            return

        market_index = eth_market['id']
        base_multiplier = 10 ** eth_market['baseDecimals']
        price_multiplier = 10 ** eth_market['priceDecimals']

        # 获取订单簿
        orderbook_url = f"{lighter_base_url}/api/v1/orderbook"
        orderbook_params = {"market_id": market_index}
        orderbook_response = requests.get(orderbook_url, headers=headers, params=orderbook_params, timeout=10)
        orderbook_response.raise_for_status()
        orderbook_data = orderbook_response.json()

        bids = orderbook_data.get('bids', [])
        asks = orderbook_data.get('asks', [])

        if not bids or not asks:
            print("❌ 无法获取订单簿")
            return

        best_bid = Decimal(bids[0]['price'])
        best_ask = Decimal(asks[0]['price'])

        print(f"📊 当前市场价格: bid={best_bid}, ask={best_ask}")

        # 确定平仓方向和价格
        if pos_size > 0:
            # 多头持仓，需要卖出平仓
            is_ask = True
            close_price = best_bid * Decimal('0.985')  # 使用 1.5% 滑点确保成交
            print(f"🔄 平多头仓位: SELL {abs(pos_size)} @ {close_price}")
        else:
            # 空头持仓，需要买入平仓
            is_ask = False
            close_price = best_ask * Decimal('1.015')  # 使用 1.5% 滑点确保成交
            print(f"🔄 平空头仓位: BUY {abs(pos_size)} @ {close_price}")

        # 转换为 Lighter 格式
        raw_quantity = int(abs(pos_size) * base_multiplier)
        raw_price = int(close_price * price_multiplier)
        client_order_id = str(int(asyncio.get_event_loop().time() * 1000))

        # 下单平仓
        print("📤 提交平仓订单...")
        result = await client.create_order(
            market_index,
            raw_price,
            raw_quantity,
            is_ask,
            client_order_id
        )

        print(f"✅ 平仓订单已提交: {result}")
        print("⏳ 等待订单成交...")

        # 等待订单成交
        await asyncio.sleep(3)

        # 再次检查持仓
        response = requests.get(url, headers=headers, params=parameters, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'accounts' in data and data['accounts']:
            positions = data['accounts'][0].get('positions', [])
            for position in positions:
                if position.get('symbol') == 'ETH':
                    new_size = Decimal(position['position']) * position['sign']
                    print(f"📊 平仓后持仓: {new_size}")

                    if abs(new_size) < Decimal('0.001'):
                        print("✅ 平仓成功！")
                    else:
                        print(f"⚠️ 警告：仓位未完全平仓，剩余 {new_size}")
                    break

    except Exception as e:
        print(f"❌ Lighter 平仓失败: {e}")
        import traceback
        traceback.print_exc()

async def main():
    """主函数"""
    print("\n" + "="*60)
    print("紧急平仓工具")
    print("="*60)
    print("\n⚠️ 警告：此脚本将使用市价单平仓，可能产生滑点！")
    print()

    choice = input("选择平仓交易所 (1=EdgeX, 2=Lighter, 3=Both): ")

    if choice == '1':
        await emergency_close_edgex()
    elif choice == '2':
        await emergency_close_lighter()
    elif choice == '3':
        await emergency_close_edgex()
        await emergency_close_lighter()
    else:
        print("❌ 无效选择")

    print("\n" + "="*60)
    print("平仓完成")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
