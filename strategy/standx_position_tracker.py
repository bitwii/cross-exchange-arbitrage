"""Position tracking for StandX and Lighter exchanges."""
import asyncio
import json
import logging
import requests
import sys
from decimal import Decimal


class StandXPositionTracker:
    """Tracks positions on StandX and Lighter exchanges."""

    def __init__(self, ticker: str, standx_client, standx_symbol: str,
                 lighter_base_url: str, account_index: int, logger: logging.Logger):
        """Initialize position tracker."""
        self.ticker = ticker
        self.standx_client = standx_client
        self.standx_symbol = standx_symbol
        self.lighter_base_url = lighter_base_url
        self.account_index = account_index
        self.logger = logger

        self.standx_position = Decimal('0')
        self.lighter_position = Decimal('0')

    async def get_standx_position(self) -> Decimal:
        """Get StandX position."""
        if not self.standx_client:
            raise Exception("StandX client not initialized")

        positions_data = await self.standx_client.get_account_positions()
        if not positions_data:
            self.logger.warning("No positions or failed to get positions")
            return Decimal('0')

        # StandX returns position directly as Decimal
        if isinstance(positions_data, Decimal):
            return positions_data
        return Decimal('0')

    async def get_lighter_position(self) -> Decimal:
        """Get Lighter position."""
        url = f"{self.lighter_base_url}/api/v1/account"
        headers = {"accept": "application/json"}
        parameters = {"by": "index", "value": self.account_index}

        current_position = None
        attempts = 0
        while current_position is None and attempts < 10:
            try:
                response = requests.get(url, headers=headers, params=parameters, timeout=10)
                response.raise_for_status()

                if not response.text.strip():
                    self.logger.warning("⚠️ Empty response from Lighter API")
                    return self.lighter_position

                data = response.json()
                if 'accounts' not in data or not data['accounts']:
                    self.logger.warning(f"⚠️ Unexpected response: {data}")
                    return self.lighter_position

                positions = data['accounts'][0].get('positions', [])
                for position in positions:
                    if position.get('symbol') == self.ticker:
                        current_position = Decimal(position['position']) * position['sign']
                        break
                if current_position is None:
                    current_position = 0

            except requests.exceptions.RequestException as e:
                self.logger.warning(f"⚠️ Network error: {e}")
            except json.JSONDecodeError as e:
                self.logger.warning(f"⚠️ JSON error: {e}")
            except Exception as e:
                self.logger.warning(f"⚠️ Error: {e}")
            finally:
                attempts += 1
                await asyncio.sleep(1)

        if current_position is None:
            self.logger.error(f"❌ Failed to get Lighter position after {attempts} attempts")
            sys.exit(1)

        return current_position

    def update_standx_position(self, delta: Decimal):
        """Update StandX position by delta."""
        self.standx_position += delta

    def update_lighter_position(self, delta: Decimal):
        """Update Lighter position by delta."""
        self.lighter_position += delta

    def get_current_standx_position(self) -> Decimal:
        """Get current StandX position (cached)."""
        return self.standx_position

    def get_current_lighter_position(self) -> Decimal:
        """Get current Lighter position (cached)."""
        return self.lighter_position

    def get_net_position(self) -> Decimal:
        """Get net position across both exchanges."""
        return self.standx_position + self.lighter_position
