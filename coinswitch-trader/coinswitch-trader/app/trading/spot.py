"""
Spot Trading Module — CoinSwitch API (all 15 endpoints).

Exchange ↔ Quote currency rule:
    coinswitchx / wazirx  →  INR  (e.g. BTC/INR)
    c2c1 / c2c2           →  USDT (e.g. BTC/USDT)

Order statuses returned by the API:
    OPEN               Order placed, not yet executed
    PARTIALLY_EXECUTED Part of the order has been filled
    EXECUTED           Fully filled
    CANCELLED          Cancelled by user
    EXPIRED            Expired by the exchange
    DISCARDED          Not placed on the exchange
    CANCELLATION_RAISED  Cancellation requested (intermediate)
    EXPIRATION_RAISED    Expiry requested (intermediate)
"""
from __future__ import annotations

from typing import Optional
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.client import get_api_client, CoinSwitchAPIError
from app.models.db_models import Order, OrderSide, OrderType, OrderStatus, MarketType
from app.database.session import get_db_context
from config.settings import get_settings
from loguru import logger

settings = get_settings()


# ── Exchange / Quote-currency helpers ─────────────────────────────────────────

# Exchanges that trade against INR
INR_EXCHANGES  = {"coinswitchx", "wazirx"}
# Exchanges that trade against USDT
USDT_EXCHANGES = {"c2c1", "c2c2"}

# All valid exchanges (lower-case)
VALID_EXCHANGES = INR_EXCHANGES | USDT_EXCHANGES


def quote_for_exchange(exchange: str) -> str:
    """
    Return the quote currency for an exchange.

    coinswitchx / wazirx  → "INR"
    c2c1 / c2c2           → "USDT"

    Raises ValueError for unknown exchanges.
    """
    ex = exchange.lower().strip()
    if ex in INR_EXCHANGES:
        return "INR"
    if ex in USDT_EXCHANGES:
        return "USDT"
    raise ValueError(
        f"Unknown exchange {exchange!r}. "
        f"Valid values: {sorted(VALID_EXCHANGES)}"
    )


def build_symbol(base: str, exchange: str) -> str:
    """
    Build the full trading symbol for a base coin and exchange.

    Examples:
        build_symbol("BTC", "coinswitchx") → "BTC/INR"
        build_symbol("BTC", "c2c1")        → "BTC/USDT"
        build_symbol("ETH", "wazirx")      → "ETH/INR"
    """
    return f"{base.upper()}/{quote_for_exchange(exchange)}"


def validate_symbol_exchange(symbol: str, exchange: str) -> None:
    """
    Assert that the quote currency in `symbol` matches the exchange's
    expected quote currency.  Raises ValueError if they are inconsistent.

    Examples:
        validate_symbol_exchange("BTC/INR",  "coinswitchx")  → OK
        validate_symbol_exchange("BTC/USDT", "coinswitchx")  → ValueError
    """
    expected_quote = quote_for_exchange(exchange)
    if "/" not in symbol:
        raise ValueError(f"Symbol must be in BASE/QUOTE format, got {symbol!r}")
    actual_quote = symbol.split("/", 1)[1].upper()
    if actual_quote != expected_quote:
        raise ValueError(
            f"Symbol {symbol!r} uses quote currency {actual_quote!r} "
            f"but exchange {exchange!r} requires {expected_quote!r}. "
            f"Use {symbol.split('/')[0]}/{expected_quote} instead."
        )


# ── Order status mapping ───────────────────────────────────────────────────────

# Map API status strings → internal OrderStatus enum
_API_STATUS_MAP: dict[str, OrderStatus] = {
    "OPEN":                OrderStatus.OPEN,
    "PARTIALLY_EXECUTED":  OrderStatus.PARTIALLY_FILLED,
    "EXECUTED":            OrderStatus.FILLED,
    "CANCELLED":           OrderStatus.CANCELLED,
    "CANCELLATION_RAISED": OrderStatus.CANCELLED,
    "EXPIRED":             OrderStatus.CANCELLED,
    "EXPIRATION_RAISED":   OrderStatus.CANCELLED,
    "DISCARDED":           OrderStatus.REJECTED,
}


