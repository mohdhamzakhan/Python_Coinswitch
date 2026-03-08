"""
Spot Trading Module - place, cancel, and query spot orders.
Supports market, limit, stop-loss, and take-profit orders.
"""
from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.client import get_api_client, CoinSwitchAPIError
from app.models.db_models import Order, OrderSide, OrderType, OrderStatus, MarketType
from app.database.session import get_db_context
from config.settings import get_settings
from loguru import logger

settings = get_settings()


class SpotTradingService:
    """
    Handles all spot trading operations.
    
    In simulation mode, orders are saved to DB without hitting the exchange.
    """

    async def get_account_info(self) -> dict:
        """Get spot account balances."""
        client = await get_api_client()
        try:
            resp = await client.get("/trade/api/v2/user/portfolio")
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_account_info: {e}")
            return {}

    async def place_spot_order(
        self,
        symbol: str,
        side: str,              # BUY or SELL
        quantity: float,
        order_type: str = "LIMIT",
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        strategy_name: Optional[str] = None,
    ) -> dict:
        """
        Place a spot order.
        
        Args:
            symbol: Trading pair, e.g. "BTC/USDT"
            side: "BUY" or "SELL"
            quantity: Amount of base asset
            order_type: LIMIT, MARKET, STOP_LIMIT, STOP_MARKET
            price: Limit price (required for LIMIT orders)
            stop_price: Trigger price for stop orders
            strategy_name: Label for this order's origin strategy
        
        Returns:
            Dict with order details
        """
        # Build order record
        db_order = Order(
            symbol=symbol,
            market_type=MarketType.SPOT,
            side=OrderSide(side.upper()),
            order_type=OrderType(order_type.upper()),
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            strategy_name=strategy_name,
        )

        if settings.simulation_mode:
            db_order.status = OrderStatus.SIMULATED
            db_order.simulated = True
            # Simulate fill at current price
            db_order.filled_quantity = quantity
            db_order.avg_fill_price = price or 0.0
            db_order.exchange_order_id = f"SIM-{int(datetime.utcnow().timestamp())}"

            async with get_db_context() as db:
                db.add(db_order)
                await db.flush()
                result = self._order_to_dict(db_order)

            logger.info(f"[SIMULATION] Spot order placed: {symbol} {side} {quantity} @ {price}")
            return result

        # Real order
        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }
        if price and order_type.upper() in ("LIMIT", "STOP_LIMIT"):
            payload["price"] = str(price)
        if stop_price:
            payload["stopPrice"] = str(stop_price)

        client = await get_api_client()
        try:
            resp = await client.post("/trade/api/v2/order", data=payload)
            order_data = resp.get("data", {})

            db_order.exchange_order_id = order_data.get("orderId")
            db_order.status = OrderStatus.OPEN
            db_order.raw_response = order_data

            async with get_db_context() as db:
                db.add(db_order)

            logger.info(f"Spot order placed: {symbol} {side} {quantity} @ {price} | ID: {db_order.exchange_order_id}")
            return order_data

        except CoinSwitchAPIError as e:
            db_order.status = OrderStatus.REJECTED
            async with get_db_context() as db:
                db.add(db_order)
            logger.error(f"Spot order rejected: {e}")
            raise

    async def cancel_spot_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open spot order."""
        if settings.simulation_mode:
            async with get_db_context() as db:
                result = await db.execute(
                    select(Order).where(Order.exchange_order_id == order_id)
                )
                order = result.scalar_one_or_none()
                if order:
                    order.status = OrderStatus.CANCELLED
            return {"orderId": order_id, "status": "CANCELLED"}

        client = await get_api_client()
        try:
            resp = await client.delete(
                "/trade/api/v2/order",
                data={"orderId": order_id, "symbol": symbol},
            )
            # Update DB
            async with get_db_context() as db:
                result = await db.execute(
                    select(Order).where(Order.exchange_order_id == order_id)
                )
                order = result.scalar_one_or_none()
                if order:
                    order.status = OrderStatus.CANCELLED
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"cancel_spot_order({order_id}): {e}")
            raise

    async def get_spot_orders(self, symbol: Optional[str] = None, status: Optional[str] = None) -> list:
        """Get open/recent spot orders from the exchange."""
        client = await get_api_client()
        params = {}
        if symbol:
            params["symbol"] = symbol
        if status:
            params["status"] = status
        try:
            resp = await client.get("/trade/api/v2/orders", params=params)
            return resp.get("data", [])
        except Exception as e:
            logger.error(f"get_spot_orders: {e}")
            return []

    async def get_spot_positions(self) -> dict:
        """Get spot wallet balances (non-zero positions)."""
        return await self.get_account_info()

    async def get_order_status(self, order_id: str, symbol: str) -> dict:
        """Get the current status of a specific order."""
        client = await get_api_client()
        try:
            resp = await client.get(
                "/trade/api/v2/order",
                params={"orderId": order_id, "symbol": symbol},
            )
            return resp.get("data", {})
        except Exception as e:
            logger.error(f"get_order_status({order_id}): {e}")
            return {}

    # ── History from local DB ────────────────────────────────────────────────

    async def get_order_history(
        self,
        symbol: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get order history from local database."""
        async with get_db_context() as db:
            query = select(Order).where(Order.market_type == MarketType.SPOT)
            if symbol:
                query = query.where(Order.symbol == symbol)
            query = query.order_by(Order.created_at.desc()).limit(limit)
            result = await db.execute(query)
            orders = result.scalars().all()
            return [self._order_to_dict(o) for o in orders]

    def _order_to_dict(self, order: Order) -> dict:
        return {
            "id": order.id,
            "exchange_order_id": order.exchange_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "quantity": order.quantity,
            "price": order.price,
            "stop_price": order.stop_price,
            "filled_quantity": order.filled_quantity,
            "avg_fill_price": order.avg_fill_price,
            "status": order.status.value,
            "strategy_name": order.strategy_name,
            "simulated": order.simulated,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }


# Module singleton
spot_trading = SpotTradingService()
