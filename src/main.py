"""
FastAPI application entry point.

This module creates and configures the FastAPI application.
Using an application factory pattern (create_app function) because:
- Easier to test with different configurations
- Explicit about initialization order
- Can create multiple app instances if needed (e.g., for testing)

For local development:
    uvicorn src.main:app --reload

For production:
    gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import analysis, health, sessions, users
from .config.settings import get_settings

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    This context manager runs on startup and shutdown, enabling:
    - Resource initialization (connection pools, caches)
    - Cleanup on shutdown (close connections, flush logs)
    
    FastAPI calls this automatically when the application starts/stops.
    """
    # Startup
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
    
    # Validate configuration
    missing_fields = settings.validate_required_fields()
    if missing_fields:
        logger.error(
            "Missing required configuration",
            extra={"missing_fields": missing_fields}
        )
        # In production, you might want to fail fast here
        # For development, we log the error but continue
    
    yield
    
    # Shutdown
    logger.info("SwimCoach API shutting down")


def create_app() -> FastAPI:
    """
    Application factory.
    
    Creates and configures the FastAPI application.
    This function is called once at startup (in production) or
    multiple times (in tests with different configurations).
    """
    settings = get_settings()
    
    # Create FastAPI instance
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
    
    # CORS middleware
    # Configure allowed origins via CORS_ORIGINS environment variable
    # Default: localhost (dev) + Cloudflare Pages (production)
    # Production: Set to your actual frontend domain(s)
    # Example: https://swimcoach.app,https://ai-swim-coach.pages.dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
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
        users.router,
        prefix="/api/v1/users",
        tags=["Users"],
    )
    
    # Root endpoint
    @app.get("/", include_in_schema=False)
    async def root():
        """Root endpoint - redirect to docs."""
        return {
            "message": "SwimCoach AI API",
            "version": settings.api_version,
            "docs": "/docs",
            "health": "/health",
        }
    
    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        """
        Catch-all exception handler.
        
        In production, this prevents stack traces from leaking to clients.
        We log the full error server-side but return a generic message.
        """
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


# Create the application instance
# This is what uvicorn/gunicorn will import
app = create_app()


# For debugging/development
if __name__ == "__main__":
    import uvicorn
    
    # Get settings to determine log level
    settings = get_settings()
    
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level=settings.log_level.lower(),
    )

