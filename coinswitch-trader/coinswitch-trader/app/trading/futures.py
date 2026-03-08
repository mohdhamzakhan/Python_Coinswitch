"""
Futures Trading Module - leverage, long/short, stop-loss, take-profit.
"""
from typing import Optional
from datetime import datetime
from sqlalchemy import select
from app.api.client import get_api_client, CoinSwitchAPIError
from app.models.db_models import (
    Order, Position, OrderSide, OrderType, OrderStatus,
    MarketType, PositionSide
)
from app.database.session import get_db_context
from config.settings import get_settings
from loguru import logger

settings = get_settings()


class FuturesTradingService:
    """
    Handles futures trading operations:
      - Long/short positions with leverage
      - Stop loss and take profit
      - Reduce-only orders for partial/full close
    """

    async def place_futures_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        leverage: int = 1,
        reduce_only: bool = False,
        position_side: str = "BOTH",
        strategy_name: Optional[str] = None,
    ) -> dict:
        """
        Place a futures order.
        
        Args:
            symbol: e.g. "BTC/USDT"
            side: "BUY" (long) or "SELL" (short)
            quantity: Contract quantity
            order_type: MARKET, LIMIT, STOP_MARKET, TAKE_PROFIT_MARKET
            price: Limit price
            stop_price: Trigger for stop/TP orders
            leverage: 1-125x
            reduce_only: True to only reduce existing position
            position_side: BOTH, LONG, SHORT (for hedge mode)
            strategy_name: Strategy label
        """
        db_order = Order(
            symbol=symbol,
            market_type=MarketType.FUTURES,
            side=OrderSide(side.upper()),
            order_type=OrderType(order_type.upper()),
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            leverage=leverage,
            reduce_only=reduce_only,
            strategy_name=strategy_name,
        )

        if settings.simulation_mode:
            db_order.status = OrderStatus.SIMULATED
            db_order.simulated = True
            db_order.filled_quantity = quantity
            db_order.avg_fill_price = price or 0.0
            db_order.exchange_order_id = f"FSIM-{int(datetime.utcnow().timestamp())}"

            async with get_db_context() as db:
                db.add(db_order)
                await db.flush()
                # Create simulated position
                await self._update_sim_position(db, symbol, side, quantity, price or 0.0, leverage, strategy_name)
                result = self._order_to_dict(db_order)
            logger.info(f"[SIMULATION] Futures order: {symbol} {side} x{leverage} {quantity} @ {price}")
            return result

        # Set leverage first
        await self.set_leverage(symbol, leverage)

        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
            "positionSide": position_side.upper(),
            "reduceOnly": reduce_only,
        }
        if price:
            payload["price"] = str(price)
        if stop_price:
            payload["stopPrice"] = str(stop_price)

        client = await get_api_client()
        try:
            resp = await client.post("/trade/api/v2/futures/order", data=payload)
            order_data = resp.get("data", {})
            db_order.exchange_order_id = order_data.get("orderId")
            db_order.status = OrderStatus.OPEN
            db_order.raw_response = order_data
            async with get_db_context() as db:
                db.add(db_order)
            logger.info(f"Futures order placed: {symbol} {side} x{leverage} | ID: {db_order.exchange_order_id}")
            return order_data
        except CoinSwitchAPIError as e:
            db_order.status = OrderStatus.REJECTED
            async with get_db_context() as db:
                db.add(db_order)
            logger.error(f"Futures order rejected: {e}")
            raise

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for a futures symbol."""
        if settings.simulation_mode:
            return {"symbol": symbol, "leverage": leverage}
        client = await get_api_client()
        try:
            resp = await client.post(
                "/trade/api/v2/futures/leverage",
                data={"symbol": symbol, "leverage": leverage},
            )
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"set_leverage({symbol}, {leverage}): {e}")
            return {}

    async def get_futures_positions(self, symbol: Optional[str] = None) -> list:
        """Get open futures positions from exchange."""
        if settings.simulation_mode:
            return await self._get_sim_positions(symbol)
        client = await get_api_client()
        params = {}
        if symbol:
            params["symbol"] = symbol
        try:
            resp = await client.get("/trade/api/v2/futures/positions", params=params)
            return resp.get("data", [])
        except Exception as e:
            logger.error(f"get_futures_positions: {e}")
            return []

    async def close_position(
        self,
        symbol: str,
        side: str,
        quantity: Optional[float] = None,
    ) -> dict:
        """
        Close a futures position fully or partially.
        
        side: "LONG" (sell to close) or "SHORT" (buy to close)
        quantity: Partial close amount; None = full close
        """
        close_side = "SELL" if side.upper() == "LONG" else "BUY"

        if not quantity:
            # Get current position size
            positions = await self.get_futures_positions(symbol)
            for pos in positions:
                if pos.get("symbol") == symbol:
                    quantity = float(pos.get("positionAmt", 0))
                    break

        if not quantity or quantity == 0:
            return {"message": "No position to close"}

        return await self.place_futures_order(
            symbol=symbol,
            side=close_side,
            quantity=abs(quantity),
            order_type="MARKET",
            reduce_only=True,
            strategy_name="manual_close",
        )

    async def cancel_futures_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open futures order."""
        if settings.simulation_mode:
            return {"orderId": order_id, "status": "CANCELLED"}
        client = await get_api_client()
        try:
            resp = await client.delete(
                "/trade/api/v2/futures/order",
                data={"orderId": order_id, "symbol": symbol},
            )
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"cancel_futures_order({order_id}): {e}")
            raise

    async def get_futures_orders(self, symbol: Optional[str] = None) -> list:
        """Get open futures orders."""
        if settings.simulation_mode:
            return []
        client = await get_api_client()
        params = {}
        if symbol:
            params["symbol"] = symbol
        try:
            resp = await client.get("/trade/api/v2/futures/orders", params=params)
            return resp.get("data", [])
        except Exception as e:
            logger.error(f"get_futures_orders: {e}")
            return []

    async def get_futures_account(self) -> dict:
        """Get futures account info (balance, margin, PnL)."""
        if settings.simulation_mode:
            return {"balance": 10000, "unrealizedPnl": 0, "marginBalance": 10000}
        client = await get_api_client()
        try:
            resp = await client.get("/trade/api/v2/futures/account")
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_futures_account: {e}")
            return {}

    # ── Simulation helpers ───────────────────────────────────────────────────

    async def _update_sim_position(self, db, symbol, side, quantity, price, leverage, strategy_name):
        """Update or create a simulated position in the DB."""
        pos_side = PositionSide.LONG if side.upper() == "BUY" else PositionSide.SHORT
        result = await db.execute(
            select(Position).where(
                Position.symbol == symbol,
                Position.market_type == MarketType.FUTURES,
                Position.is_open == True,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.quantity += quantity
            existing.entry_price = (existing.entry_price + price) / 2
        else:
            pos = Position(
                symbol=symbol,
                market_type=MarketType.FUTURES,
                side=pos_side,
                entry_price=price,
                quantity=quantity,
                leverage=leverage,
                strategy_name=strategy_name,
            )
            db.add(pos)

    async def _get_sim_positions(self, symbol=None) -> list:
        async with get_db_context() as db:
            query = select(Position).where(
                Position.market_type == MarketType.FUTURES,
                Position.is_open == True,
            )
            if symbol:
                query = query.where(Position.symbol == symbol)
            result = await db.execute(query)
            positions = result.scalars().all()
            return [
                {
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "entryPrice": p.entry_price,
                    "positionAmt": p.quantity,
                    "leverage": p.leverage,
                    "unrealizedPnl": p.unrealized_pnl,
                    "simulated": True,
                }
                for p in positions
            ]

    def _order_to_dict(self, order: Order) -> dict:
        return {
            "id": order.id,
            "exchange_order_id": order.exchange_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "quantity": order.quantity,
            "price": order.price,
            "leverage": order.leverage,
            "reduce_only": order.reduce_only,
            "status": order.status.value,
            "strategy_name": order.strategy_name,
            "simulated": order.simulated,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }


# Module singleton
futures_trading = FuturesTradingService()
