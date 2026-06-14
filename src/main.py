"""
FastAPI application entry point.

App factory pattern — see create_app().
Dev: uvicorn src.main:app --reload
Prod: gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import analysis, health, sessions, video
from .api.sweeper import sweeper_loop
from .config.settings import get_settings

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    settings = get_settings()
    
    logger.info(
        "SwimCoach API starting",
        extra={
            "version": settings.api_version,
            "mock_mode": {
                "snowflake": settings.snowflake_mock_mode,
                "r2": settings.r2_mock_mode,
            }
        }
    )
    
    missing_fields = settings.validate_required_fields()
    if missing_fields:
        logger.error(
            "Missing required configuration",
            extra={"missing_fields": missing_fields}
        )
        # TODO: fix later - should fail fast in prod, log-and-continue in dev

    # Start the stale-job sweeper (unsticks orphaned "processing" sessions).
    sweeper_stop: asyncio.Event | None = None
    sweeper_task: asyncio.Task | None = None
    if settings.sweeper_enabled:
        sweeper_stop = asyncio.Event()
        sweeper_task = asyncio.create_task(sweeper_loop(settings, sweeper_stop))

    yield

    if sweeper_task is not None:
        sweeper_stop.set()
        try:
            await asyncio.wait_for(sweeper_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            sweeper_task.cancel()

    logger.info("SwimCoach API shutting down")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description="""
        AI-powered swim technique analysis platform.
        
        ## Features
        
        - Upload video frames for analysis
        - Get personalized coaching feedback from AI
        - Ask follow-up questions about your technique
        - Track coaching sessions over time
        
        ## Authentication
        
        All endpoints require an API key provided in the `X-API-Key` header.
        
        ## Workflow
        
        1. **Upload frames**: `POST /api/v1/analysis/upload`
           - Upload 10-20 frames extracted from your swimming video
           - Receive a session ID
        
        2. **Request analysis**: `POST /api/v1/analysis/{session_id}/analyze`
           - AI analyzes your technique
           - Returns detailed coaching feedback
        
        3. **Ask questions**: `POST /api/v1/sessions/{session_id}/chat`
           - Continue the conversation with follow-up questions
           - Get targeted advice based on your specific video
        
        4. **Review history**: `GET /api/v1/sessions/{session_id}`
           - Retrieve full session with all messages
        """,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    app.include_router(
        health.router,
        prefix="/health",
        tags=["Health"],
    )
    
    app.include_router(
        analysis.router,
        prefix="/api/v1/analysis",
        tags=["Analysis"],
    )
    
    app.include_router(
        sessions.router,
        prefix="/api/v1/sessions",
        tags=["Sessions"],
    )
    
    app.include_router(
        video.router,
        prefix="/api/v1/video",
        tags=["Video Analysis"],
    )
    
    @app.get("/", include_in_schema=False)
    async def root():
        """Root endpoint - redirect to docs."""
        return {
            "message": "SwimCoach AI API",
            "version": settings.api_version,
            "docs": "/docs",
            "health": "/health",
        }
    
    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        """Catch-all — log full error, return generic message to client."""
        logger.error(
            "Unhandled exception",
            extra={
                "path": request.url.path,
                "method": request.method,
                "error": str(exc),
            },
            exc_info=exc,
        )
        
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error. Please contact support if this persists."
            }
        )
    
    logger.info(
        "FastAPI application created",
        extra={
            "title": settings.api_title,
            "version": settings.api_version,
        }
    )
    
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level=settings.log_level.lower(),
    )

