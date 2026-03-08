"""
RSI (Relative Strength Index) Strategy.

Signals:
  BUY  when RSI crosses above oversold threshold (default 30)
  SELL when RSI crosses below overbought threshold (default 70)

Parameters:
  rsi_period: int   (default 14)
  oversold: float   (default 30)
  overbought: float (default 70)
  timeframe: str    (default "1h")
"""
from typing import Optional
import pandas as pd
from app.strategies.base import BaseStrategy


def _compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


class RSIStrategy(BaseStrategy):
    name = "rsi_strategy"
    description = "RSI Oversold/Overbought Strategy"

    def __init__(self, config: dict):
        super().__init__(config)
        self.rsi_period = int(config.get("rsi_period", 14))
        self.oversold = float(config.get("oversold", 30))
        self.overbought = float(config.get("overbought", 70))
        self.timeframe = config.get("timeframe", "1h")
        self._last_signal: Optional[str] = None

    async def run(self) -> Optional[str]:
        df = await self.get_candles_df(self.timeframe, limit=self.rsi_period * 5)
        if df.empty or len(df) < self.rsi_period + 2:
            self.log("Not enough candles", "WARNING")
            return None

        df["rsi"] = _compute_rsi(df["close"], self.rsi_period)

        prev_rsi = df["rsi"].iloc[-2]
        curr_rsi = df["rsi"].iloc[-1]
        signal = None

        self.log(f"RSI: prev={prev_rsi:.2f}, curr={curr_rsi:.2f}")

        # Buy when RSI crosses above oversold
        if prev_rsi <= self.oversold and curr_rsi > self.oversold:
            if self._last_signal != "BUY":
                signal = "BUY"
                self.log(f"RSI crossed above oversold ({self.oversold}): {curr_rsi:.2f}")
                await self.buy()
                self._last_signal = "BUY"

        # Sell when RSI crosses below overbought
        elif prev_rsi >= self.overbought and curr_rsi < self.overbought:
            if self._last_signal != "SELL":
                signal = "SELL"
                self.log(f"RSI crossed below overbought ({self.overbought}): {curr_rsi:.2f}")
                await self.sell()
                self._last_signal = "SELL"

        return signal
