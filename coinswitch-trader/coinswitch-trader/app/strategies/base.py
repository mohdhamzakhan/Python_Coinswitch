"""
Base Strategy class — all strategies inherit from this.

Provides: candle fetching, price fetching, and spot order placement.
All methods use SPOT trading only (futures removed as requested).

Config dict fields (set via StrategyConfig.parameters + top-level):
    symbol          Trading pair, e.g. "BTC/INR" or "BTC/USDT"
    exchange        "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
    quantity        Base order quantity (default 0.001)
    stop_loss_pct   Stop loss % (default 2.0)
    take_profit_pct Take profit % (default 4.0)

Exchange / quote currency is derived from `exchange`:
    coinswitchx / wazirx  →  INR
    c2c1 / c2c2           →  USDT

If `symbol` is set directly it is validated against the exchange.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from app.services.market_data import market_data
from app.trading.spot import spot_trading, build_symbol, validate_symbol_exchange
from app.risk.manager import risk_manager, RiskViolation
from loguru import logger


class BaseStrategy(ABC):
    """
    Abstract base class for all spot trading strategies.

    Subclasses must implement `run()`.
    """

    name:        str = "base_strategy"
    description: str = ""

    def __init__(self, config: dict):
        # Exchange — drives quote currency selection
        self.exchange = config.get("exchange", "coinswitchx").lower().strip()

        # Symbol — derived from exchange if not explicitly provided
        raw_symbol = config.get("symbol", "")
        if raw_symbol and "/" in raw_symbol:
            # Validate the supplied symbol against the exchange
            try:
                validate_symbol_exchange(raw_symbol.upper(), self.exchange)
                self.symbol = raw_symbol.upper()
            except ValueError:
                # Symbol doesn't match exchange — derive from base coin
                base = raw_symbol.split("/")[0].upper()
                self.symbol = build_symbol(base, self.exchange)
                logger.warning(
                    f"[{self.name}] Symbol {raw_symbol!r} is inconsistent with "
                    f"exchange {self.exchange!r}. Using {self.symbol!r} instead."
                )
        else:
            # No symbol or bare base coin supplied — use BTC as default
            base = (raw_symbol.split("/")[0].upper() or "BTC") if raw_symbol else "BTC"
            self.symbol = build_symbol(base, self.exchange)

        self.quantity        = float(config.get("quantity",        0.001))
        self.stop_loss_pct   = float(config.get("stop_loss_pct",   2.0))
        self.take_profit_pct = float(config.get("take_profit_pct", 4.0))
        self.params          = config

    @abstractmethod
    async def run(self) -> Optional[str]:
        """
        Execute one iteration of the strategy.
        Returns signal: "BUY", "SELL", or None.
        """

    # ── Market data helpers ───────────────────────────────────────────────────

    async def get_candles_df(
        self,
        timeframe: str = "1h",
        limit:     int = 200,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles and return as a DataFrame.

        Columns (normalised from CoinSwitch response):
            timestamp, open, high, low, close, volume
        """
        candles = await market_data.get_candles(
            symbol    = self.symbol,
            timeframe = timeframe,
            limit     = limit,
            exchange  = self.exchange,
        )

        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)

        # CoinSwitch candles use single-letter keys: o h l c v
        col_map = {
            "o": "open",  "h": "high",  "l": "low",
            "c": "close", "v": "volume",
            # start_time / close_time both map to timestamp (prefer start_time)
            "start_time": "timestamp", "close_time": "close_time",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Sort by time
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
            df = df.sort_values("timestamp").reset_index(drop=True)

        return df

    async def get_price(self) -> float:
        """Return the latest traded price for the strategy's symbol."""
        return await market_data.get_price(self.symbol, self.exchange)

    # ── Order helpers (SPOT only) ─────────────────────────────────────────────

    async def buy(
        self,
        quantity: Optional[float] = None,
        price:    Optional[float] = None,
    ) -> dict:
        """
        Place a LIMIT buy order.
        If price is None the current market price is used.
        """
        qty = quantity or self.quantity

        # Get price if not supplied (needed for risk check and order)
        if price is None:
            price = await self.get_price()

        if price <= 0:
            logger.warning(f"[{self.name}] buy() skipped: price is 0")
            return {}

        # Risk check
        try:
            await risk_manager.check_order(
                symbol=self.symbol, side="BUY",
                quantity=qty, price=price,
            )
        except RiskViolation as e:
            logger.warning(f"[{self.name}] Buy blocked by risk: {e}")
            return {}

        # Split symbol to get base coin
        base_coin = self.symbol.split("/")[0]
        return await spot_trading.create_order(
            exchange      = self.exchange,
            base_coin     = base_coin,
            side          = "buy",
            price         = price,
            quantity      = qty,
            symbol        = self.symbol,
            strategy_name = self.name,
        )

    async def sell(
        self,
        quantity: Optional[float] = None,
        price:    Optional[float] = None,
    ) -> dict:
        """
        Place a LIMIT sell order.
        If price is None the current market price is used.
        """
        qty = quantity or self.quantity

        if price is None:
            price = await self.get_price()

        if price <= 0:
            logger.warning(f"[{self.name}] sell() skipped: price is 0")
            return {}

        try:
            await risk_manager.check_order(
                symbol=self.symbol, side="SELL",
                quantity=qty, price=price,
            )
        except RiskViolation as e:
            logger.warning(f"[{self.name}] Sell blocked by risk: {e}")
            return {}

        base_coin = self.symbol.split("/")[0]
        return await spot_trading.create_order(
            exchange      = self.exchange,
            base_coin     = base_coin,
            side          = "sell",
            price         = price,
            quantity      = qty,
            symbol        = self.symbol,
            strategy_name = self.name,
        )

    def log(self, message: str, level: str = "INFO"):
        getattr(logger, level.lower())(f"[{self.name}] {message}")
