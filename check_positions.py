#!/usr/bin/env python3
"""
检查 EdgeX 和 Lighter 的订单和持仓状态
用于在程序崩溃后手动检查账户状态
"""
import asyncio
import os
import sys
from decimal import Decimal
import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

async def check_edgex_orders_and_positions():
    """检查 EdgeX 的订单和持仓"""
    print("\n" + "="*60)
    print("检查 EdgeX 订单和持仓")
    print("="*60)

    try:
        from edgex_sdk import Client

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
        metadata = await client.get_metadata()
        data = metadata.get('data', {})
        contract_list = data.get('contractList', [])

        eth_contract = None
        for contract in contract_list:
            if contract.get('contractName') == 'ETHUSD':
                eth_contract = contract
                break

        if not eth_contract:
            print("❌ 未找到 ETH-PERP 合约")
            return

        contract_id = eth_contract['contractId']
        print(f"✅ 合约ID: {contract_id}")

        # 检查未完成订单
        print("\n📋 检查未完成订单...")
        # 直接调用 get_orders，不使用 Params 类
        orders_result = await client.get_orders(contract_id=contract_id)

        if orders_result and 'data' in orders_result:
            orders = orders_result['data'].get('orderList', [])
            pending_orders = [o for o in orders if o.get('status') in ['NEW', 'OPEN', 'PENDING', 'PARTIALLY_FILLED']]

            if pending_orders:
                print(f"⚠️ 发现 {len(pending_orders)} 个未完成订单:")
                for order in pending_orders:
                    print(f"  - 订单ID: {order['orderId']}")
                    print(f"    状态: {order['status']}")
                    print(f"    方向: {order['side']}")
                    print(f"    价格: {order['price']}")
                    print(f"    数量: {order['size']}")
                    print(f"    已成交: {order.get('filledSize', 0)}")
                    print(f"    客户端订单ID: {order.get('clientOrderId', 'N/A')}")
                    print()
            else:
                print("✅ 没有未完成订单")

        # 检查持仓
        print("\n📊 检查持仓...")
        positions_data = await client.get_account_positions()

        if positions_data and 'data' in positions_data:
            positions = positions_data.get('data', {}).get('positionList', [])
            eth_position = None

            for p in positions:
                if isinstance(p, dict) and p.get('contractId') == contract_id:
                    eth_position = p
                    break

            if eth_position:
                open_size = Decimal(eth_position.get('openSize', 0))
                avg_entry_price = Decimal(eth_position.get('avgEntryPrice', 0))
                unrealized_pnl = Decimal(eth_position.get('unrealizedPnl', 0))

                print(f"📈 ETH-PERP 持仓:")
                print(f"  - 持仓量: {open_size}")
                print(f"  - 平均开仓价: {avg_entry_price}")
                print(f"  - 未实现盈亏: {unrealized_pnl}")

                if abs(open_size) > Decimal('0.001'):
                    print(f"⚠️ 警告：存在未平仓位！")
            else:
                print("✅ 没有持仓")

        await client.close()

    except Exception as e:
        print(f"❌ 检查 EdgeX 时出错: {e}")
        import traceback
        traceback.print_exc()

def check_lighter_positions():
    """检查 Lighter 的持仓"""
    print("\n" + "="*60)
    print("检查 Lighter 持仓")
    print("="*60)

    try:
        lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX'))

        url = f"{lighter_base_url}/api/v1/account"
        headers = {"accept": "application/json"}
        parameters = {"by": "index", "value": account_index}

        response = requests.get(url, headers=headers, params=parameters, timeout=10)
        response.raise_for_status()

        data = response.json()

        if 'accounts' not in data or not data['accounts']:
            print("❌ 未找到账户信息")
            return

        account = data['accounts'][0]
        positions = account.get('positions', [])

        print(f"✅ 账户地址: {account.get('address', 'N/A')}")
        print(f"✅ 账户索引: {account_index}")

        if positions:
            print(f"\n📊 持仓信息:")
            for position in positions:
                symbol = position.get('symbol')
                pos_size = Decimal(position['position']) * position['sign']

                if symbol == 'ETH':
                    print(f"  - {symbol}: {pos_size}")

                    if abs(pos_size) > Decimal('0.001'):
                        print(f"⚠️ 警告：存在未平仓位！")
        else:
            print("✅ 没有持仓")

        # 检查未完成订单（如果 API 支持）
        print("\n📋 Lighter 订单信息:")
        print("  (注意：Lighter 可能不提供历史订单查询)")

    except Exception as e:
        print(f"❌ 检查 Lighter 时出错: {e}")
        import traceback
        traceback.print_exc()

async def main():
    """主函数"""
    print("\n" + "="*60)
    print("账户状态检查工具")
    print("="*60)

    # 检查 EdgeX
    await check_edgex_orders_and_positions()

    # 检查 Lighter
    check_lighter_positions()

    print("\n" + "="*60)
    print("检查完成")
    print("="*60)
    print("\n如果发现未平仓位或未完成订单，请：")
    print("1. 登录交易所网页界面")
    print("2. 手动取消未完成订单")
    print("3. 手动平仓")
    print()

if __name__ == "__main__":
    asyncio.run(main())
