"""
Stale-job sweeper.

BackgroundTasks runs in-process and isn't durable — if a worker restarts
mid-analysis, the session is orphaned in "processing" forever. This periodic
sweep flips anything stuck past the threshold to "failed" so the frontend stops
polling and the user can retry. Cheap janitor, runs on a timer from the app lifespan.
"""

import asyncio
import logging

from ..config.settings import Settings
from ..infrastructure.snowflake.client import create_snowflake_connection
from ..infrastructure.snowflake.repositories.sessions import SessionRepository
from .dependencies import _snowflake_config, get_mock_snowflake_connection

logger = logging.getLogger(__name__)


async def sweep_stale_jobs_once(settings: Settings) -> int:
    """Run a single sweep. Builds its own repo (no request scope here). Returns count flipped."""
    threshold = settings.stale_job_threshold_minutes

    if settings.snowflake_mock_mode:
        repo = SessionRepository(get_mock_snowflake_connection())
        return repo.fail_stale_processing(threshold)

    with create_snowflake_connection(config=_snowflake_config(settings)) as conn:
        return SessionRepository(conn).fail_stale_processing(threshold)


async def sweeper_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    """Sweep on an interval until told to stop. Survives individual sweep failures."""
    interval = settings.sweeper_interval_seconds
    logger.info(
        "Stale-job sweeper started",
        extra={"interval_seconds": interval, "threshold_minutes": settings.stale_job_threshold_minutes},
    )

    while not stop_event.is_set():
        try:
            count = await sweep_stale_jobs_once(settings)
            if count:
                logger.warning("Sweeper failed %d stale job(s)", count)
        except Exception as e:
            # A DB hiccup shouldn't kill the loop — just skip this tick.
            logger.warning("Sweeper tick failed", extra={"error": str(e)})

        # Sleep, but wake immediately on shutdown.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Stale-job sweeper stopped")
