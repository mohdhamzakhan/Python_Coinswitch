"""
Main FastAPI Application.
Entry point for the CoinSwitch Trading Platform.
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import get_settings
from app.database.session import init_db
from app.strategies.engine import strategy_engine
from app.services.scheduler import start_scheduler, stop_scheduler
from app.websocket.listener import ws_listener

# Import routers
from app.api.routes import (
    market_router, spot_router,
    strategy_router, settings_router, backtest_router,
    dashboard_router,
)

settings = get_settings()

# ── Logging setup ────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    level="DEBUG" if settings.debug else "INFO",
)
logger.add(
    "logs/trading.log",
    rotation="50 MB",
    retention="30 days",
    compression="zip",
    level="INFO",
)

os.makedirs("logs", exist_ok=True)


# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info(f"Starting {settings.app_name}")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Seed strategy configs
    await strategy_engine.seed_default_configs()

    # Start WebSocket listener in background
    ws_task = None
    if settings.coinswitch_api_key:
        ws_task = asyncio.create_task(ws_listener.start())
        logger.info("WebSocket listener started")
    else:
        logger.warning("No API key configured - WebSocket not started. Add credentials in Settings.")

    # Start scheduler
    await start_scheduler()

    logger.info(f"App running. Simulation mode: {settings.simulation_mode}")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await stop_scheduler()
    if ws_task:
        await ws_listener.stop()
        ws_task.cancel()


# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="Automated Cryptocurrency Trading Platform for CoinSwitch",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static files
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

    # API routers
    app.include_router(market_router, prefix="/api/market", tags=["Market Data"])
    app.include_router(spot_router, prefix="/api/spot", tags=["Spot Trading"])
    app.include_router(strategy_router, prefix="/api/strategies", tags=["Strategies"])
    app.include_router(settings_router, prefix="/api/settings", tags=["Settings"])
    app.include_router(backtest_router, prefix="/api/backtest", tags=["Backtesting"])

    # Dashboard HTML routes
    app.include_router(dashboard_router, tags=["Dashboard"])

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=settings.debug,
        workers=1,  # Single worker for WebSocket state
    )
