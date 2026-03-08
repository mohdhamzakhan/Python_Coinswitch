"""
Task Scheduler using APScheduler.
Runs strategies at configurable intervals.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.strategies.engine import strategy_engine
from loguru import logger

scheduler = AsyncIOScheduler(timezone="UTC")


def setup_scheduler():
    """Configure scheduled tasks."""

    # Run all enabled strategies every 30 seconds
    scheduler.add_job(
        strategy_engine.run_all,
        trigger=IntervalTrigger(seconds=30),
        id="run_strategies",
        name="Run All Strategies",
        replace_existing=True,
        max_instances=1,  # Prevent overlap
    )

    logger.info("Scheduler configured: strategies run every 30s")


async def start_scheduler():
    """Start the scheduler."""
    setup_scheduler()
    scheduler.start()
    logger.info("Scheduler started")


async def stop_scheduler():
    """Stop the scheduler gracefully."""
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


def update_strategy_interval(seconds: int):
    """Update strategy run interval."""
    scheduler.reschedule_job(
        "run_strategies",
        trigger=IntervalTrigger(seconds=seconds),
    )
    logger.info(f"Strategy interval updated to {seconds}s")
