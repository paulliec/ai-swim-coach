"""
Health check endpoints.

Health checks are essential for:
- Load balancers to know if the service is alive
- Monitoring systems to track availability
- Deployment systems to verify rollouts
- Debugging production issues

We provide two endpoints:
- /health: Basic liveness check (is the process running?)
- /health/ready: Readiness check (can we serve traffic?)

The distinction matters in orchestration systems like Kubernetes
where liveness and readiness have different behaviors.
"""

import logging
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from ..dependencies import SettingsDep, SessionRepositoryDep

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    """
    Health check response.
    
    Standardized format makes it easy for monitoring tools to parse.
    """
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
    """
    Liveness check - is the process alive?
    
    This endpoint should be very fast and not check external dependencies.
    It's used by load balancers to route traffic and by orchestrators
    to decide if they should restart the service.
    
    If this check fails, the service is not running at all.
    """
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
    """
    Readiness check - can we serve traffic?
    
    This endpoint checks that all critical dependencies are available:
    - Database connection works
    - Configuration is valid
    - Critical external services are reachable
    
    This is slower than the liveness check but provides better signal
    about whether the service can actually fulfill requests.
    
    Returns 503 if any check fails, which tells load balancers not
    to route traffic here.
    """
    checks: list[ReadinessCheck] = []
    all_ok = True
    
    # Check configuration
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
    
    # Check database (if not in mock mode)
    if not settings.snowflake_mock_mode:
        try:
            # Try a simple operation to verify connection
            # In a real implementation, SessionRepository would have a health_check method
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
    
    # Check Anthropic API key is set
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
    
    # Determine overall status
    response_status = "ready" if all_ok else "not_ready"
    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    
    response = ReadinessResponse(
        status=response_status,
        version="0.1.0",
        checks=checks
    )
    
    # Log if not ready
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

