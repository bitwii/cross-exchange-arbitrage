"""Dynamic threshold calculator based on historical spread statistics."""
import time
from collections import deque
from decimal import Decimal
from typing import Optional, Tuple
import logging


class DynamicThresholdCalculator:
    """Calculate dynamic trading thresholds based on historical spread data."""

    def __init__(
        self,
        window_size: int = 1000,  # Number of spreads to keep in history
        update_interval: int = 60,  # Update thresholds every 60 seconds
        min_threshold: Decimal = Decimal('1.0'),  # Minimum threshold (safety floor)
        max_threshold: Decimal = Decimal('20.0'),  # Maximum threshold (safety ceiling)
        percentile: float = 0.75,  # Use 75th percentile as threshold
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize dynamic threshold calculator.

        Args:
            window_size: Number of recent spread observations to keep
            update_interval: Seconds between threshold recalculations
            min_threshold: Minimum allowed threshold value
            max_threshold: Maximum allowed threshold value
            percentile: Percentile to use for threshold (0.75 = 75th percentile)
            logger: Logger instance
        """
        self.window_size = window_size
        self.update_interval = update_interval
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        self.percentile = percentile
        self.logger = logger or logging.getLogger(__name__)

        # Historical spread data
        self.long_spreads = deque(maxlen=window_size)  # lighter_bid - edgex_bid
        self.short_spreads = deque(maxlen=window_size)  # edgex_ask - lighter_ask

        # Current thresholds
        self.long_threshold = min_threshold
        self.short_threshold = min_threshold

        # Statistics
        self.last_update_time = time.time()
        self.long_mean = Decimal('0')
        self.long_std = Decimal('0')
        self.short_mean = Decimal('0')
        self.short_std = Decimal('0')

    def add_spread_observation(self, long_spread: Decimal, short_spread: Decimal) -> None:
        """
        Add a new spread observation to the history.

        Args:
            long_spread: Current long spread (lighter_bid - edgex_bid)
            short_spread: Current short spread (edgex_ask - lighter_ask)
        """
        self.long_spreads.append(long_spread)
        self.short_spreads.append(short_spread)

        # Check if we should update thresholds
        current_time = time.time()
        if current_time - self.last_update_time >= self.update_interval:
            self._update_thresholds()
            self.last_update_time = current_time

    def _update_thresholds(self) -> None:
        """Recalculate thresholds based on current spread history."""
        if len(self.long_spreads) < 100 or len(self.short_spreads) < 100:
            # Not enough data yet, use minimum threshold
            self.logger.info(
                f"📊 [Dynamic Threshold] Insufficient data: "
                f"long={len(self.long_spreads)}, short={len(self.short_spreads)} samples. "
                f"Using minimum thresholds."
            )
            return

        # Calculate statistics for long spreads
        long_sorted = sorted(self.long_spreads)
        long_percentile_idx = int(len(long_sorted) * self.percentile)
        new_long_threshold = long_sorted[long_percentile_idx]

        # Calculate mean and std for logging
        self.long_mean = sum(self.long_spreads, Decimal('0')) / len(self.long_spreads)
        long_variance = sum((x - self.long_mean) ** 2 for x in self.long_spreads) / len(self.long_spreads)
        self.long_std = long_variance.sqrt() if long_variance > 0 else Decimal('0')

        # Calculate statistics for short spreads
        short_sorted = sorted(self.short_spreads)
        short_percentile_idx = int(len(short_sorted) * self.percentile)
        new_short_threshold = short_sorted[short_percentile_idx]

        # Calculate mean and std for logging
        self.short_mean = sum(self.short_spreads, Decimal('0')) / len(self.short_spreads)
        short_variance = sum((x - self.short_mean) ** 2 for x in self.short_spreads) / len(self.short_spreads)
        self.short_std = short_variance.sqrt() if short_variance > 0 else Decimal('0')

        # Apply safety bounds
        new_long_threshold = max(self.min_threshold, min(self.max_threshold, new_long_threshold))
        new_short_threshold = max(self.min_threshold, min(self.max_threshold, new_short_threshold))

        # Log threshold changes
        if new_long_threshold != self.long_threshold or new_short_threshold != self.short_threshold:
            self.logger.info(
                f"📊 [Dynamic Threshold Update] "
                f"Long: {self.long_threshold:.2f} → {new_long_threshold:.2f} "
                f"(mean={self.long_mean:.2f}, std={self.long_std:.2f}, {self.percentile*100:.0f}th percentile) | "
                f"Short: {self.short_threshold:.2f} → {new_short_threshold:.2f} "
                f"(mean={self.short_mean:.2f}, std={self.short_std:.2f}, {self.percentile*100:.0f}th percentile) | "
                f"Samples: {len(self.long_spreads)}"
            )

        self.long_threshold = new_long_threshold
        self.short_threshold = new_short_threshold

    def get_thresholds(self) -> Tuple[Decimal, Decimal]:
        """
        Get current dynamic thresholds.

        Returns:
            Tuple of (long_threshold, short_threshold)
        """
        return self.long_threshold, self.short_threshold

    def get_statistics(self) -> dict:
        """
        Get current spread statistics.

        Returns:
            Dictionary with statistics
        """
        return {
            'long_threshold': float(self.long_threshold),
            'short_threshold': float(self.short_threshold),
            'long_mean': float(self.long_mean),
            'long_std': float(self.long_std),
            'short_mean': float(self.short_mean),
            'short_std': float(self.short_std),
            'sample_count': len(self.long_spreads),
            'window_size': self.window_size,
            'percentile': self.percentile
        }

    def force_update(self) -> None:
        """Force immediate threshold update regardless of interval."""
        self._update_thresholds()
        self.last_update_time = time.time()
