"""
Breakout Strategy.

Detects when price breaks above resistance or below support
based on the highest high and lowest low of a lookback period.

Parameters:
  lookback: int    - Candles to look back for high/low (default 20)
  timeframe: str   - Candle timeframe (default "1h")
  volume_filter: bool - Require above-average volume for breakout confirmation
"""
from typing import Optional
from app.strategies.base import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    name = "breakout"
    description = "Price Breakout above resistance / below support"

    def __init__(self, config: dict):
        super().__init__(config)
        self.lookback = int(config.get("lookback", 20))
        self.timeframe = config.get("timeframe", "1h")
        self.volume_filter = bool(config.get("volume_filter", True))
        self._last_signal: Optional[str] = None

    async def run(self) -> Optional[str]:
        df = await self.get_candles_df(self.timeframe, limit=self.lookback + 10)
        if df.empty or len(df) < self.lookback + 2:
            return None

        # Use all but last candle for levels
        window = df.iloc[-(self.lookback + 1):-1]
        resistance = window["high"].max()
        support = window["low"].min()
        avg_volume = window["volume"].mean()

        current = df.iloc[-1]
        curr_close = current["close"]
        curr_volume = current.get("volume", 0)

        volume_ok = (curr_volume >= avg_volume * 1.2) if self.volume_filter else True

        signal = None

        # Bullish breakout
        if curr_close > resistance and volume_ok:
            if self._last_signal != "BUY":
                signal = "BUY"
                self.log(f"Bullish breakout! Close {curr_close:.4f} > Resistance {resistance:.4f}")
                await self.buy()
                self._last_signal = "BUY"

        # Bearish breakdown
        elif curr_close < support and volume_ok:
            if self._last_signal != "SELL":
                signal = "SELL"
                self.log(f"Bearish breakdown! Close {curr_close:.4f} < Support {support:.4f}")
                await self.sell()
                self._last_signal = "SELL"

        return signal
