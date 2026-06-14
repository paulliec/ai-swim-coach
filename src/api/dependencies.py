"""
FastAPI dependency injection.

Each function provides a service instance to route handlers.
"""

import logging
from typing import Annotated, Generator

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from ..config.settings import Settings, get_settings
from ..core.analysis.coach import SwimCoach
from ..infrastructure.anthropic.client import (
    AnthropicConfig,
    AnthropicVisionClient,
    create_anthropic_client,
)
from ..infrastructure.snowflake.client import create_snowflake_connection
from ..infrastructure.snowflake.repositories.sessions import (
    SessionRepository,
    SnowflakeConfig,
)
from ..infrastructure.snowflake.repositories.usage_limits import UsageLimitRepository
from ..infrastructure.snowflake.repositories.knowledge import KnowledgeRepository
from ..infrastructure.storage.client import StorageClient, StorageConfig, create_storage_client
from ..infrastructure.video.processor import (
    VideoProcessor,
    create_video_processor,
)

logger = logging.getLogger(__name__)

# Global instances for video processor (expensive to create)
_video_processor = None

# API Key security scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Global mock instances (shared across requests for testing)
_mock_storage_client = None
_mock_snowflake_connection = None


# ---------------------------------------------------------------------------
# Resource builders (shared by Depends providers and background tasks)
# ---------------------------------------------------------------------------

def _snowflake_config(settings: Settings) -> SnowflakeConfig:
    """Build Snowflake config from settings. DRYs the repository providers."""
    return SnowflakeConfig(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        password=settings.snowflake_password or None,
        private_key_path=settings.snowflake_private_key_path,
        private_key_base64=settings.snowflake_private_key_base64,
        database=settings.snowflake_database,
        schema=settings.snowflake_schema,
        warehouse=settings.snowflake_warehouse,
        role=settings.snowflake_role,
    )


def get_mock_snowflake_connection():
    """Shared in-memory connection so mock mode persists across requests/tasks."""
    global _mock_snowflake_connection
    if _mock_snowflake_connection is None:
        from ..infrastructure.snowflake.client import MockSnowflakeConnection
        _mock_snowflake_connection = MockSnowflakeConnection()
        logger.info("Created shared mock Snowflake connection")
    return _mock_snowflake_connection


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

async def verify_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    api_key: str = Security(api_key_header),
) -> str:
    """Validate API key from X-API-Key header. Raises 403 if invalid."""
    if not api_key:
        logger.warning("Request missing API key")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key required. Provide X-API-Key header.",
        )
    
    if api_key not in settings.api_keys_list:
        logger.warning(
            "Invalid API key attempt",
            extra={"key_prefix": api_key[:8] if api_key else ""}
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    
    return api_key


# ---------------------------------------------------------------------------
# Service Dependencies
# ---------------------------------------------------------------------------

def get_swim_coach(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SwimCoach:
    """Provide SwimCoach instance. Stateless, new per request."""
    config = AnthropicConfig(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        temperature=settings.anthropic_temperature,
    )
    
    anthropic_client = AnthropicVisionClient(config)
    return SwimCoach(vision_client=anthropic_client)


def get_session_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[SessionRepository, None, None]:
    """Provide SessionRepository. Mock mode shares connection for data persistence."""
    if settings.snowflake_mock_mode:
        yield SessionRepository(get_mock_snowflake_connection())
    else:
        with create_snowflake_connection(config=_snowflake_config(settings)) as conn:
            yield SessionRepository(conn)


def get_usage_limit_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[UsageLimitRepository, None, None]:
    """Provide UsageLimitRepository. Separate from sessions — could move to Redis later."""
    if settings.snowflake_mock_mode:
        yield UsageLimitRepository(get_mock_snowflake_connection())
    else:
        with create_snowflake_connection(config=_snowflake_config(settings)) as conn:
            yield UsageLimitRepository(conn)


def get_knowledge_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[KnowledgeRepository, None, None]:
    """Provide KnowledgeRepository for RAG. Mock mode returns empty results."""
    if settings.snowflake_mock_mode:
        yield KnowledgeRepository(get_mock_snowflake_connection())
    else:
        with create_snowflake_connection(config=_snowflake_config(settings)) as conn:
            yield KnowledgeRepository(conn)


def get_storage_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StorageClient:
    """Provide R2 or mock storage client. Mock shares instance for persistence."""
    global _mock_storage_client
    
    if settings.r2_mock_mode:
        if _mock_storage_client is None:
            _mock_storage_client = create_storage_client(mock_mode=True)
        return _mock_storage_client
    else:
        config = StorageConfig(
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket_name=settings.r2_bucket_name,
            endpoint_url=settings.r2_endpoint,
        )
        client = create_storage_client(config=config)

    return client


def get_video_processor(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VideoProcessor:
    """Provide video processor. Cached — FFmpeg availability check is expensive."""
    global _video_processor
    
    mock_mode = settings.video_processor_mock_mode
    
    if _video_processor is None:
        _video_processor = create_video_processor(mock_mode=mock_mode)
        logger.info(
            f"Created video processor",
            extra={"mock_mode": mock_mode}
        )
    
    return _video_processor


def get_vision_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnthropicVisionClient:
    """Raw Anthropic client for agentic coach (direct vision access)."""
    config = AnthropicConfig(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        temperature=settings.anthropic_temperature,
    )
    
    return AnthropicVisionClient(config)


# ---------------------------------------------------------------------------
# Convenience Type Aliases
# ---------------------------------------------------------------------------

AuthenticatedUser = Annotated[str, Depends(verify_api_key)]
SwimCoachDep = Annotated[SwimCoach, Depends(get_swim_coach)]
SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
UsageLimitRepositoryDep = Annotated[UsageLimitRepository, Depends(get_usage_limit_repository)]
KnowledgeRepositoryDep = Annotated[KnowledgeRepository, Depends(get_knowledge_repository)]
StorageClientDep = Annotated[StorageClient, Depends(get_storage_client)]
VideoProcessorDep = Annotated[VideoProcessor, Depends(get_video_processor)]
VisionClientDep = Annotated[AnthropicVisionClient, Depends(get_vision_client)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

