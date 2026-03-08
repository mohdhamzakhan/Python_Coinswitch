"""
Market Data Service — CoinSwitch spot endpoints.

All methods call the real CoinSwitch API using the correct parameters.
Candle fetching computes start_time/end_time from `limit` + `timeframe`
so callers can still just say get_candles(symbol, "1h", 200).

Timeframe → minutes:
    1m=1  3m=3  5m=5  15m=15  30m=30  1h=60  2h=120
    4h=240  6h=360  12h=720  1d=1440  1w=10080
"""
from __future__ import annotations

import time
from typing import Optional

from app.api.client import get_api_client
from app.trading.spot import spot_trading, VALID_EXCHANGES, quote_for_exchange
from loguru import logger


# ── Timeframe → minutes ───────────────────────────────────────────────────────

_TF_MINUTES: dict[str, int] = {
    "1m": 1,   "3m": 3,   "5m": 5,   "15m": 15,  "30m": 30,
    "1h": 60,  "2h": 120, "4h": 240, "6h": 360,  "12h": 720,
    "1d": 1440, "1w": 10080,
}


def timeframe_to_minutes(timeframe: str) -> int:
    """
    Convert a timeframe string to minutes.
    Falls back to 60 (1h) for unknown values.
    """
    return _TF_MINUTES.get(timeframe.lower(), 60)


def _candle_window(timeframe: str, limit: int) -> tuple[int, int]:
    """
    Return (start_time_ms, end_time_ms) that covers `limit` candles ending now.
    """
    minutes  = timeframe_to_minutes(timeframe)
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - (limit * minutes * 60 * 1000)
    return start_ms, now_ms


# ── MarketDataService ─────────────────────────────────────────────────────────

class MarketDataService:
    """
    Provides market data for spot trading strategies and the backtest engine.

    Exchange selection:
        coinswitchx / wazirx  →  INR pairs
        c2c1 / c2c2           →  USDT pairs

    Default exchange for legacy callers that don't supply one is "coinswitchx"
    (INR). Callers using USDT pairs should pass exchange="c2c1".
    """

    # ── Ticker ────────────────────────────────────────────────────────────────

    async def get_ticker(
        self,
        symbol:   str,
        exchange: str = "coinswitchx",
    ) -> dict:
        """
        GET /trade/api/v2/24hr/ticker

        Returns 24-hour price stats for a symbol.

        Args:
            symbol:   e.g. "BTC/INR" or "BTC/USDT"
            exchange: Comma-separated or single exchange

        Returns:
            {lastPrice, openPrice, highPrice, lowPrice, baseVolume, ...}
        """
        try:
            resp = await spot_trading.get_ticker(
                symbol=symbol.upper(),
                exchanges=exchange.lower(),
            )
            # Response is {symbol: {...}} — return the inner dict
            return resp.get(symbol.upper(), next(iter(resp.values()), {}))
        except Exception as e:
            logger.error(f"get_ticker({symbol}): {e}")
            return {}

    async def get_price(
        self,
        symbol:   str,
        exchange: str = "coinswitchx",
    ) -> float:
        """Return the last traded price for a symbol."""
        ticker = await self.get_ticker(symbol, exchange)
        return float(ticker.get("lastPrice") or ticker.get("price") or 0.0)

    async def get_ticker_all(self, exchange: str = "coinswitchx") -> dict:
        """
        GET /trade/api/v2/24hr/all-pairs/ticker

        Returns tickers for all pairs on the exchange.
        """
        return await spot_trading.get_ticker_all_pairs(exchange)

    # ── Order Book ────────────────────────────────────────────────────────────

    async def get_orderbook(
        self,
        symbol:   str,
        exchange: str = "coinswitchx",
    ) -> dict:
        """
        GET /trade/api/v2/depth

        Returns bids and asks for a symbol.

        Returns:
            {bids: [[price, qty], ...], asks: [[price, qty], ...]}
        """
        return await spot_trading.get_depth(exchange, symbol)

    # ── Candles ───────────────────────────────────────────────────────────────

    async def get_candles(
        self,
        symbol:     str,
        timeframe:  str = "1h",
        limit:      int = 200,
        exchange:   str = "coinswitchx",
        start_time: Optional[int] = None,
        end_time:   Optional[int] = None,
    ) -> list[dict]:
        """
        GET /trade/api/v2/candles

        Returns OHLCV candles.

        The CoinSwitch API requires start_time and end_time in milliseconds.
        When they are not supplied, this method calculates them automatically
        from `limit` × `timeframe` ending at the current time.

        Args:
            symbol:     e.g. "BTC/INR" or "BTC/USDT"
            timeframe:  "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d"
            limit:      Number of candles to fetch (used to compute window)
            exchange:   "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
            start_time: Override start epoch ms
            end_time:   Override end epoch ms

        Returns:
            List of {o, h, l, c, volume, start_time, close_time, interval, symbol}
        """
        interval_minutes = timeframe_to_minutes(timeframe)

        if start_time is None or end_time is None:
            start_time, end_time = _candle_window(timeframe, limit)

        return await spot_trading.get_candles(
            exchange   = exchange,
            symbol     = symbol.upper(),
            interval   = interval_minutes,
            start_time = start_time,
            end_time   = end_time,
        )

    # ── Recent Trades ─────────────────────────────────────────────────────────

    async def get_trades(
        self,
        symbol:   str,
        exchange: str = "coinswitchx",
    ) -> list[dict]:
        """
        GET /trade/api/v2/trades

        Returns recent trade executions for a symbol.
        """
        return await spot_trading.get_trades(exchange, symbol)

    # ── Portfolio / Account ───────────────────────────────────────────────────

    async def get_portfolio(self) -> list[dict]:
        """GET /trade/api/v2/user/portfolio"""
        return await spot_trading.get_portfolio()

    async def get_balance(self, currency: str) -> float:
        """Return available main balance for one currency."""
        return await spot_trading.get_balance(currency)

    # ── Exchange info helpers ─────────────────────────────────────────────────

    async def get_exchange_info(self, exchange: str = "coinswitchx") -> dict:
        """Return active coins and precision for an exchange."""
        coins = await spot_trading.get_active_coins(exchange)
        precision = await spot_trading.get_exchange_precision(exchange)
        return {"coins": coins, "precision": precision, "exchange": exchange}

    async def get_trade_info(self, exchange: str, symbol: str) -> dict:
        """Return min/max order sizes for a symbol."""
        return await spot_trading.get_trade_info(exchange, symbol)

    async def get_trading_fee(self, exchange: str = "coinswitchx") -> dict:
        """Return maker/taker fees for an exchange."""
        return await spot_trading.get_trading_fee(exchange)

    # ── Convenience: best bid/ask ─────────────────────────────────────────────

    async def get_best_price(self, exchange: str, symbol: str) -> dict:
        """Return {bid, ask, mid, spread_pct} from the order book."""
        return await spot_trading.get_best_price(exchange, symbol)


# Module-level singleton
market_data = MarketDataService()
