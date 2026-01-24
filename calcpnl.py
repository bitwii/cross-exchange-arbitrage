import pandas as pd

def analyze_performance():
    print("🚀 开始分析套利机器人运行数据...")
    
    # 1. 加载交易数据
    try:
        # 解析时间戳列
        df = pd.read_csv('edgex_ETH_trades.csv', parse_dates=['timestamp'])
        if df.empty:
            print("⚠️ 警告: 交易文件为空，无数据可分析。")
            return
    except FileNotFoundError:
        print("❌ 错误: 找不到 'edgex_ETH_trades.csv' 文件。")
        return

    # 2. 标准化买卖方向
    # EdgeX 用 'buy'/'sell', Lighter 用 'LONG'/'SHORT' (通常 SHORT=Sell, LONG=Buy)
    # 逻辑: 买入(资金流出), 卖出(资金流入)
    def normalize_side(side):
        side = side.lower()
        if side in ['buy', 'long']:
            return 'BUY'
        elif side in ['sell', 'short']:
            return 'SELL'
        return 'UNKNOWN'

    df['norm_side'] = df['side'].apply(normalize_side)

    # 3. 计算交易量 (Volume)
    total_vol_eth = df['quantity'].sum()
    total_vol_usd = (df['quantity'] * df['price']).sum()

    # 4. 计算现金流 (Cash Flow)
    # BUY: 现金减少 (- price * qty)
    # SELL: 现金增加 (+ price * qty)
    df['cash_flow'] = df.apply(
        lambda x: -1 * x['price'] * x['quantity'] if x['norm_side'] == 'BUY' 
        else x['price'] * x['quantity'], axis=1
    )
    net_cash = df['cash_flow'].sum()

    # 5. 计算净持仓 (Net Position)
    # BUY: 持仓增加 (+ qty)
    # SELL: 持仓减少 (- qty)
    df['pos_change'] = df.apply(
        lambda x: x['quantity'] if x['norm_side'] == 'BUY' 
        else -x['quantity'], axis=1
    )
    net_position = df['pos_change'].sum()

    # 6. 计算盈亏 (PnL)
    # 获取当前市场价格 (Mark Price) 用于评估剩余持仓价值
    try:
        bbo_df = pd.read_csv('edgex_ETH_bbo_data.csv')
        last_price = bbo_df.iloc[-1]['maker_ask'] if not bbo_df.empty else 0
        print(f"ℹ️ 使用最后 BBO 价格估值: ${last_price:.2f}")
    except:
        last_price = df.iloc[-1]['price'] # 降级方案：使用最后一笔交易价格
        print(f"ℹ️ 使用最后成交价格估值: ${last_price:.2f}")

    # 毛利润 = 净现金流 + (净持仓 * 当前市价)
    position_value = net_position * last_price
    gross_pnl = net_cash + position_value

    # 7. 统计日志错误
    error_count = 0
    timeout_count = 0
    try:
        with open('edgex_ETH_log.txt', 'r', encoding='utf-8') as f:
            for line in f:
                if 'Error' in line or 'Exception' in line:
                    error_count += 1
                if 'Timeout' in line:
                    timeout_count += 1
    except:
        print("⚠️ 警告: 无法读取日志文件。")

    # === 输出报告 ===
    print("\n" + "="*30)
    print("       🤖 运行分析报告")
    print("="*30)
    print(f"⏱️  统计时段: {df['timestamp'].min()} 至 {df['timestamp'].max()}")
    print(f"📦 总交易量: {total_vol_eth:.4f} ETH (${total_vol_usd:,.2f})")
    print(f"💰 净现金流: ${net_cash:,.4f}")
    print(f"⚖️ 当前净持仓: {net_position:.4f} ETH (价值: ${position_value:,.2f})")
    print("-" * 30)
    print(f"📈 总盈亏 (Gross PnL): ${gross_pnl:,.4f}")
    print("-" * 30)
    print(f"⚠️ 日志健康度: 错误 {error_count} 次, 超时 {timeout_count} 次")
    print("="*30)

if __name__ == "__main__":
    analyze_performance()