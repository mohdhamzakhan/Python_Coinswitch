"""
Moving Average Crossover Strategy.

Signals:
  BUY  when fast MA crosses above slow MA (golden cross)
  SELL when fast MA crosses below slow MA (death cross)

Parameters:
  fast_period: int  (default 9)
  slow_period: int  (default 21)
  timeframe: str    (default "1h")
"""
import pandas as pd
from typing import Optional
from app.strategies.base import BaseStrategy


class MACrossoverStrategy(BaseStrategy):
    name = "ma_crossover"
    description = "Moving Average Crossover (Golden/Death Cross)"

    def __init__(self, config: dict):
        super().__init__(config)
        self.fast_period = int(config.get("fast_period", 9))
        self.slow_period = int(config.get("slow_period", 21))
        self.timeframe = config.get("timeframe", "1h")
        self._last_signal: Optional[str] = None

    async def run(self) -> Optional[str]:
        """Check for MA crossover and place orders."""
        df = await self.get_candles_df(self.timeframe, limit=max(self.slow_period * 3, 100))
        if df.empty or len(df) < self.slow_period + 2:
            self.log("Not enough candles", "WARNING")
            return None

        df["fast_ma"] = df["close"].rolling(self.fast_period).mean()
        df["slow_ma"] = df["close"].rolling(self.slow_period).mean()

        # Check last two candles for crossover
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        signal = None

        # Golden cross: fast crosses above slow
        if prev["fast_ma"] <= prev["slow_ma"] and curr["fast_ma"] > curr["slow_ma"]:
            if self._last_signal != "BUY":
                signal = "BUY"
                self.log(f"Golden cross: fast={curr['fast_ma']:.4f} > slow={curr['slow_ma']:.4f}")
                await self.buy()
                self._last_signal = "BUY"

        # Death cross: fast crosses below slow
        elif prev["fast_ma"] >= prev["slow_ma"] and curr["fast_ma"] < curr["slow_ma"]:
            if self._last_signal != "SELL":
                signal = "SELL"
                self.log(f"Death cross: fast={curr['fast_ma']:.4f} < slow={curr['slow_ma']:.4f}")
                await self.sell()
                self._last_signal = "SELL"

        return signal
