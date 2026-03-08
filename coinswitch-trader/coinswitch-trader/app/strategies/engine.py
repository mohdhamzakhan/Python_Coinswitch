"""
Strategy Engine.
Manages all trading strategies, loads configs from DB, and runs them on schedule.
"""
from typing import Dict, Type, Optional
from datetime import datetime
from sqlalchemy import select
from app.models.db_models import StrategyConfig, TradeLog
from app.database.session import get_db_context
from app.strategies.base import BaseStrategy
from app.strategies.ma_crossover import MACrossoverStrategy
from app.strategies.rsi_strategy import RSIStrategy
from app.strategies.grid_strategy import GridStrategy
from app.strategies.dca_strategy import DCAStrategy
from app.strategies.breakout_strategy import BreakoutStrategy
from app.risk.manager import risk_manager
from loguru import logger


# Registry of all available strategies
STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "ma_crossover": MACrossoverStrategy,
    "rsi_strategy": RSIStrategy,
    "grid_trading": GridStrategy,
    "dca_bot": DCAStrategy,
    "breakout": BreakoutStrategy,
}


class StrategyEngine:
    """
    Loads strategy configs from DB, instantiates strategy objects,
    and runs them in sequence. Handles errors gracefully.
    """

    def __init__(self):
        self._strategies: Dict[str, BaseStrategy] = {}

    async def load_strategies(self):
        """Load and instantiate enabled strategies from DB."""
        async with get_db_context() as db:
            result = await db.execute(
                select(StrategyConfig).where(StrategyConfig.is_enabled == True)
            )
            configs = result.scalars().all()

        loaded = []
        for config in configs:
            strategy_class = STRATEGY_REGISTRY.get(config.name)
            if not strategy_class:
                logger.warning(f"Unknown strategy: {config.name}")
                continue

            params = config.parameters or {}
            params.update({
                "symbol": config.symbol,
                "market_type": config.market_type.value,
            })

            self._strategies[config.name] = strategy_class(params)
            loaded.append(config.name)

        logger.info(f"Loaded {len(loaded)} strategies: {loaded}")

    async def run_all(self):
        """Run one iteration of all loaded strategies."""
        if risk_manager.trading_stopped:
            logger.warning("Strategy run skipped: trading stopped")
            return

        # Reload strategies in case configs changed
        await self.load_strategies()

        for name, strategy in self._strategies.items():
            try:
                signal = await strategy.run()
                if signal:
                    await self._log_signal(name, strategy.symbol, signal)
                    await self._update_last_run(name)
            except Exception as e:
                logger.error(f"Strategy {name} error: {e}")
                await self._log_error(name, str(e))

    async def run_strategy(self, strategy_name: str) -> Optional[str]:
        """Run a specific strategy by name."""
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            await self.load_strategies()
            strategy = self._strategies.get(strategy_name)
        if not strategy:
            logger.error(f"Strategy not found: {strategy_name}")
            return None
        try:
            return await strategy.run()
        except Exception as e:
            logger.error(f"Strategy {strategy_name} error: {e}")
            return None

    def get_available_strategies(self) -> list[dict]:
        """Return list of all registered strategy names and descriptions."""
        return [
            {"name": name, "description": cls.description}
            for name, cls in STRATEGY_REGISTRY.items()
        ]

    async def seed_default_configs(self):
        """Seed default strategy configs if none exist."""
        async with get_db_context() as db:
            for name, cls in STRATEGY_REGISTRY.items():
                result = await db.execute(
                    select(StrategyConfig).where(StrategyConfig.name == name)
                )
                if result.scalar_one_or_none():
                    continue
                config = StrategyConfig(
                    name=name,
                    is_enabled=False,
                    symbol="BTC/USDT",
                    parameters=_default_params(name),
                )
                db.add(config)
        logger.info("Seeded default strategy configs")

    # ── Private helpers ──────────────────────────────────────────────────────

    async def _log_signal(self, strategy: str, symbol: str, signal: str):
        async with get_db_context() as db:
            log = TradeLog(
                level="INFO",
                source=strategy,
                message=f"Signal: {signal} on {symbol}",
                data={"strategy": strategy, "symbol": symbol, "signal": signal},
            )
            db.add(log)

    async def _log_error(self, strategy: str, error: str):
        async with get_db_context() as db:
            log = TradeLog(
                level="ERROR",
                source=strategy,
                message=error,
            )
            db.add(log)

    async def _update_last_run(self, strategy_name: str):
        async with get_db_context() as db:
            result = await db.execute(
                select(StrategyConfig).where(StrategyConfig.name == strategy_name)
            )
            config = result.scalar_one_or_none()
            if config:
                config.last_run = datetime.utcnow()


def _default_params(name: str) -> dict:
    defaults = {
        "ma_crossover": {"fast_period": 9, "slow_period": 21, "timeframe": "1h"},
        "rsi_strategy": {"rsi_period": 14, "oversold": 30, "overbought": 70, "timeframe": "1h"},
        "grid_trading": {"grid_levels": 10, "quantity_per_grid": 0.001},
        "dca_bot": {"interval_minutes": 60, "use_rsi_filter": True, "rsi_threshold": 40, "max_entries": 10},
        "breakout": {"lookback": 20, "timeframe": "1h", "volume_filter": True},
    }
    return defaults.get(name, {})


# Module singleton
strategy_engine = StrategyEngine()
