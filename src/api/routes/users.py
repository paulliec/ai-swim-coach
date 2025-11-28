"""
User-specific API endpoints.

Manages user-related operations like fetching session history.
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import (
    AuthenticatedUser,
    SessionRepositoryDep,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------

class UserSessionSummary(BaseModel):
    """Summary of a user's session."""
    session_id: str = Field(description="Session identifier")
    created_at: str = Field(description="When session was created")
    updated_at: str = Field(description="Last update time")
    stroke_type: str | None = Field(None, description="Stroke type if analyzed")
    summary: str | None = Field(None, description="Analysis summary preview")
    message_count: int = Field(description="Number of messages in conversation")
    frame_count: int = Field(0, description="Number of frames analyzed")


class UserSessionsResponse(BaseModel):
    """Response with user's sessions."""
    sessions: list[UserSessionSummary] = Field(description="List of sessions")
    total: int = Field(description="Total number of sessions")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/me/sessions",
    response_model=UserSessionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get my session history",
    description="Retrieve all sessions for the authenticated user",
)
async def get_my_sessions(
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    repository: SessionRepositoryDep = None,
) -> UserSessionsResponse:
    """
    Retrieve all sessions for the current user.
    
    Returns a list of session summaries including:
    - Session ID
    - Created/updated timestamps
    - Stroke type
    - Analysis summary preview
    - Message count
    
    Sessions are ordered by most recent first.
    
    Note: Currently returns all sessions. Once user_id is stored
    in the database, this will filter by the authenticated user.
    """
    user_id = x_user_id or "anonymous"
    
    logger.info(
        "Fetching user sessions",
        extra={"user_id": user_id}
    )
    
    try:
        # Fetch recent sessions
        # TODO: Filter by user_id once it's stored in database
        sessions = repository.list_recent(limit=50)
        
        # Convert to response format
        session_list = []
        for session in sessions:
            session_list.append(UserSessionSummary(
                session_id=str(session.id),
                created_at=session.created_at.isoformat(),
                updated_at=session.updated_at.isoformat(),
                stroke_type=session.analysis.stroke_type.value if session.analysis else None,
                summary=session.analysis.summary[:150] if session.analysis and session.analysis.summary else None,
                message_count=len(session.conversation),
                frame_count=session.analysis.frame_count_analyzed if session.analysis else 0,
            ))
        
        return UserSessionsResponse(
            sessions=session_list,
            total=len(session_list)
        )
        
    except Exception as e:
        logger.error(
            "Failed to fetch user sessions",
            extra={"user_id": user_id, "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch sessions"
        )

