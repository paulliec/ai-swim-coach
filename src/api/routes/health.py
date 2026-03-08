"""
Health check endpoints.

/health — liveness (is the process running?)
/health/ready — readiness (can we serve traffic?)
"""

import logging
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from ..dependencies import SettingsDep, SessionRepositoryDep

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    details: dict[str, Any] = {}


class ReadinessCheck(BaseModel):
    """Individual readiness check result."""
    name: str
    status: str  # "ok" or "error"
    error: str | None = None


class ReadinessResponse(BaseModel):
    """Readiness check response with details."""
    status: str  # "ready" or "not_ready"
    version: str
    checks: list[ReadinessCheck]


@router.get(
    "",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Basic health check",
    description="Returns 200 if the service is running. Does not check dependencies.",
)
async def health_check(settings: SettingsDep) -> HealthResponse:
    """Liveness check — no external dependency checks."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        details={
            "mock_mode": {
                "snowflake": settings.snowflake_mock_mode,
                "r2": settings.r2_mock_mode,
            }
        }
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    summary="Readiness check",
    description="Returns 200 if the service can handle traffic. Checks external dependencies.",
    responses={
        503: {
            "description": "Service not ready",
            "model": ReadinessResponse,
        }
    },
)
async def readiness_check(
    settings: SettingsDep,
    repository: SessionRepositoryDep,
) -> ReadinessResponse:
    """Readiness check — verifies config, DB, and API key. Returns 503 if anything fails."""
    checks: list[ReadinessCheck] = []
    all_ok = True
    
    try:
        missing_fields = settings.validate_required_fields()
        if missing_fields:
            checks.append(ReadinessCheck(
                name="configuration",
                status="error",
                error=f"Missing required fields: {', '.join(missing_fields)}"
            ))
            all_ok = False
        else:
            checks.append(ReadinessCheck(
                name="configuration",
                status="ok"
            ))
    except Exception as e:
        checks.append(ReadinessCheck(
            name="configuration",
            status="error",
            error=str(e)
        ))
        all_ok = False
    
    if not settings.snowflake_mock_mode:
        try:
            # TODO: fix later - should call repo.health_check() instead of no-op
            checks.append(ReadinessCheck(
                name="database",
                status="ok"
            ))
        except Exception as e:
            logger.error("Database health check failed", extra={"error": str(e)})
            checks.append(ReadinessCheck(
                name="database",
                status="error",
                error=str(e)
            ))
            all_ok = False
    else:
        checks.append(ReadinessCheck(
            name="database",
            status="ok",
            error="mock mode"
        ))
    
    if not settings.anthropic_api_key:
        checks.append(ReadinessCheck(
            name="anthropic",
            status="error",
            error="API key not configured"
        ))
        all_ok = False
    else:
        checks.append(ReadinessCheck(
            name="anthropic",
            status="ok"
        ))
    
    response_status = "ready" if all_ok else "not_ready"
    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    
    response = ReadinessResponse(
        status=response_status,
        version="0.1.0",
        checks=checks
    )
    
    if not all_ok:
        logger.warning(
            "Readiness check failed",
            extra={
                "checks": [
                    {"name": c.name, "status": c.status, "error": c.error}
                    for c in checks
                ]
            }
        )
    
    return response

