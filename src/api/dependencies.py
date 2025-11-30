"""
FastAPI dependency injection.

Dependencies provide instances of services, clients, and configuration
to route handlers. Using dependency injection means:
- Routes don't instantiate their own dependencies (easier to test)
- Dependencies can be mocked for testing
- Configuration is centralized
- Resource lifecycle (connections, clients) is managed properly

Each dependency is a function that FastAPI calls when needed.
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
from ..infrastructure.storage.client import StorageClient, StorageConfig, create_storage_client

logger = logging.getLogger(__name__)

# API Key security scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Global mock instances (shared across requests for testing)
_mock_storage_client = None
_mock_snowflake_connection = None


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

async def verify_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    api_key: str = Security(api_key_header),
) -> str:
    """
    Validate API key from request header.
    
    This is a simple API key authentication scheme. For production, you'd want:
    - Key hashing (don't store plaintext keys)
    - Rate limiting per key
    - Key usage analytics
    - Automatic key rotation
    
    Raises 403 if key is invalid or missing.
    """
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
    """
    Provide SwimCoach instance with Anthropic client.
    
    The coach is stateless, so we create a new instance per request.
    In production with high load, you might want to reuse the Anthropic
    client across requests.
    """
    # Create Anthropic client
    config = AnthropicConfig(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        temperature=settings.anthropic_temperature,
    )
    
    anthropic_client = AnthropicVisionClient(config)
    
    # Create and return coach
    coach = SwimCoach(vision_client=anthropic_client)
    
    logger.debug("Created SwimCoach instance")
    
    return coach


def get_session_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[SessionRepository, None, None]:
    """
    Provide SessionRepository with database connection.
    
    This is a generator function (yields instead of returns) because
    we need to manage the connection lifecycle:
    1. Create connection
    2. Create repository
    3. Yield repository (FastAPI injects it)
    4. Close connection (cleanup after request)
    
    FastAPI automatically handles the generator lifecycle.
    
    In mock mode, we reuse the same connection across requests
    so that data persists during the testing session.
    """
    global _mock_snowflake_connection
    
    if settings.snowflake_mock_mode:
        # Use shared mock connection (persists across requests)
        if _mock_snowflake_connection is None:
            from ..infrastructure.snowflake.client import MockSnowflakeConnection
            _mock_snowflake_connection = MockSnowflakeConnection()
            logger.info("Created shared mock Snowflake connection for session")
        
        repo = SessionRepository(_mock_snowflake_connection)
        logger.debug("Using shared mock Snowflake connection")
        yield repo
    else:
        # Use real Snowflake connection
        config = SnowflakeConfig(
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
        
        with create_snowflake_connection(config=config) as conn:
            repo = SessionRepository(conn)
            logger.debug("Created SessionRepository with Snowflake connection")
            yield repo


def get_usage_limit_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[UsageLimitRepository, None, None]:
    """
    Provide UsageLimitRepository for rate limiting.
    
    Like SessionRepository, this uses the same Snowflake connection
    strategy: shared mock for testing, real connection for production.
    
    Why separate from SessionRepository:
    - Different concerns (rate limiting vs session data)
    - Could be moved to Redis later for better performance
    - Keeps repository responsibilities focused
    """
    global _mock_snowflake_connection
    
    if settings.snowflake_mock_mode:
        # Use shared mock connection (same as SessionRepository)
        if _mock_snowflake_connection is None:
            from ..infrastructure.snowflake.client import MockSnowflakeConnection
            _mock_snowflake_connection = MockSnowflakeConnection()
            logger.info("Created shared mock Snowflake connection for usage limits")
        
        repo = UsageLimitRepository(_mock_snowflake_connection)
        logger.debug("Using shared mock Snowflake connection for usage limits")
        yield repo
    else:
        # Use real Snowflake connection
        config = SnowflakeConfig(
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
        
        with create_snowflake_connection(config=config) as conn:
            repo = UsageLimitRepository(conn)
            logger.debug("Created UsageLimitRepository with Snowflake connection")
            yield repo


def get_storage_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StorageClient:
    """
    Provide storage client for frame uploads/downloads.
    
    Returns either R2 client or mock client based on settings.
    
    In mock mode, we reuse the same client across requests
    so that uploaded frames persist during the testing session.
    """
    global _mock_storage_client
    
    if settings.r2_mock_mode:
        # Use shared mock client (persists across requests)
        if _mock_storage_client is None:
            _mock_storage_client = create_storage_client(mock_mode=True)
            logger.info("Created shared mock storage client for session")
        logger.debug("Using shared mock storage client")
        return _mock_storage_client
    else:
        config = StorageConfig(
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket_name=settings.r2_bucket_name,
            endpoint_url=settings.r2_endpoint,
        )
        client = create_storage_client(config=config)
        logger.debug("Created R2 storage client")
    
    return client


# ---------------------------------------------------------------------------
# Convenience Type Aliases
# ---------------------------------------------------------------------------

# These type aliases make route signatures cleaner
AuthenticatedUser = Annotated[str, Depends(verify_api_key)]
SwimCoachDep = Annotated[SwimCoach, Depends(get_swim_coach)]
SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
UsageLimitRepositoryDep = Annotated[UsageLimitRepository, Depends(get_usage_limit_repository)]
StorageClientDep = Annotated[StorageClient, Depends(get_storage_client)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

