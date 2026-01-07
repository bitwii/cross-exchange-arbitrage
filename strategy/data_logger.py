"""Data logging module for trade and BBO data."""
import csv
import json
import os
import logging
from decimal import Decimal
from datetime import datetime
import pytz


class DataLogger:
    """Handles CSV and JSON logging for trades and BBO data."""

    def __init__(self, exchange: str, ticker: str, logger: logging.Logger):
        """Initialize data logger with file paths."""
        self.exchange = exchange
        self.ticker = ticker
        self.logger = logger
        os.makedirs("logs", exist_ok=True)

        self.csv_filename = f"logs/{exchange}_{ticker}_trades.csv"
        self.bbo_csv_filename = f"logs/{exchange}_{ticker}_bbo_data.csv"
        self.thresholds_json_filename = f"logs/{exchange}_{ticker}_thresholds.json"

        # CSV file handles for efficient writing (kept open)
        self.bbo_csv_file = None
        self.bbo_csv_writer = None
        self.bbo_write_counter = 0
        self.bbo_flush_interval = 100  # Flush every 100 rows
        self.bbo_flush_timeout = 60    # Or flush every 60 seconds
        self.last_bbo_flush_time = None  # Initialize on first write

        # Trade CSV file handles for efficient writing (kept open)
        self.trade_csv_file = None
        self.trade_csv_writer = None
        self.trade_write_counter = 0
        self.trade_flush_interval = 1  # Flush immediately after each trade (changed from 10)
        self.last_trade_flush_time = None  # Initialize on first write

        self._initialize_trade_csv_file()
        self._initialize_bbo_csv_file()

    def _initialize_trade_csv_file(self):
        """Initialize trade CSV file with headers if it doesn't exist."""
        file_exists = os.path.exists(self.csv_filename)

        # Open file in append mode with 8KB buffer
        self.trade_csv_file = open(self.csv_filename, 'a', newline='', buffering=8192)
        self.trade_csv_writer = csv.writer(self.trade_csv_file)

        # Write header only if file is new
        if not file_exists:
            self.trade_csv_writer.writerow(['exchange', 'timestamp', 'side', 'price', 'quantity'])
            self.trade_csv_file.flush()  # Ensure header is written immediately

    def _initialize_bbo_csv_file(self):
        """Initialize BBO CSV file with headers if it doesn't exist."""
        file_exists = os.path.exists(self.bbo_csv_filename)

        # Open file in append mode (will create if doesn't exist)
        self.bbo_csv_file = open(self.bbo_csv_filename, 'a', newline='', buffering=8192)  # 8KB buffer
        self.bbo_csv_writer = csv.writer(self.bbo_csv_file)

        # Write header only if file is new
        if not file_exists:
            self.bbo_csv_writer.writerow([
                'timestamp',
                'maker_bid',
                'maker_ask',
                'lighter_bid',
                'lighter_ask',
                'long_maker_spread',
                'short_maker_spread',
                'long_maker',
                'short_maker',
                'long_maker_threshold',
                'short_maker_threshold'
            ])
            self.bbo_csv_file.flush()  # Ensure header is written immediately

    def log_trade_to_csv(self, exchange: str, side: str, price: str, quantity: str):
        """Log trade details to CSV file."""
        import time

        if not self.trade_csv_file or not self.trade_csv_writer:
            # Fallback: reinitialize if file handle is lost
            self._initialize_trade_csv_file()

        timestamp = datetime.now(pytz.UTC).isoformat()

        try:
            self.trade_csv_writer.writerow([exchange, timestamp, side, price, quantity])
            self.trade_write_counter += 1

            # Initialize timestamp on first write
            if self.last_trade_flush_time is None:
                self.last_trade_flush_time = time.time()

            # Flush every N trades or every 30 seconds
            current_time = time.time()
            if (self.trade_write_counter >= self.trade_flush_interval or
                (current_time - self.last_trade_flush_time) >= 30):
                self.trade_csv_file.flush()
                self.trade_write_counter = 0
                self.last_trade_flush_time = current_time
                self.logger.info(f"💾 Trade CSV flushed to disk")

            self.logger.info(f"📊 Trade logged to CSV: {exchange} {side} {quantity} @ {price}")
        except Exception as e:
            self.logger.error(f"Error writing trade to CSV: {e}")
            # Try to reinitialize on error
            try:
                if self.trade_csv_file:
                    self.trade_csv_file.close()
            except Exception:
                pass
            self._initialize_trade_csv_file()


    def _get_log_timestamp(self):
        """
        生成精简格式的时间戳: YYMMDDT HH:MM:SS.msTZ
        示例: 260103T11:58:56.12+00
        """
        now = datetime.now(pytz.UTC)

        # %y: 两位年份, %m%d: 月日, T: 分隔符, %H:%M:%S: 时分秒
        main_part = now.strftime("%y%m%dT%H:%M:%S")

        # %f 是 6 位微秒，取前两位变成 10 毫秒精度
        ms = now.strftime("%f")[:2]

        # %z 是 +0000 格式，取前三位变成 +00
        tz = now.strftime("%z")[:3]

        return f"{main_part}.{ms}{tz}"


    def log_bbo_to_csv(self, maker_bid: Decimal, maker_ask: Decimal, lighter_bid: Decimal,
                       lighter_ask: Decimal, long_maker: bool, short_maker: bool,
                       long_maker_threshold: Decimal, short_maker_threshold: Decimal):
        """Log BBO data to CSV file using buffered writes."""
        if not self.bbo_csv_file or not self.bbo_csv_writer:
            # Fallback: reinitialize if file handle is lost
            self._initialize_bbo_csv_file()

        timestamp = self._get_log_timestamp()

        # Calculate spreads
        long_maker_spread = (lighter_bid - maker_bid
                             if lighter_bid and lighter_bid > 0 and maker_bid > 0
                             else Decimal('0'))
        short_maker_spread = (maker_ask - lighter_ask
                              if maker_ask > 0 and lighter_ask and lighter_ask > 0
                              else Decimal('0'))

        try:
            self.bbo_csv_writer.writerow([
                timestamp,
                float(maker_bid),
                float(maker_ask),
                float(lighter_bid) if lighter_bid and lighter_bid > 0 else 0.0,
                float(lighter_ask) if lighter_ask and lighter_ask > 0 else 0.0,
                float(long_maker_spread),
                float(short_maker_spread),
                long_maker,
                short_maker,
                float(long_maker_threshold),
                float(short_maker_threshold)
            ])

            # Increment counter and flush periodically
            self.bbo_write_counter += 1

            # Initialize timestamp on first write
            if self.last_bbo_flush_time is None:
                import time
                self.last_bbo_flush_time = time.time()

            # Flush based on row count or time interval
            import time
            current_time = time.time()
            should_flush = (
                self.bbo_write_counter >= self.bbo_flush_interval or
                (current_time - self.last_bbo_flush_time) >= self.bbo_flush_timeout
            )

            if should_flush:
                self.bbo_csv_file.flush()
                self.bbo_write_counter = 0
                self.last_bbo_flush_time = current_time
        except Exception as e:
            self.logger.error(f"Error writing to BBO CSV: {e}")
            # Try to reinitialize on error
            try:
                if self.bbo_csv_file:
                    self.bbo_csv_file.close()
            except Exception:
                pass
            self._initialize_bbo_csv_file()

    def close(self):
        """Close file handles."""
        # Close BBO CSV file
        if self.bbo_csv_file:
            try:
                self.bbo_csv_file.flush()
                self.bbo_csv_file.close()
                self.bbo_csv_file = None
                self.bbo_csv_writer = None
                self.logger.info("📊 BBO CSV file closed")
            except (ValueError, OSError) as e:
                # File already closed or I/O error - ignore silently
                self.bbo_csv_file = None
                self.bbo_csv_writer = None
            except Exception as e:
                self.logger.error(f"Error closing BBO CSV file: {e}")
                self.bbo_csv_file = None
                self.bbo_csv_writer = None

        # Close trade CSV file
        if self.trade_csv_file:
            try:
                self.trade_csv_file.flush()
                self.trade_csv_file.close()
                self.trade_csv_file = None
                self.trade_csv_writer = None
                self.logger.info("📊 Trade CSV file closed")
            except (ValueError, OSError) as e:
                # File already closed or I/O error - ignore silently
                self.trade_csv_file = None
                self.trade_csv_writer = None
            except Exception as e:
                self.logger.error(f"Error closing trade CSV file: {e}")
                self.trade_csv_file = None
                self.trade_csv_writer = None
