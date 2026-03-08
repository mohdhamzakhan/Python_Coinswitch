"""
FastAPI route handlers for the CoinSwitch trading platform.

Spot trading covers all 15 official API endpoints:
    1.  GET  /spot/coins              — active coins per exchange
    2.  POST /spot/precision          — exchange precision
    3.  GET  /spot/trade-info         — min/max sizes & precision
    4.  GET  /spot/fee                — maker/taker fees
    5.  POST /spot/order              — create order
    6.  DELETE /spot/order/{order_id} — cancel order
    7.  GET  /spot/order              — get single order
    8.  GET  /spot/orders/open        — open orders
    9.  GET  /spot/orders/closed      — closed orders
    10. GET  /spot/portfolio          — portfolio balances
    11. GET  /spot/tds                — TDS summary
    12. GET  /spot/trades             — recent trades
    13. GET  /spot/depth              — order book
    14. GET  /spot/candles            — OHLCV candles
    15. GET  /spot/ticker/all         — 24hr ticker all pairs
    16. GET  /spot/ticker             — 24hr ticker for symbol

Exchange ↔ quote currency:
    coinswitchx / wazirx  →  INR   (e.g. BTC/INR)
    c2c1 / c2c2           →  USDT  (e.g. BTC/USDT)
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from loguru import logger

from app.database.session import get_db
from app.models.db_models import (
    Order, StrategyConfig, TradingSettings, APICredential,
    TradeLog, MarketType,
)
from app.services.market_data import market_data
from app.trading.spot import spot_trading, VALID_EXCHANGES, quote_for_exchange, build_symbol
from app.api.client import invalidate_api_client
from app.strategies.engine import strategy_engine, STRATEGY_REGISTRY
from app.risk.manager import risk_manager
from app.services.backtesting import BacktestEngine
from config.settings import get_settings

settings = get_settings()
templates = Jinja2Templates(directory="frontend/templates")


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

_EXCHANGE_VALUES = "coinswitchx | wazirx | c2c1 | c2c2"


class SpotOrderRequest(BaseModel):
    """
    Request body for creating a spot limit order.

    The symbol is built automatically from base_coin + exchange:
        coinswitchx / wazirx  → BTC/INR
        c2c1 / c2c2           → BTC/USDT

    If you supply `symbol` directly it is validated against the exchange.
    """
    exchange:   str   = Field(..., description=_EXCHANGE_VALUES)
    base_coin:  str   = Field(..., description="Base coin, e.g. BTC, ETH, SHIB")
    side:       str   = Field(..., description="buy | sell")
    price:      float = Field(..., gt=0, description="Limit price")
    quantity:   float = Field(..., gt=0, description="Base quantity")
    symbol:     Optional[str] = Field(None, description="Override symbol (validated)")
    strategy_name: Optional[str] = None

    @field_validator("exchange")
    @classmethod
    def exchange_must_be_valid(cls, v: str) -> str:
        if v.lower().strip() not in VALID_EXCHANGES:
            raise ValueError(f"exchange must be one of: {_EXCHANGE_VALUES}")
        return v.lower().strip()

    @field_validator("side")
    @classmethod
    def side_must_be_valid(cls, v: str) -> str:
        if v.lower() not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        return v.lower()


class CancelOrderRequest(BaseModel):
    order_id: str = Field(..., description="UUID returned by create order")


class PrecisionRequest(BaseModel):
    exchange: str = Field(..., description=_EXCHANGE_VALUES)
    symbol:   Optional[str] = Field(None, description="e.g. BTC/INR (optional)")


class FuturesOrderRequest(BaseModel):
    symbol:     str
    side:       str
    quantity:   float
    order_type: str   = "MARKET"
    price:      Optional[float] = None
    leverage:   int   = 1
    reduce_only: bool = False


class StrategyToggleRequest(BaseModel):
    name:    str
    enabled: bool


class StrategyParamsRequest(BaseModel):
    name:        str
    symbol:      str  = "BTC/USDT"
    market_type: str  = "SPOT"
    parameters:  dict = {}


class SettingsUpdateRequest(BaseModel):
    key:   str
    value: str


class APICredentialRequest(BaseModel):
    api_key:    str  # from CoinSwitch portal
    api_secret: str  # hex-encoded Ed25519 private key


class BacktestRequest(BaseModel):
    strategy:        str
    symbol:          str
    timeframe:       str   = "1h"
    limit:           int   = 500
    initial_capital: float = 10000.0
    parameters:      dict  = {}


# ── Market Data Routes (legacy / general) ────────────────────────────────────

market_router = APIRouter()


@market_router.get("/ticker/{symbol:path}")
async def get_ticker_legacy(symbol: str):
    data = await market_data.get_ticker(symbol)
    return {"data": data}


@market_router.get("/price/{symbol:path}")
async def get_price(symbol: str):
    price = await market_data.get_price(symbol)
    return {"data": {"price": price, "symbol": symbol}}


@market_router.get("/orderbook/{symbol:path}")
async def get_orderbook(
    symbol: str,
    exchange: str = Query("coinswitchx", description="coinswitchx | wazirx | c2c1 | c2c2"),
):
    """
    Order book (bids + asks) for a symbol.

    - **symbol**: e.g. `BTC/INR` for coinswitchx/wazirx, `BTC/USDT` for c2c1/c2c2
    - **exchange**: which exchange to query (default: coinswitchx)
    """
    import urllib.parse
    symbol = urllib.parse.unquote(symbol).upper()
    try:
        data = await market_data.get_orderbook(symbol, exchange.lower())
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CoinSwitch API error: {e}")


@market_router.get("/candles/{symbol:path}")
async def get_candles_legacy(
    symbol: str,
    timeframe: str = Query("1h", description="1m 5m 15m 30m 1h 4h 1d"),
    limit: int = Query(100, ge=1, le=1000),
    exchange: str = Query("coinswitchx", description="coinswitchx | wazirx | c2c1 | c2c2"),
):
    """
    OHLCV candle data for a symbol.

    - **symbol**: e.g. `BTC/INR` — must match the exchange's quote currency
    - **timeframe**: candle interval string (1m 5m 15m 1h 4h 1d)
    - **limit**: number of candles to return (1–1000)
    - **exchange**: coinswitchx/wazirx for INR pairs, c2c1/c2c2 for USDT pairs

    Requires valid API credentials saved in Settings.
    """
    import urllib.parse
    symbol = urllib.parse.unquote(symbol).upper()
    try:
        data = await market_data.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            exchange=exchange.lower(),
        )
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CoinSwitch API error: {e}")


@market_router.get("/trades/{symbol:path}")
async def get_recent_trades(
    symbol: str,
    exchange: str = Query("coinswitchx", description="coinswitchx | wazirx | c2c1 | c2c2"),
):
    """
    Recent trade executions for a symbol.

    - **symbol**: e.g. `BTC/INR`
    - **exchange**: which exchange to query

    Requires valid API credentials. CoinSwitch is a spot exchange —
    there are no funding rates (that is a futures concept).
    """
    import urllib.parse
    symbol = urllib.parse.unquote(symbol).upper()
    try:
        data = await market_data.get_trades(symbol, exchange.lower())
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CoinSwitch API error: {e}")


@market_router.get("/fees")
async def get_trading_fees(
    exchange: str = Query("coinswitchx", description="coinswitchx | wazirx | c2c1 | c2c2"),
):
    """
    Maker / taker fee schedule for an exchange.
    Requires valid API credentials.
    """
    data = await market_data.get_trading_fee(exchange.lower())
    return {"data": data}


@market_router.get("/status")
async def api_status():
    """
    Diagnostic endpoint. Shows whether API credentials are loaded and valid.
    Call this first if market data endpoints return errors.
    """
    from app.api.client import get_api_client, CoinSwitchAuthError, CoinSwitchAPIError
    client = await get_api_client()
    has_key    = bool(client.api_key)
    has_secret = bool(client.secret_key_hex)
    secret_len = len(client.secret_key_hex)

    result = {
        "credentials_loaded": has_key and has_secret,
        "api_key_present":    has_key,
        "api_key_preview":    (client.api_key[:8] + "…") if has_key else None,
        "secret_present":     has_secret,
        "secret_length":      secret_len,
        "secret_length_ok":   secret_len == 64,
        "hint": None,
    }

    if not has_key or not has_secret:
        result["hint"] = "Go to Settings → API Credentials and save your CoinSwitch API key and 64-char hex secret."
        return result

    if secret_len != 64:
        result["hint"] = f"Secret is {secret_len} chars, must be exactly 64 hex characters."
        return result

    # Try a live ping — validate/keys endpoint
    try:
        ping = await client.validate_keys()
        result["api_reachable"] = True
        result["api_response"]  = ping
    except CoinSwitchAuthError as e:
        result["api_reachable"] = False
        result["error"] = f"Auth error: {e}"
        result["hint"]  = "Secret key may be wrong. Re-paste it from the CoinSwitch HFT portal."
    except CoinSwitchAPIError as e:
        result["api_reachable"] = False
        result["error"] = f"API error {e.status_code}: {e}"
        result["hint"]  = "Check your API key. 401 = invalid key. 403 = IP not whitelisted."
    except Exception as e:
        result["api_reachable"] = False
        result["error"] = str(e)

    return result


@market_router.get("/exchange-info")
async def get_exchange_info(
    exchange: str = Query("coinswitchx", description="coinswitchx | wazirx | c2c1 | c2c2"),
):
    try:
        data = await market_data.get_exchange_info(exchange.lower())
        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CoinSwitch API error: {e}")


# ── Spot Trading Routes ───────────────────────────────────────────────────────
# All 15 official CoinSwitch spot API endpoints.

spot_router = APIRouter()


# 1 ─ Active Coins ─────────────────────────────────────────────────────────────

@spot_router.get(
    "/coins",
    summary="Active coins",
    description="List all active trading pairs for an exchange.",
)
async def get_active_coins(
    exchange: str = Query(..., description=_EXCHANGE_VALUES),
):
    """GET /trade/api/v2/coins — returns list of symbols for the exchange."""
    try:
        data = await spot_trading.get_active_coins(exchange)
        return {
            "data":    data,
            "exchange": exchange.lower(),
            "quote":   quote_for_exchange(exchange),
            "count":   len(data),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 2 ─ Exchange Precision ────────────────────────────────────────────────────────

@spot_router.post(
    "/precision",
    summary="Exchange precision",
    description="Get decimal precision rules for a symbol.",
)
async def get_exchange_precision(req: PrecisionRequest):
    """POST /trade/api/v2/exchangePrecision"""
    try:
        data = await spot_trading.get_exchange_precision(req.exchange, req.symbol)
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 3 ─ Trade Info ────────────────────────────────────────────────────────────────

@spot_router.get(
    "/trade-info",
    summary="Trade info",
    description="Get min/max order sizes and precision for a symbol.",
)
async def get_trade_info(
    exchange: str           = Query(..., description=_EXCHANGE_VALUES),
    symbol:   Optional[str] = Query(None, description="e.g. BTC/INR"),
):
    """GET /trade/api/v2/tradeInfo"""
    try:
        data = await spot_trading.get_trade_info(exchange, symbol)
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 4 ─ Trading Fee ───────────────────────────────────────────────────────────────

@spot_router.get(
    "/fee",
    summary="Trading fee",
    description="Get maker/taker fees for every coin on an exchange.",
)
async def get_trading_fee(
    exchange: str = Query(..., description=_EXCHANGE_VALUES),
):
    """GET /trade/api/v2/tradingFee"""
    try:
        data = await spot_trading.get_trading_fee(exchange)
        return {"data": data, "exchange": exchange.lower()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 5 ─ Create Order ──────────────────────────────────────────────────────────────

@spot_router.post(
    "/order",
    summary="Create order",
    description=(
        "Place a LIMIT spot order. Symbol is derived from base_coin + exchange: "
        "coinswitchx/wazirx → BTC/INR, c2c1/c2c2 → BTC/USDT."
    ),
)
async def create_order(req: SpotOrderRequest):
    """POST /trade/api/v2/order"""
    try:
        result = await spot_trading.create_order(
            exchange      = req.exchange,
            base_coin     = req.base_coin,
            side          = req.side,
            price         = req.price,
            quantity      = req.quantity,
            symbol        = req.symbol,
            strategy_name = req.strategy_name or "manual",
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# 6 ─ Cancel Order ─────────────────────────────────────────────────────────────

@spot_router.delete(
    "/order/{order_id}",
    summary="Cancel order",
    description="Cancel an open order by order_id.",
)
async def cancel_order(order_id: str):
    """DELETE /trade/api/v2/order"""
    try:
        result = await spot_trading.cancel_order(order_id)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# 7 ─ Get Order ─────────────────────────────────────────────────────────────────

@spot_router.get(
    "/order",
    summary="Get order",
    description="Fetch the current state of a single order.",
)
async def get_order(
    order_id: str = Query(..., description="UUID of the order"),
):
    """GET /trade/api/v2/order"""
    try:
        data = await spot_trading.get_order(order_id)
        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 8 ─ Open Orders ──────────────────────────────────────────────────────────────

@spot_router.get(
    "/orders/open",
    summary="Open orders",
    description="Get all currently open or partially executed orders.",
)
async def get_open_orders(
    count:     int           = Query(25,   description="Max results"),
    side:      Optional[str] = Query(None, description="buy | sell"),
    symbols:   Optional[str] = Query(None, description="Comma-separated, e.g. BTC/INR,ETH/INR"),
    exchanges: Optional[str] = Query(None, description="Comma-separated exchanges"),
    from_time: Optional[int] = Query(None, description="Start epoch ms"),
    to_time:   Optional[int] = Query(None, description="End epoch ms"),
):
    """GET /trade/api/v2/orders   open=True"""
    orders = await spot_trading.get_open_orders(
        count=count, side=side, symbols=symbols,
        exchanges=exchanges, from_time=from_time, to_time=to_time,
    )
    return {"data": {"orders": orders, "count": len(orders)}}


# 9 ─ Closed Orders ────────────────────────────────────────────────────────────

@spot_router.get(
    "/orders/closed",
    summary="Closed orders",
    description="Get completed, cancelled, or expired orders.",
)
async def get_closed_orders(
    count:     int           = Query(500,  description="Max results"),
    side:      Optional[str] = Query(None, description="buy | sell"),
    symbols:   Optional[str] = Query(None, description="Comma-separated, e.g. BTC/INR,ETH/INR"),
    exchanges: Optional[str] = Query(None, description="Comma-separated exchanges"),
    status:    Optional[str] = Query(None, description="EXECUTED | CANCELLED | EXPIRED"),
    from_time: Optional[int] = Query(None, description="Start epoch ms"),
    to_time:   Optional[int] = Query(None, description="End epoch ms"),
):
    """GET /trade/api/v2/orders   open=False"""
    orders = await spot_trading.get_closed_orders(
        count=count, side=side, symbols=symbols, exchanges=exchanges,
        status=status, from_time=from_time, to_time=to_time,
    )
    return {"data": {"orders": orders, "count": len(orders)}}


# 10 ─ Portfolio ───────────────────────────────────────────────────────────────

@spot_router.get(
    "/portfolio",
    summary="Portfolio",
    description="Get all balances (coins + INR + USDT).",
)
async def get_portfolio():
    """GET /trade/api/v2/user/portfolio"""
    try:
        data = await spot_trading.get_portfolio()
        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 11 ─ TDS ────────────────────────────────────────────────────────────────────

@spot_router.get(
    "/tds",
    summary="TDS",
    description="Total TDS deducted for the current financial year.",
)
async def get_tds():
    """GET /trade/api/v2/tds"""
    try:
        data = await spot_trading.get_tds()
        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 12 ─ Trades ─────────────────────────────────────────────────────────────────

@spot_router.get(
    "/trades",
    summary="Recent trades",
    description="Recent executions for a symbol. Quote currency must match exchange.",
)
async def get_trades(
    exchange: str = Query(..., description=_EXCHANGE_VALUES),
    symbol:   str = Query(..., description="e.g. BTC/INR or BTC/USDT"),
):
    """GET /trade/api/v2/trades"""
    try:
        data = await spot_trading.get_trades(exchange, symbol)
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 13 ─ Depth (Order Book) ─────────────────────────────────────────────────────

@spot_router.get(
    "/depth",
    summary="Order book depth",
    description="Bids and asks for a symbol. Quote currency must match exchange.",
)
async def get_depth(
    exchange: str = Query(..., description=_EXCHANGE_VALUES),
    symbol:   str = Query(..., description="e.g. BTC/INR or BTC/USDT"),
):
    """GET /trade/api/v2/depth"""
    try:
        data = await spot_trading.get_depth(exchange, symbol)
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 14 ─ Candles ────────────────────────────────────────────────────────────────

@spot_router.get(
    "/candles",
    summary="OHLCV candles",
    description="Candlestick data for a symbol. interval is in minutes (e.g. 1, 5, 60, 1440).",
)
async def get_candles(
    exchange:   str = Query(..., description=_EXCHANGE_VALUES),
    symbol:     str = Query(..., description="e.g. BTC/INR"),
    interval:   int = Query(..., description="Candle duration in minutes"),
    start_time: int = Query(..., description="Start epoch ms"),
    end_time:   int = Query(..., description="End epoch ms"),
):
    """GET /trade/api/v2/candles"""
    try:
        data = await spot_trading.get_candles(exchange, symbol, interval, start_time, end_time)
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 15a ─ Ticker — All Pairs ────────────────────────────────────────────────────

@spot_router.get(
    "/ticker/all",
    summary="24hr ticker — all pairs",
    description="24-hour stats for every trading pair on the exchange.",
)
async def get_ticker_all_pairs(
    exchange: str = Query(..., description=_EXCHANGE_VALUES),
):
    """GET /trade/api/v2/24hr/all-pairs/ticker"""
    try:
        data = await spot_trading.get_ticker_all_pairs(exchange)
        return {"data": data, "exchange": exchange.lower(), "pairs": len(data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 15b ─ Ticker — Specific Symbol ──────────────────────────────────────────────

@spot_router.get(
    "/ticker",
    summary="24hr ticker — specific symbol",
    description="24-hour stats for one symbol across one or more exchanges.",
)
async def get_ticker(
    symbol:    str = Query(..., description="e.g. BTC/INR or BTC/USDT"),
    exchanges: str = Query(..., description="Comma-separated, e.g. coinswitchx,wazirx"),
):
    """GET /trade/api/v2/24hr/ticker"""
    try:
        data = await spot_trading.get_ticker(symbol, exchanges)
        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─ Helper: best bid/ask price ─────────────────────────────────────────────────

@spot_router.get(
    "/best-price",
    summary="Best bid/ask",
    description="Top-of-book bid, ask, mid-price and spread from the order book.",
)
async def get_best_price(
    exchange: str = Query(..., description=_EXCHANGE_VALUES),
    symbol:   str = Query(..., description="e.g. BTC/INR"),
):
    try:
        data = await spot_trading.get_best_price(exchange, symbol)
        return {"data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─ Helper: single currency balance ───────────────────────────────────────────

@spot_router.get(
    "/balance/{currency}",
    summary="Currency balance",
    description="Available main balance for a single currency from the portfolio.",
)
async def get_balance(currency: str):
    try:
        balance = await spot_trading.get_balance(currency)
        return {"data": {"currency": currency.upper(), "balance": balance}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─ Exchange meta helper ───────────────────────────────────────────────────────

@spot_router.get(
    "/exchanges",
    summary="Exchange info",
    description="List valid exchanges and their quote currencies.",
)
async def list_exchanges():
    return {
        "data": {
            "exchanges": {
                "coinswitchx": {"quote": "INR",  "description": "CoinSwitchX — INR pairs"},
                "wazirx":      {"quote": "INR",  "description": "WazirX — INR pairs"},
                "c2c1":        {"quote": "USDT", "description": "C2C1 — USDT pairs"},
                "c2c2":        {"quote": "USDT", "description": "C2C2 — USDT pairs"},
            },
            "note": "Use coinswitchx/wazirx for INR; c2c1/c2c2 for USDT",
        }
    }




@spot_router.get(
    "/history",
    summary="Order history",
    description="Recent spot orders from local database.",
)
async def get_order_history(
    symbol: Optional[str] = Query(None),
    limit:  int           = Query(50),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import desc as sqldesc
    query = select(Order).where(Order.market_type == MarketType.SPOT)
    if symbol:
        query = query.where(Order.symbol == symbol)
    query = query.order_by(sqldesc(Order.created_at)).limit(limit)
    result = await db.execute(query)
    orders = result.scalars().all()
    def _fmt(o):
        return {
            "id": o.id, "exchange_order_id": o.exchange_order_id,
            "symbol": o.symbol, "side": o.side.value,
            "order_type": o.order_type.value, "quantity": o.quantity,
            "price": o.price, "filled_quantity": o.filled_quantity,
            "avg_fill_price": o.avg_fill_price, "status": o.status.value,
            "strategy_name": o.strategy_name, "simulated": o.simulated,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
    return {"data": [_fmt(o) for o in orders]}

# ── Futures Trading Routes ────────────────────────────────────────────────────

futures_router = APIRouter()


@futures_router.post("/order")
async def place_futures_order(req: FuturesOrderRequest):
    try:
        result = await futures_trading.place_futures_order(
            symbol=req.symbol, side=req.side, quantity=req.quantity,
            order_type=req.order_type, price=req.price,
            leverage=req.leverage, reduce_only=req.reduce_only,
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
    strategies = result.scalars().all()
    items = [
        {
            "name":        s.name,
            "is_enabled":  s.is_enabled,
            "market_type": s.market_type.value if s.market_type else "SPOT",
            "symbol":      s.symbol,
            "parameters":  s.parameters or {},
            "last_run":    s.last_run.isoformat() if s.last_run else None,
        }
        for s in strategies
    ]
    # Return under both keys so dashboard (j.configs) and settings (j.data) both work
    return {"data": items, "configs": items}


@strategy_router.post("/toggle")
async def toggle_strategy(req: StrategyToggleRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.name == req.name)
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {req.name!r} not found")
    strategy.is_enabled = req.enabled
    await db.commit()
    return {"success": True, "name": req.name, "is_enabled": req.enabled}


@strategy_router.post("/update")
async def update_strategy(req: StrategyParamsRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.name == req.name)
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        strategy = StrategyConfig(name=req.name)
        db.add(strategy)
    strategy.symbol      = req.symbol
    strategy.parameters  = req.parameters
    strategy.market_type = MarketType(req.market_type.upper())
    await db.commit()
    return {"success": True}


@strategy_router.post("/run/{name}")
async def run_strategy(name: str):
    try:
        await strategy_engine.run_strategy(name)
        return {"success": True, "strategy": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@strategy_router.get("/logs")
async def get_strategy_logs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TradeLog).order_by(desc(TradeLog.created_at)).limit(limit)
    )
    logs = result.scalars().all()
    return {
        "data": [
            {
                "id":         l.id,
                "level":      l.level,
                "source":     l.source,
                "message":    l.message,
                "data":       l.data,
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
    """Save encrypted API credentials (Ed25519 hex key)."""
    s = get_settings()
    encrypted_secret = s.encrypt_secret(req.api_secret)

    result = await db.execute(
        select(APICredential).where(APICredential.name == "default")
    )
    cred = result.scalar_one_or_none()
    if cred:
        cred.api_key              = req.api_key
        cred.api_secret_encrypted = encrypted_secret
    else:
        cred = APICredential(
            name="default",
            api_key=req.api_key,
            api_secret_encrypted=encrypted_secret,
        )
        db.add(cred)
    await db.commit()
    # Force the API client to reload credentials on next call
    invalidate_api_client()
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
    try:
        # Determine exchange from symbol quote currency
        symbol_upper = req.symbol.upper()
        if "/" in symbol_upper:
            quote = symbol_upper.split("/")[1]
            exchange = "coinswitchx" if quote == "INR" else "c2c1"
        else:
            exchange = "coinswitchx"

        candles = await market_data.get_candles(
            symbol    = symbol_upper,
            timeframe = req.timeframe,
            limit     = req.limit,
            exchange  = exchange,
        )
        if not candles:
            raise HTTPException(status_code=400, detail="Could not fetch candle data from CoinSwitch. Check that your API credentials are saved in Settings and that the symbol/exchange combination is valid (e.g. BTC/INR on coinswitchx, BTC/USDT on c2c1).")

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
            result = engine.run_breakout(lookback=int(params.get("lookback", 20)))
        else:
            raise HTTPException(status_code=400, detail=f"No backtest for {req.strategy!r}")

        result.symbol    = req.symbol
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
