"""
DCA (Dollar Cost Averaging) Bot Strategy.

Buys a fixed amount at regular intervals, regardless of price.
Optionally uses RSI to time entries (only buy when RSI is low).

Parameters:
  interval_minutes: int  - How often to buy (default 60)
  use_rsi_filter: bool   - Only buy when RSI < rsi_threshold
  rsi_threshold: float   - RSI threshold for buying (default 40)
  max_entries: int       - Max total DCA entries (default 10)
"""
from typing import Optional
from datetime import datetime, timedelta
import pandas as pd
from app.strategies.base import BaseStrategy
from app.strategies.rsi_strategy import _compute_rsi


class DCAStrategy(BaseStrategy):
    name = "dca_bot"
    description = "Dollar Cost Averaging - periodic buy at fixed intervals"

    def __init__(self, config: dict):
        super().__init__(config)
        self.interval_minutes = int(config.get("interval_minutes", 60))
        self.use_rsi_filter = bool(config.get("use_rsi_filter", False))
        self.rsi_threshold = float(config.get("rsi_threshold", 40))
        self.rsi_period = int(config.get("rsi_period", 14))
        self.max_entries = int(config.get("max_entries", 10))
        self._last_buy: Optional[datetime] = None
        self._entry_count = 0

    async def run(self) -> Optional[str]:
        if self._entry_count >= self.max_entries:
            self.log(f"Max entries ({self.max_entries}) reached, DCA complete")
            return None

        # Check interval
        now = datetime.utcnow()
        if self._last_buy:
            elapsed = (now - self._last_buy).total_seconds() / 60
            if elapsed < self.interval_minutes:
                return None

        # RSI filter (optional)
        if self.use_rsi_filter:
            df = await self.get_candles_df("1h", limit=self.rsi_period * 4)
            if not df.empty and len(df) >= self.rsi_period:
                df["rsi"] = _compute_rsi(df["close"], self.rsi_period)
                current_rsi = df["rsi"].iloc[-1]
                if current_rsi > self.rsi_threshold:
                    self.log(f"DCA skipped: RSI {current_rsi:.1f} > threshold {self.rsi_threshold}")
                    return None

        # Place DCA buy
        price = await self.get_price()
        self.log(f"DCA buy #{self._entry_count + 1}/{self.max_entries} @ {price:.4f}")
        order = await self.buy()

        if order:
            self._last_buy = now
            self._entry_count += 1
            return "BUY"

        return None
