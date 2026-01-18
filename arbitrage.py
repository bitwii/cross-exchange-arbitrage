import asyncio
import sys
import argparse
from decimal import Decimal
import dotenv

# 引入 EdgeX 策略
from strategy.edgex_arb import EdgexArb

# 引入 StandX 策略
try:
    from strategy.standx_arb import StandxArb
except ImportError:
    StandxArb = None  # 如果文件不存在，防止报错，但在运行时会检查


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Cross-Exchange Arbitrage Bot - Supports EdgeX and StandX',
        formatter_class=argparse.RawDescriptionHelpFormatter
        )

    parser.add_argument('--exchange', type=str, default='edgex',
                        help='Exchange to use (edgex, standx). Default: edgex')
    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Ticker symbol (default: BTC)')
    parser.add_argument('--size', type=str, required=True,
                        help='Number of tokens to buy/sell per order')
    parser.add_argument('--fill-timeout', type=int, default=5,
                        help='Timeout in seconds for maker order fills (default: 5)')
    parser.add_argument('--max-position', type=Decimal, default=Decimal('0'),
                        help='Maximum position to hold (default: 0)')
    parser.add_argument('--long-threshold', type=Decimal, default=Decimal('10'),
                        help='Long threshold for exchange (default: 10). Note: Ignored if USE_DYNAMIC_THRESHOLD=true')
    parser.add_argument('--short-threshold', type=Decimal, default=Decimal('10'),
                        help='Short threshold for exchange (default: 10). Note: Ignored if USE_DYNAMIC_THRESHOLD=true')
    return parser.parse_args()


def validate_exchange(exchange):
    """Validate that the exchange is supported."""
    supported_exchanges = ['edgex', 'standx']
    if exchange.lower() not in supported_exchanges:
        print(f"Error: Unsupported exchange '{exchange}'")
        print(f"Supported exchanges: {', '.join(supported_exchanges)}")
        sys.exit(1)


async def main():
    """Main entry point that creates and runs the cross-exchange arbitrage bot."""
    args = parse_arguments()

    dotenv.load_dotenv()

    # Validate exchange
    validate_exchange(args.exchange)

    bot = None
    exchange_name = args.exchange.lower()

    try:
        common_params = {
            'ticker': args.ticker.upper(),
            'order_quantity': Decimal(args.size),
            'fill_timeout': args.fill_timeout,
            'max_position': args.max_position,
            'long_ex_threshold': Decimal(args.long_threshold),
            'short_ex_threshold': Decimal(args.short_threshold)
        }

        if exchange_name == 'edgex':
            print(f"🚀 Starting EdgeX Arbitrage Bot for {args.ticker}...")
            bot = EdgexArb(**common_params)

        elif exchange_name == 'standx':
            if StandxArb is None:
                print("❌ Error: Could not import StandxArb. Please ensure 'strategy/standx_arb.py' exists.")
                return 1
            print(f"🚀 Starting StandX Arbitrage Bot for {args.ticker}...")
            bot = StandxArb(**common_params)

        # Run the bot
        if bot:
            await bot.run()

    except KeyboardInterrupt:
        print("\n⚠️ Cross-Exchange Arbitrage interrupted by user")
        return 1
    except Exception as e:
        print(f"❌ Error running cross-exchange arbitrage: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
