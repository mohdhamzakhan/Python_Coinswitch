"""
Risk Management Module.
Enforces max trade size, daily loss limits, position limits, leverage limits.
Acts as a gatekeeper before any order is placed.
"""
from datetime import date
from sqlalchemy import select, func
from app.models.db_models import Order, Position, DailyPnL, TradingSettings
from app.database.session import get_db_context
from config.settings import get_settings
from loguru import logger

settings = get_settings()


class RiskViolation(Exception):
    """Raised when a trade violates risk parameters."""
    pass


class RiskManager:
    """
    Enforces risk controls:
      - Max leverage
      - Max trade size (% of balance)
      - Max daily loss
      - Max open positions
      - Kill switch (stop all trading)

    Test usage — bypass the DB completely by setting _risk_params directly:
        rm = RiskManager()
        rm._risk_params = {"max_leverage": "10", "stop_trading": "false", ...}
        rm._count_open_positions = lambda: _async_return(0)
        rm._get_today_loss       = lambda: _async_return(0.0)
        await rm.check_order(...)
    """

    def __init__(self):
        self._trading_stopped: bool = False
        self._risk_params:     dict = {}
        # Set to True once params have been loaded (from DB or injected in tests).
        # check_order will NOT call load_params() when this is True.
        self._params_loaded:   bool = False

    async def load_params(self, force: bool = False):
        """
        Load risk parameters from the `trading_settings` table.

        Skipped when:
          - _params_loaded is True AND force is False
          - _risk_params is already populated (injected directly in tests)

        Pass force=True to unconditionally re-read from DB.
        """
        if (self._params_loaded or self._risk_params) and not force:
            return
        try:
            async with get_db_context() as db:
                result = await db.execute(select(TradingSettings))
                rows   = result.scalars().all()
                self._risk_params  = {r.key: r.value for r in rows}
                self._params_loaded = True
        except Exception:
            # DB unavailable (e.g. during tests before schema is initialised).
            # Keep whatever is in _risk_params; raise only if it's completely empty.
            if not self._risk_params:
                raise

    def get_param(self, key: str, default):
        val = self._risk_params.get(key)
        if val is None:
            return default
        try:
            return type(default)(val)
        except (ValueError, TypeError):
            return default

    @property
    def trading_stopped(self) -> bool:
        stop_flag = self._risk_params.get("stop_trading", "false")
        return self._trading_stopped or str(stop_flag).lower() in ("true", "1", "yes")

    async def stop_trading(self, reason: str = "Manual stop"):
        self._trading_stopped = True
        await self._save_param("stop_trading", "true")
        logger.warning(f"TRADING STOPPED: {reason}")

    async def resume_trading(self):
        self._trading_stopped = False
        await self._save_param("stop_trading", "false")
        logger.info("Trading resumed")

    async def check_order(
        self,
        symbol:          str,
        side:            str,
        quantity:        float,
        price:           float,
        market_type:     str   = "SPOT",
        leverage:        int   = 1,
        account_balance: float = 0.0,
    ) -> bool:
        """
        Validate an order against all risk controls.
        Returns True if allowed, raises RiskViolation otherwise.
        """
        # Load from DB only when _risk_params has NOT been pre-populated.
        if not self._risk_params and not self._params_loaded:
            await self.load_params()

        # ── Kill switch ──────────────────────────────────────────────────────
        if self.trading_stopped:
            raise RiskViolation("Trading is currently stopped (kill switch active)")

        # ── Leverage limit ───────────────────────────────────────────────────
        max_leverage = self.get_param("max_leverage", 20)
        if leverage > max_leverage:
            raise RiskViolation(
                f"Leverage {leverage}x exceeds max allowed {max_leverage}x"
            )

        # ── Max trade size ───────────────────────────────────────────────────
        if account_balance > 0 and price > 0:
            trade_value    = quantity * price
            max_size_pct   = self.get_param("max_position_size_percent", settings.max_position_size_percent)
            max_trade_value = account_balance * (max_size_pct / 100)
            if trade_value > max_trade_value:
                raise RiskViolation(
                    f"Trade value ${trade_value:.2f} exceeds max allowed "
                    f"${max_trade_value:.2f} ({max_size_pct}% of ${account_balance:.2f})"
                )

        # ── Max open positions ───────────────────────────────────────────────
        max_positions     = self.get_param("max_open_positions", settings.max_open_positions)
        current_positions = await self._count_open_positions()
        if current_positions >= max_positions:
            raise RiskViolation(
                f"Already at max open positions ({current_positions}/{max_positions})"
            )

        # ── Max daily loss ───────────────────────────────────────────────────
        max_daily_loss_pct = self.get_param("max_daily_loss_percent", settings.max_daily_loss_percent)
        if account_balance > 0 and max_daily_loss_pct > 0:
            today_loss = await self._get_today_loss()
            max_loss   = account_balance * (max_daily_loss_pct / 100)
            if today_loss >= max_loss:
                await self.stop_trading(f"Daily loss limit reached: ${today_loss:.2f}")
                raise RiskViolation(
                    f"Daily loss limit reached: ${today_loss:.2f} >= ${max_loss:.2f}"
                )

        logger.debug(f"Risk check passed: {symbol} {side} {quantity} @ {price} x{leverage}")
        return True

    async def record_trade_result(self, pnl: float):
        today = date.today().isoformat()
        async with get_db_context() as db:
            result = await db.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            if not record:
                record = DailyPnL(date=today)
                db.add(record)
            record.realized_pnl += pnl
            record.trade_count  += 1
            if pnl > 0:
                record.win_count  += 1
            else:
                record.loss_count += 1

    async def get_daily_stats(self) -> dict:
        today = date.today().isoformat()
        async with get_db_context() as db:
            result = await db.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            if not record:
                return {"date": today, "realized_pnl": 0, "trade_count": 0, "win_rate": 0}
            win_rate = (record.win_count / record.trade_count * 100) if record.trade_count > 0 else 0
            return {
                "date":         record.date,
                "realized_pnl": record.realized_pnl,
                "trade_count":  record.trade_count,
                "win_count":    record.win_count,
                "loss_count":   record.loss_count,
                "win_rate":     round(win_rate, 2),
            }

    # ── Private DB helpers ────────────────────────────────────────────────────

    async def _count_open_positions(self) -> int:
        async with get_db_context() as db:
            result = await db.execute(
                select(func.count(Position.id)).where(Position.is_open == True)
            )
            return result.scalar() or 0

    async def _get_today_loss(self) -> float:
        today = date.today().isoformat()
        async with get_db_context() as db:
            result = await db.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            if not record:
                return 0.0
            return abs(min(0, record.realized_pnl))

    async def _save_param(self, key: str, value: str):
        async with get_db_context() as db:
            result  = await db.execute(select(TradingSettings).where(TradingSettings.key == key))
            setting = result.scalar_one_or_none()
            if setting:
                setting.value = value
            else:
                setting = TradingSettings(key=key, value=value)
                db.add(setting)


# Module singleton
risk_manager = RiskManager()
