"""
Backtesting Module.
Runs a strategy against historical candle data and returns performance metrics.
"""
import pandas as pd
import numpy as np
from typing import Type, Optional
from dataclasses import dataclass, field
from app.strategies.base import BaseStrategy
from loguru import logger


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    total_pnl_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trades: list = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "period": f"{self.start_date} → {self.end_date}",
            "initial_capital": self.initial_capital,
            "final_capital": round(self.final_capital, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
        }


class BacktestEngine:
    """
    Simulated backtesting engine.
    
    Works by feeding historical candles to strategy signal logic
    without placing real orders.
    
    Note: This runs MA/RSI/Breakout signal detection on historical data.
    Grid and DCA strategies use a simplified backtest mode.
    """

    def __init__(
        self,
        candles: list[dict],
        initial_capital: float = 10000.0,
        fee_rate: float = 0.001,  # 0.1% taker fee
        slippage_pct: float = 0.0005,  # 0.05% slippage
    ):
        self.df = pd.DataFrame(candles)
        if not self.df.empty:
            col_map = {
                "openTime": "timestamp", "t": "timestamp",
                "o": "open", "h": "high", "l": "low",
                "c": "close", "v": "volume",
            }
            self.df = self.df.rename(columns={k: v for k, v in col_map.items() if k in self.df.columns})
            for col in ["open", "high", "low", "close", "volume"]:
                if col in self.df.columns:
                    self.df[col] = pd.to_numeric(self.df[col], errors="coerce")
            self.df = self.df.sort_values("timestamp").reset_index(drop=True)

        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct

    def run_ma_crossover(self, fast: int = 9, slow: int = 21, qty_pct: float = 0.1) -> BacktestResult:
        """Backtest MA crossover strategy."""
        df = self.df.copy()
        df["fast_ma"] = df["close"].rolling(fast).mean()
        df["slow_ma"] = df["close"].rolling(slow).mean()
        df["signal"] = 0
        df.loc[(df["fast_ma"] > df["slow_ma"]) & (df["fast_ma"].shift(1) <= df["slow_ma"].shift(1)), "signal"] = 1
        df.loc[(df["fast_ma"] < df["slow_ma"]) & (df["fast_ma"].shift(1) >= df["slow_ma"].shift(1)), "signal"] = -1
        return self._simulate_trades(df, "ma_crossover", qty_pct)

    def run_rsi(self, period: int = 14, oversold: float = 30, overbought: float = 70, qty_pct: float = 0.1) -> BacktestResult:
        """Backtest RSI strategy."""
        from app.strategies.rsi_strategy import _compute_rsi
        df = self.df.copy()
        df["rsi"] = _compute_rsi(df["close"], period)
        df["signal"] = 0
        df.loc[(df["rsi"] > oversold) & (df["rsi"].shift(1) <= oversold), "signal"] = 1
        df.loc[(df["rsi"] < overbought) & (df["rsi"].shift(1) >= overbought), "signal"] = -1
        return self._simulate_trades(df, "rsi_strategy", qty_pct)

    def run_breakout(self, lookback: int = 20, qty_pct: float = 0.1) -> BacktestResult:
        """Backtest breakout strategy."""
        df = self.df.copy()
        df["resistance"] = df["high"].shift(1).rolling(lookback).max()
        df["support"] = df["low"].shift(1).rolling(lookback).min()
        df["signal"] = 0
        df.loc[df["close"] > df["resistance"], "signal"] = 1
        df.loc[df["close"] < df["support"], "signal"] = -1
        return self._simulate_trades(df, "breakout", qty_pct)

    def _simulate_trades(self, df: pd.DataFrame, strategy_name: str, qty_pct: float = 0.1) -> BacktestResult:
        """Simulate trades based on signals."""
        capital = self.initial_capital
        position = 0.0
        entry_price = 0.0
        trades = []
        equity_curve = [capital]

        for i, row in df.iterrows():
            signal = row.get("signal", 0)
            price = row["close"]
            if pd.isna(price):
                continue

            exec_price = price * (1 + self.slippage_pct if signal == 1 else 1 - self.slippage_pct)

            # Buy signal and no position
            if signal == 1 and position == 0:
                qty = (capital * qty_pct) / exec_price
                cost = qty * exec_price * (1 + self.fee_rate)
                if cost <= capital:
                    position = qty
                    entry_price = exec_price
                    capital -= cost
                    trades.append({"type": "BUY", "price": exec_price, "qty": qty, "capital": capital})

            # Sell signal and have position
            elif signal == -1 and position > 0:
                proceeds = position * exec_price * (1 - self.fee_rate)
                pnl = proceeds - (position * entry_price)
                capital += proceeds
                trades.append({
                    "type": "SELL", "price": exec_price, "qty": position,
                    "pnl": pnl, "capital": capital
                })
                position = 0.0
                entry_price = 0.0

            equity_curve.append(capital + position * price)

        # Close any open position at last price
        if position > 0:
            last_price = df["close"].iloc[-1]
            proceeds = position * last_price * (1 - self.fee_rate)
            pnl = proceeds - (position * entry_price)
            capital += proceeds
            trades.append({"type": "CLOSE", "price": last_price, "qty": position, "pnl": pnl})

        # Metrics
        sell_trades = [t for t in trades if t.get("pnl") is not None]
        winning = [t for t in sell_trades if t["pnl"] > 0]
        losing = [t for t in sell_trades if t["pnl"] <= 0]

        total_pnl = capital - self.initial_capital
        win_rate = len(winning) / len(sell_trades) * 100 if sell_trades else 0
        gross_profit = sum(t["pnl"] for t in winning)
        gross_loss = abs(sum(t["pnl"] for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Drawdown
        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        drawdown = peak - eq
        max_dd = drawdown.max()
        max_dd_pct = (max_dd / peak.max() * 100) if peak.max() > 0 else 0

        # Sharpe ratio (simplified daily)
        returns = pd.Series(equity_curve).pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0

        ts = df["timestamp"]
        start_date = str(ts.iloc[0]) if len(ts) > 0 else ""
        end_date = str(ts.iloc[-1]) if len(ts) > 0 else ""

        return BacktestResult(
            strategy_name=strategy_name,
            symbol="",
            timeframe="",
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=capital,
            total_trades=len(sell_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_pnl=total_pnl,
            total_pnl_pct=(total_pnl / self.initial_capital * 100),
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=sharpe,
            trades=sell_trades[-50:],  # Last 50 trades
        )
