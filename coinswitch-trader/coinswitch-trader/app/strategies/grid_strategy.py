"""
Grid Trading Strategy.

Places buy and sell orders at fixed price intervals (grid levels)
within a defined price range. Profits from oscillating prices.

Parameters:
  lower_price: float  - Bottom of grid range
  upper_price: float  - Top of grid range
  grid_levels: int    - Number of grid levels (default 10)
  quantity_per_grid: float - Order size per grid
"""
from typing import Optional
from app.strategies.base import BaseStrategy
from app.services.market_data import market_data


class GridStrategy(BaseStrategy):
    name = "grid_trading"
    description = "Grid Trading - buy low sell high within a price range"

    def __init__(self, config: dict):
        super().__init__(config)
        self.lower_price = float(config.get("lower_price", 0))
        self.upper_price = float(config.get("upper_price", 0))
        self.grid_levels = int(config.get("grid_levels", 10))
        self.quantity_per_grid = float(config.get("quantity_per_grid", self.quantity))
        self._initialized = False
        self._grid_prices: list[float] = []
        self._active_orders: dict[float, dict] = {}  # price -> order

    def _compute_grid(self) -> list[float]:
        if self.upper_price <= self.lower_price:
            return []
        step = (self.upper_price - self.lower_price) / self.grid_levels
        return [round(self.lower_price + step * i, 8) for i in range(self.grid_levels + 1)]

    async def run(self) -> Optional[str]:
        current_price = await self.get_price()
        if current_price <= 0:
            return None

        if not self._grid_prices:
            if self.lower_price == 0 or self.upper_price == 0:
                # Auto-calculate range: ±10% from current price
                self.lower_price = current_price * 0.90
                self.upper_price = current_price * 1.10
            self._grid_prices = self._compute_grid()
            self.log(
                f"Grid initialized: {len(self._grid_prices)} levels "
                f"[{self.lower_price:.2f} - {self.upper_price:.2f}]"
            )

        if not self._initialized:
            await self._place_initial_grid(current_price)
            self._initialized = True
            return None

        # Check if any grid orders were filled and place counter-orders
        await self._check_and_replace_orders(current_price)
        return None

    async def _place_initial_grid(self, current_price: float):
        """Place initial grid buy/sell orders."""
        placed = 0
        for price in self._grid_prices:
            if price < current_price:
                # Buy below current price
                order = await self.buy(
                    quantity=self.quantity_per_grid,
                    price=price,
                )
                if order:
                    self._active_orders[price] = {"order": order, "side": "BUY"}
                    placed += 1
            elif price > current_price:
                # Sell above current price
                order = await self.sell(
                    quantity=self.quantity_per_grid,
                    price=price,
                )
                if order:
                    self._active_orders[price] = {"order": order, "side": "SELL"}
                    placed += 1
        self.log(f"Placed {placed} initial grid orders")

    async def _check_and_replace_orders(self, current_price: float):
        """
        In a real implementation this would check order fill status via WebSocket.
        This is a simplified version that checks price proximity.
        """
        # This is simplified - in production, use WebSocket fills
        for price, order_info in list(self._active_orders.items()):
            # If price crossed a grid level, assume filled and place counter-order
            if order_info["side"] == "BUY" and current_price <= price * 1.001:
                # BUY was filled, place SELL at next grid up
                next_price = price + (self._grid_prices[1] - self._grid_prices[0])
                if next_price <= self.upper_price:
                    await self.sell(quantity=self.quantity_per_grid, price=next_price)
                    self.log(f"Grid: BUY filled at {price:.4f}, placed SELL at {next_price:.4f}")

            elif order_info["side"] == "SELL" and current_price >= price * 0.999:
                # SELL was filled, place BUY at next grid down
                step = self._grid_prices[1] - self._grid_prices[0]
                next_price = price - step
                if next_price >= self.lower_price:
                    await self.buy(quantity=self.quantity_per_grid, price=next_price)
                    self.log(f"Grid: SELL filled at {price:.4f}, placed BUY at {next_price:.4f}")
