"""
FastAPI route handlers for all trading modules.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from loguru import logger

from app.database.session import get_db
from app.models.db_models import (
    Order, StrategyConfig, TradingSettings, APICredential,
    TradeLog, MarketType,
)
from app.services.market_data import market_data
from app.trading.spot import spot_trading
from app.trading.futures import futures_trading
from app.strategies.engine import strategy_engine, STRATEGY_REGISTRY
from app.risk.manager import risk_manager
from app.services.backtesting import BacktestEngine
from config.settings import get_settings

settings = get_settings()
templates = Jinja2Templates(directory="frontend/templates")


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class SpotOrderRequest(BaseModel):
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"
    price: Optional[float] = None
    stop_price: Optional[float] = None


class FuturesOrderRequest(BaseModel):
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"
    price: Optional[float] = None
    leverage: int = 1
    reduce_only: bool = False


class StrategyToggleRequest(BaseModel):
    name: str
    enabled: bool


class StrategyParamsRequest(BaseModel):
    name: str
    symbol: str = "BTC/USDT"
    market_type: str = "SPOT"
    parameters: dict = {}


class SettingsUpdateRequest(BaseModel):
    key: str
    value: str


class APICredentialRequest(BaseModel):
    api_key: str
    api_secret: str
    passphrase: str = ""


class BacktestRequest(BaseModel):
    strategy: str
    symbol: str
    timeframe: str = "1h"
    limit: int = 500
    initial_capital: float = 10000.0
    parameters: dict = {}


# ── Market Data Routes ────────────────────────────────────────────────────────

market_router = APIRouter()


@market_router.get("/ticker/{symbol:path}")
async def get_ticker(symbol: str):
    data = await market_data.get_ticker(symbol)
    return {"data": data}


@market_router.get("/price/{symbol:path}")
async def get_price(symbol: str):
    price = await market_data.get_price(symbol)
    return {"symbol": symbol, "price": price}


@market_router.get("/orderbook/{symbol:path}")
async def get_orderbook(symbol: str, depth: int = 20):
    data = await market_data.get_orderbook(symbol, depth)
    return {"data": data}


@market_router.get("/candles/{symbol:path}")
async def get_candles(symbol: str, timeframe: str = "1h", limit: int = 100):
    data = await market_data.get_candles(symbol, timeframe, limit)
    return {"data": data}


@market_router.get("/funding-rate/{symbol:path}")
async def get_funding_rate(symbol: str):
    data = await market_data.get_funding_rate(symbol)
    return {"data": data}


@market_router.get("/exchange-info")
async def get_exchange_info():
    data = await market_data.get_exchange_info()
    return {"data": data}


# ── Spot Trading Routes ───────────────────────────────────────────────────────

spot_router = APIRouter()


@spot_router.post("/order")
async def place_spot_order(req: SpotOrderRequest):
    try:
        result = await spot_trading.place_spot_order(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            order_type=req.order_type,
            price=req.price,
            stop_price=req.stop_price,
            strategy_name="manual",
        )
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@spot_router.delete("/order/{order_id}")
async def cancel_spot_order(order_id: str, symbol: str):
    try:
        result = await spot_trading.cancel_spot_order(order_id, symbol)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@spot_router.get("/orders")
async def get_spot_orders(symbol: Optional[str] = None):
    orders = await spot_trading.get_spot_orders(symbol)
    return {"data": orders}


@spot_router.get("/history")
async def get_order_history(symbol: Optional[str] = None, limit: int = 50):
    orders = await spot_trading.get_order_history(symbol, limit)
    return {"data": orders}


@spot_router.get("/portfolio")
async def get_portfolio():
    data = await spot_trading.get_account_info()
    return {"data": data}


# ── Futures Trading Routes ────────────────────────────────────────────────────

futures_router = APIRouter()


@futures_router.post("/order")
async def place_futures_order(req: FuturesOrderRequest):
    try:
        result = await futures_trading.place_futures_order(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            order_type=req.order_type,
            price=req.price,
            leverage=req.leverage,
            reduce_only=req.reduce_only,
            strategy_name="manual",
        )
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@futures_router.get("/positions")
async def get_positions(symbol: Optional[str] = None):
    positions = await futures_trading.get_futures_positions(symbol)
    return {"data": positions}


@futures_router.post("/close/{symbol:path}")
async def close_position(symbol: str, side: str, quantity: Optional[float] = None):
    try:
        result = await futures_trading.close_position(symbol, side, quantity)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@futures_router.get("/account")
async def get_futures_account():
    data = await futures_trading.get_futures_account()
    return {"data": data}


@futures_router.post("/leverage")
async def set_leverage(symbol: str, leverage: int):
    result = await futures_trading.set_leverage(symbol, leverage)
    return {"data": result}


# ── Strategy Routes ───────────────────────────────────────────────────────────

strategy_router = APIRouter()


@strategy_router.get("/")
async def list_strategies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StrategyConfig))
    configs = result.scalars().all()
    available = strategy_engine.get_available_strategies()
    return {
        "available": available,
        "configs": [
            {
                "id": c.id,
                "name": c.name,
                "is_enabled": c.is_enabled,
                "symbol": c.symbol,
                "market_type": c.market_type.value,
                "parameters": c.parameters,
                "last_run": c.last_run.isoformat() if c.last_run else None,
            }
            for c in configs
        ],
    }


@strategy_router.post("/toggle")
async def toggle_strategy(req: StrategyToggleRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StrategyConfig).where(StrategyConfig.name == req.name))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail=f"Strategy {req.name} not found")
    config.is_enabled = req.enabled
    await db.commit()
    return {"success": True, "name": req.name, "enabled": req.enabled}


@strategy_router.post("/update")
async def update_strategy(req: StrategyParamsRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StrategyConfig).where(StrategyConfig.name == req.name))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail=f"Strategy {req.name} not found")
    config.symbol = req.symbol
    from app.models.db_models import MarketType
    config.market_type = MarketType(req.market_type.upper())
    config.parameters = req.parameters
    await db.commit()
    return {"success": True}


@strategy_router.post("/run/{name}")
async def run_strategy(name: str):
    """Manually trigger a strategy run."""
    signal = await strategy_engine.run_strategy(name)
    return {"strategy": name, "signal": signal}


@strategy_router.get("/logs")
async def get_strategy_logs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TradeLog).order_by(desc(TradeLog.created_at)).limit(limit)
    )
    logs = result.scalars().all()
    return {
        "data": [
            {
                "id": l.id,
                "level": l.level,
                "source": l.source,
                "message": l.message,
                "data": l.data,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]
    }


# ── Settings Routes ───────────────────────────────────────────────────────────

settings_router = APIRouter()


@settings_router.get("/")
async def get_settings_list(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TradingSettings))
    rows = result.scalars().all()
    return {"data": {r.key: r.value for r in rows}}


@settings_router.post("/update")
async def update_setting(req: SettingsUpdateRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TradingSettings).where(TradingSettings.key == req.key)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = req.value
    else:
        setting = TradingSettings(key=req.key, value=req.value)
        db.add(setting)
    await db.commit()
    return {"success": True, "key": req.key, "value": req.value}


@settings_router.post("/credentials")
async def save_credentials(req: APICredentialRequest, db: AsyncSession = Depends(get_db)):
    """Save encrypted API credentials."""
    s = get_settings()
    encrypted_secret = s.encrypt_secret(req.api_secret)
    encrypted_passphrase = s.encrypt_secret(req.passphrase) if req.passphrase else ""

    result = await db.execute(
        select(APICredential).where(APICredential.name == "default")
    )
    cred = result.scalar_one_or_none()
    if cred:
        cred.api_key = req.api_key
        cred.api_secret_encrypted = encrypted_secret
        cred.passphrase_encrypted = encrypted_passphrase
    else:
        cred = APICredential(
            name="default",
            api_key=req.api_key,
            api_secret_encrypted=encrypted_secret,
            passphrase_encrypted=encrypted_passphrase,
        )
        db.add(cred)
    await db.commit()
    return {"success": True, "message": "Credentials saved securely"}


@settings_router.get("/risk")
async def get_risk_stats():
    stats = await risk_manager.get_daily_stats()
    return {"data": stats}


@settings_router.post("/trading/stop")
async def stop_trading():
    await risk_manager.stop_trading("Manual stop via API")
    return {"success": True, "message": "Trading stopped"}


@settings_router.post("/trading/resume")
async def resume_trading():
    await risk_manager.resume_trading()
    return {"success": True, "message": "Trading resumed"}


# ── Backtesting Routes ────────────────────────────────────────────────────────

backtest_router = APIRouter()


@backtest_router.post("/run")
async def run_backtest(req: BacktestRequest):
    """Run a strategy backtest on historical data."""
    try:
        # Fetch historical candles
        candles = await market_data.get_candles(req.symbol, req.timeframe, req.limit)
        if not candles:
            raise HTTPException(status_code=400, detail="Could not fetch candle data")

        engine = BacktestEngine(candles, initial_capital=req.initial_capital)

        params = req.parameters
        if req.strategy == "ma_crossover":
            result = engine.run_ma_crossover(
                fast=int(params.get("fast_period", 9)),
                slow=int(params.get("slow_period", 21)),
            )
        elif req.strategy == "rsi_strategy":
            result = engine.run_rsi(
                period=int(params.get("rsi_period", 14)),
                oversold=float(params.get("oversold", 30)),
                overbought=float(params.get("overbought", 70)),
            )
        elif req.strategy == "breakout":
            result = engine.run_breakout(
                lookback=int(params.get("lookback", 20)),
            )
        else:
            raise HTTPException(status_code=400, detail=f"Backtest not available for {req.strategy}")

        result.symbol = req.symbol
        result.timeframe = req.timeframe
        return {"success": True, "data": result.summary(), "trades": result.trades[-20:]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Dashboard HTML Routes ─────────────────────────────────────────────────────

dashboard_router = APIRouter()


@dashboard_router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "app_name": settings.app_name, "simulation": settings.simulation_mode},
    )


@dashboard_router.get("/settings-page", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "app_name": settings.app_name},
    )


@dashboard_router.get("/backtest-page", response_class=HTMLResponse)
async def backtest_page(request: Request):
    return templates.TemplateResponse(
        "backtest.html",
        {"request": request, "app_name": settings.app_name},
    )