def map_api_status(api_status: str) -> OrderStatus:
    return _API_STATUS_MAP.get(api_status.upper(), OrderStatus.OPEN)


# ── SpotTradingService ────────────────────────────────────────────────────────

class SpotTradingService:
    """
    Handles all CoinSwitch spot trading operations.

    Every method maps 1-to-1 to an official API endpoint.
    In simulation mode, orders are recorded locally without hitting the exchange.

    Exchange / symbol rules are enforced at every entry point:
        coinswitchx, wazirx → symbol must end in /INR
        c2c1, c2c2          → symbol must end in /USDT
    """

    # ── 1. Active Coins ───────────────────────────────────────────────────────

    async def get_active_coins(self, exchange: str) -> list[str]:
        """
        GET /trade/api/v2/coins

        Returns the list of active trading pairs for the given exchange.

        Args:
            exchange: "coinswitchx" | "wazirx" | "c2c1" | "c2c2"

        Returns:
            List of symbols, e.g. ["BTC/INR", "ETH/INR", ...]
        """
        ex = exchange.lower().strip()
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/coins",
                params={"exchange": ex},
            )
            data = resp.get("data", {})
            return data.get(ex, [])
        except Exception as e:
            logger.error(f"get_active_coins({exchange}): {e}")
            return []

    # ── 2. Exchange Precision ─────────────────────────────────────────────────

    async def get_exchange_precision(
        self,
        exchange: str,
        symbol: Optional[str] = None,
    ) -> dict:
        """
        POST /trade/api/v2/exchangePrecision

        Returns the precision rules (decimal places) for the exchange / symbol.

        Args:
            exchange: "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
            symbol:   e.g. "BTC/INR" (optional — returns all if omitted)

        Returns:
            {exchange: {symbol: {base, quote, limit}}}
        """
        if symbol:
            validate_symbol_exchange(symbol, exchange)

        payload: dict = {"exchange": exchange.lower()}
        if symbol:
            payload["symbol"] = symbol.upper()

        client = await get_api_client()
        try:
            resp = await client.post("/trade/api/v2/exchangePrecision", payload=payload)
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_exchange_precision({exchange}, {symbol}): {e}")
            return {}

    # ── 3. Trade Info ─────────────────────────────────────────────────────────

    async def get_trade_info(
        self,
        exchange: str,
        symbol: Optional[str] = None,
    ) -> dict:
        """
        GET /trade/api/v2/tradeInfo

        Returns min/max order sizes and precision for the symbol.

        Args:
            exchange: "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
            symbol:   e.g. "BTC/INR" (optional)

        Returns:
            {exchange: {symbol: {quote: {min, max}, precision: {...}}}}
        """
        if symbol:
            validate_symbol_exchange(symbol, exchange)

        params: dict = {"exchange": exchange.lower()}
        if symbol:
            params["symbol"] = symbol.upper()

        client = await get_api_client()
        try:
            resp = await client.get("/trade/api/v2/tradeInfo", params=params)
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_trade_info({exchange}, {symbol}): {e}")
            return {}

    # ── 4. Trading Fee ────────────────────────────────────────────────────────

    async def get_trading_fee(self, exchange: str) -> dict:
        """
        GET /trade/api/v2/tradingFee

        Returns maker/taker fees for every coin on the exchange.

        Args:
            exchange: "coinswitchx" | "wazirx" | "c2c1" | "c2c2"

        Returns:
            {coin: {maker_fee, taker_fee, maker_fee_after_discount, ...}}
        """
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/tradingFee",
                params={"exchange": exchange.lower()},
            )
            data = resp.get("data", {})
            return data.get(exchange.lower(), data)
        except Exception as e:
            logger.error(f"get_trading_fee({exchange}): {e}")
            return {}

    # ── 5. Create Order ───────────────────────────────────────────────────────

    async def create_order(
        self,
        exchange:      str,
        base_coin:     str,
        side:          str,
        price:         float,
        quantity:      float,
        symbol:        Optional[str] = None,
        strategy_name: Optional[str] = None,
    ) -> dict:
        """
        POST /trade/api/v2/order

        Place a LIMIT spot order.  Only LIMIT orders are supported by the API.

        The symbol is derived automatically from `base_coin` + `exchange`:
            coinswitchx/wazirx → BTC/INR
            c2c1/c2c2          → BTC/USDT

        You may also supply `symbol` directly, in which case it is validated
        against the exchange's expected quote currency.

        Args:
            exchange:      "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
            base_coin:     e.g. "BTC", "ETH", "SHIB"
            side:          "buy" | "sell" (case-insensitive)
            price:         Limit price
            quantity:      Base quantity
            symbol:        Optional override (validated against exchange)
            strategy_name: Tag for the originating strategy

        Returns:
            Full API response dict with order_id, status, etc.

        Raises:
            ValueError         – invalid exchange or symbol/exchange mismatch
            CoinSwitchAPIError – API rejected the order (4xx / 5xx)
        """
        ex     = exchange.lower().strip()
        sym    = symbol.upper() if symbol else build_symbol(base_coin, ex)
        side_l = side.lower()

        validate_symbol_exchange(sym, ex)

        # ── DB record ─────────────────────────────────────────────────────────
        db_order = Order(
            symbol      = sym,
            market_type = MarketType.SPOT,
            side        = OrderSide(side_l.upper()),
            order_type  = OrderType.LIMIT,
            quantity    = quantity,
            price       = price,
            strategy_name = strategy_name,
        )

        # ── Simulation ────────────────────────────────────────────────────────
        if settings.simulation_mode:
            db_order.status           = OrderStatus.SIMULATED
            db_order.simulated        = True
            db_order.filled_quantity  = quantity
            db_order.avg_fill_price   = price
            db_order.exchange_order_id = f"SIM-{int(datetime.utcnow().timestamp())}"
            async with get_db_context() as db:
                db.add(db_order)
                await db.flush()
            logger.info(f"[SIM] {side_l.upper()} {quantity} {sym} @ {price} on {ex}")
            return self._order_to_dict(db_order)

        # ── Live order ────────────────────────────────────────────────────────
        payload = {
            "side":     side_l,
            "symbol":   sym,
            "type":     "limit",
            "price":    price,
            "quantity": quantity,
            "exchange": ex,
        }

        client = await get_api_client()
        try:
            resp = await client.post("/trade/api/v2/order", payload=payload)
            order_data = resp.get("data", {})

            db_order.exchange_order_id = order_data.get("order_id")
            db_order.status            = map_api_status(order_data.get("status", "OPEN"))
            db_order.raw_response      = order_data

            async with get_db_context() as db:
                db.add(db_order)

            logger.info(
                f"Order placed: {side_l.upper()} {quantity} {sym} @ {price} "
                f"on {ex} | ID: {db_order.exchange_order_id}"
            )
            return order_data

        except CoinSwitchAPIError as e:
            db_order.status = OrderStatus.REJECTED
            async with get_db_context() as db:
                db.add(db_order)
            logger.error(f"Order rejected [{e.status_code}]: {e}")
            raise

    # ── 6. Cancel Order ───────────────────────────────────────────────────────

    async def cancel_order(self, order_id: str) -> dict:
        """
        DELETE /trade/api/v2/order

        Cancel an open order by order_id.

        Args:
            order_id: UUID returned by create_order

        Returns:
            API response dict with updated status
        """
        if settings.simulation_mode:
            async with get_db_context() as db:
                result = await db.execute(
                    select(Order).where(Order.exchange_order_id == order_id)
                )
                order = result.scalar_one_or_none()
                if order:
                    order.status = OrderStatus.CANCELLED
            return {"order_id": order_id, "status": "CANCELLED"}

        payload = {"order_id": order_id}
        client  = await get_api_client()
        try:
            resp       = await client.delete("/trade/api/v2/order", payload=payload)
            order_data = resp.get("data", {})

            # Sync DB
            async with get_db_context() as db:
                result = await db.execute(
                    select(Order).where(Order.exchange_order_id == order_id)
                )
                order = result.scalar_one_or_none()
                if order:
                    order.status = map_api_status(order_data.get("status", "CANCELLED"))

            logger.info(f"Order cancelled: {order_id}")
            return order_data

        except Exception as e:
            logger.error(f"cancel_order({order_id}): {e}")
            raise

    # ── 7. Get Order ──────────────────────────────────────────────────────────

    async def get_order(self, order_id: str) -> dict:
        """
        GET /trade/api/v2/order

        Fetch the current state of a single order.

        Args:
            order_id: UUID of the order

        Returns:
            API response dict with status, filled qty, average price, etc.
        """
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/order",
                params={"order_id": order_id},
            )
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_order({order_id}): {e}")
            return {}

    # ── 8. Open Orders ────────────────────────────────────────────────────────

    async def get_open_orders(
        self,
        count:     int          = 25,
        side:      Optional[str] = None,
        symbols:   Optional[str] = None,
        exchanges: Optional[str] = None,
        from_time: Optional[int] = None,
        to_time:   Optional[int] = None,
    ) -> list[dict]:
        """
        GET /trade/api/v2/orders   (open=True)

        Returns currently open / partially executed orders.

        Args:
            count:     Max results (default 25)
            side:      "buy" | "sell" | None
            symbols:   Comma-separated, e.g. "BTC/INR,ETH/INR"
            exchanges: Comma-separated, e.g. "coinswitchx,wazirx"
            from_time: Start timestamp in milliseconds
            to_time:   End timestamp in milliseconds

        Returns:
            List of order dicts
        """
        params: dict = {"open": True, "count": count, "type": "limit"}
        if side:
            params["side"]      = side.lower()
        if symbols:
            params["symbols"]   = symbols.lower()
        if exchanges:
            params["exchanges"] = exchanges.lower()
        if from_time:
            params["from_time"] = from_time
        if to_time:
            params["to_time"]   = to_time

        client = await get_api_client()
        try:
            resp = await client.get("/trade/api/v2/orders", params=params)
            return resp.get("data", {}).get("orders", [])
        except Exception as e:
            logger.error(f"get_open_orders: {e}")
            return []

    # ── 9. Closed Orders ─────────────────────────────────────────────────────

    async def get_closed_orders(
        self,
        count:     int           = 500,
        side:      Optional[str] = None,
        symbols:   Optional[str] = None,
        exchanges: Optional[str] = None,
        status:    Optional[str] = None,
        from_time: Optional[int] = None,
        to_time:   Optional[int] = None,
    ) -> list[dict]:
        """
        GET /trade/api/v2/orders   (open=False)

        Returns completed, cancelled, or expired orders.

        Args:
            count:     Max results (default 500)
            side:      "buy" | "sell" | None
            symbols:   Comma-separated, e.g. "BTC/INR,ETH/INR"
            exchanges: Comma-separated, e.g. "coinswitchx,wazirx"
            status:    Filter by status, e.g. "EXECUTED" | "CANCELLED"
            from_time: Start timestamp in milliseconds
            to_time:   End timestamp in milliseconds

        Returns:
            List of order dicts
        """
        params: dict = {"open": False, "count": count, "type": "limit"}
        if side:
            params["side"]      = side.lower()
        if symbols:
            params["symbols"]   = symbols.lower()
        if exchanges:
            params["exchanges"] = exchanges.lower()
        if status:
            params["status"]    = status.upper()
        if from_time:
            params["from_time"] = from_time
        if to_time:
            params["to_time"]   = to_time

        client = await get_api_client()
        try:
            resp = await client.get("/trade/api/v2/orders", params=params)
            return resp.get("data", {}).get("orders", [])
        except Exception as e:
            logger.error(f"get_closed_orders: {e}")
            return []

    # ── 10. Portfolio ──────────────────────────────────────────────────────────

    async def get_portfolio(self) -> list[dict]:
        """
        GET /trade/api/v2/user/portfolio

        Returns the user's balances across all currencies, including
        INR, USDT, and all held coins.

        Returns:
            List of {currency, main_balance, blocked_balance_order,
                     buy_average_price, current_value, ...}
        """
        client = await get_api_client()
        try:
            resp = await client.get("/trade/api/v2/user/portfolio")
            return resp.get("data", [])
        except Exception as e:
            logger.error(f"get_portfolio: {e}")
            return []

    # ── 11. TDS ───────────────────────────────────────────────────────────────

    async def get_tds(self) -> dict:
        """
        GET /trade/api/v2/tds

        Returns the total TDS (Tax Deducted at Source) deducted for
        the current financial year.

        Returns:
            {total_tds_amount, financial_year}
        """
        client = await get_api_client()
        try:
            resp = await client.get("/trade/api/v2/tds")
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_tds: {e}")
            return {}

    # ── 12. Trades (recent executions) ───────────────────────────────────────

    async def get_trades(self, exchange: str, symbol: str) -> list[dict]:
        """
        GET /trade/api/v2/trades

        Returns recent trade executions for a symbol on an exchange.

        Args:
            exchange: "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
            symbol:   e.g. "BTC/INR" or "BTC/USDT"

        Returns:
            List of {t (trade_id), p (price), q (qty), m (is_buyer_maker),
                     E (event_time), s (symbol), e (exchange)}
        """
        validate_symbol_exchange(symbol, exchange)
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/trades",
                params={
                    "exchange": exchange.lower(),
                    "symbol":   symbol.upper(),
                },
            )
            return resp.get("data", [])
        except Exception as e:
            logger.error(f"get_trades({exchange}, {symbol}): {e}")
            raise

    # ── 13. Depth (order book) ────────────────────────────────────────────────

    async def get_depth(self, exchange: str, symbol: str) -> dict:
        """
        GET /trade/api/v2/depth

        Returns the order book (bids + asks) for a symbol.

        Args:
            exchange: "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
            symbol:   e.g. "BTC/INR" or "BTC/USDT"

        Returns:
            {symbol, timestamp, bids: [[price, qty], ...],
                                asks: [[price, qty], ...]}
        """
        validate_symbol_exchange(symbol, exchange)
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/depth",
                params={
                    "exchange": exchange.lower(),
                    "symbol":   symbol.upper(),
                },
            )
            return resp.get("data", {"bids": [], "asks": []})
        except Exception as e:
            logger.error(f"get_depth({exchange}, {symbol}): {e}")
            raise

    # ── 14. Candles ───────────────────────────────────────────────────────────

    async def get_candles(
        self,
        exchange:   str,
        symbol:     str,
        interval:   int,
        start_time: int,
        end_time:   int,
    ) -> list[dict]:
        """
        GET /trade/api/v2/candles

        Returns OHLCV candlestick data.

        Args:
            exchange:   "coinswitchx" | "wazirx" | "c2c1" | "c2c2"
            symbol:     e.g. "BTC/INR" or "BTC/USDT"
            interval:   Candle duration in minutes (e.g. 1, 5, 15, 60, 1440)
            start_time: Start epoch in milliseconds
            end_time:   End epoch in milliseconds

        Returns:
            List of {o, h, l, c, volume, start_time, close_time, interval, symbol}
        """
        validate_symbol_exchange(symbol, exchange)
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/candles",
                params={
                    "exchange":   exchange.lower(),
                    "symbol":     symbol.upper(),
                    "interval":   str(interval),
                    "start_time": str(start_time),
                    "end_time":   str(end_time),
                },
            )
            # CoinSwitch returns candle array under "data" key
            candles = resp.get("data", resp.get("result", []))
            logger.debug(f"get_candles: got {len(candles)} candles for {symbol}")
            return candles
        except Exception as e:
            logger.error(f"get_candles({exchange}, {symbol}): {e}")
            raise  # re-raise so routes can return a meaningful HTTP error

    # ── 15a. Ticker — all pairs ───────────────────────────────────────────────

    async def get_ticker_all_pairs(self, exchange: str) -> dict:
        """
        GET /trade/api/v2/24hr/all-pairs/ticker

        Returns 24-hour ticker data for every pair on the exchange.

        Args:
            exchange: "coinswitchx" | "wazirx" | "c2c1" | "c2c2"

        Returns:
            {symbol: {openPrice, highPrice, lowPrice, lastPrice,
                      baseVolume, quoteVolume, percentageChange,
                      bidPrice, askPrice, at}}
        """
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/24hr/all-pairs/ticker",
                params={"exchange": exchange.lower()},
            )
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_ticker_all_pairs({exchange}): {e}")
            return {}

    # ── 15b. Ticker — specific symbol ────────────────────────────────────────

    async def get_ticker(
        self,
        symbol:    str,
        exchanges: str,
    ) -> dict:
        """
        GET /trade/api/v2/24hr/ticker

        Returns 24-hour ticker data for a specific symbol across one or more
        exchanges.

        Args:
            symbol:    e.g. "BTC/INR" or "BTC/USDT"
            exchanges: Comma-separated, e.g. "coinswitchx,wazirx"

        Returns:
            {symbol: {openPrice, highPrice, lowPrice, lastPrice, ...}}
        """
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/24hr/ticker",
                params={
                    "symbol":   symbol.upper(),
                    "exchange": exchanges.lower(),
                },
            )
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_ticker({symbol}, {exchanges}): {e}")
            return {}

    # ── Convenience helpers ───────────────────────────────────────────────────

    async def get_best_price(self, exchange: str, symbol: str) -> dict:
        """
        Return the current best bid and ask from the order book.

        Returns:
            {bid: float, ask: float, mid: float, spread_pct: float}
        """
        book = await self.get_depth(exchange, symbol)
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        bid  = float(bids[0][0]) if bids else 0.0
        ask  = float(asks[0][0]) if asks else 0.0
        mid  = (bid + ask) / 2 if bid and ask else 0.0
        spread_pct = ((ask - bid) / mid * 100) if mid else 0.0
        return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": round(spread_pct, 4)}

    async def get_balance(self, currency: str) -> float:
        """
        Return the available main balance for a single currency from the
        portfolio.

        Args:
            currency: e.g. "BTC", "ETH", "INR", "USDT"

        Returns:
            Available balance as a float (0.0 if not held)
        """
        portfolio = await self.get_portfolio()
        for item in portfolio:
            if item.get("currency", "").upper() == currency.upper():
                return float(item.get("main_balance", 0.0))
        return 0.0

    # ── DB ↔ dict helper ──────────────────────────────────────────────────────

    def _order_to_dict(self, order: Order) -> dict:
        return {
            "id":                 order.id,
            "exchange_order_id":  order.exchange_order_id,
            "symbol":             order.symbol,
            "side":               order.side.value,
            "order_type":         order.order_type.value,
            "quantity":           order.quantity,
            "price":              order.price,
            "filled_quantity":    order.filled_quantity,
            "avg_fill_price":     order.avg_fill_price,
            "status":             order.status.value,
            "strategy_name":      order.strategy_name,
            "simulated":          order.simulated,
            "created_at":         order.created_at.isoformat() if order.created_at else None,
        }


# ── Module singleton ──────────────────────────────────────────────────────────

spot_trading = SpotTradingService()
