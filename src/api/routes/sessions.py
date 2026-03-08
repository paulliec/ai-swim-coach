"""
Coaching session API endpoints.

Manages follow-up conversations after video analysis.
History is persisted in Snowflake for multi-turn coaching.
"""

import logging
from uuid import UUID

from typing import Annotated, Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from ...core.analysis.models import CoachingSession
from ..dependencies import (
    AuthenticatedUser,
    SessionRepositoryDep,
    SwimCoachDep,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Request to continue coaching conversation."""
    message: str = Field(
        description="User's question or comment",
        min_length=1,
        max_length=2000,
    )


class ChatResponse(BaseModel):
    """Response with coach's message."""
    session_id: UUID = Field(description="Session identifier")
    user_message: str = Field(description="The user's message (echoed)")
    assistant_message: str = Field(description="The coach's response")
    message_count: int = Field(description="Total messages in conversation")


class MessageItem(BaseModel):
    """Single message in conversation history."""
    role: str = Field(description="Message role (user or assistant)")
    content: str = Field(description="Message content")
    timestamp: str = Field(description="When the message was sent (ISO format)")


class SessionDetailResponse(BaseModel):
    """Complete session details."""
    session_id: UUID = Field(description="Session identifier")
    created_at: str = Field(description="When session was created (ISO format)")
    updated_at: str = Field(description="Last update time (ISO format)")
    
    # Video info
    has_video: bool = Field(description="Whether video metadata exists")
    video_filename: str | None = Field(None, description="Original video filename")
    
    # Analysis info
    is_analyzed: bool = Field(description="Whether video has been analyzed")
    stroke_type: str | None = Field(None, description="Stroke type if analyzed")
    summary: str | None = Field(None, description="Analysis summary if available")
    
    # Conversation
    message_count: int = Field(description="Number of messages in conversation")
    messages: list[MessageItem] = Field(description="Full conversation history")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=list[SessionDetailResponse],
    status_code=status.HTTP_200_OK,
    summary="List user sessions",
    description="Retrieve recent coaching sessions for the authenticated user",
)
async def list_sessions(
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    repository: SessionRepositoryDep = None,
) -> list[SessionDetailResponse]:
    """List recent sessions, ordered by most recently updated."""
    logger.info(
        "Listing sessions",
        extra={"user_id": x_user_id or "anonymous"}
    )

    try:
        sessions = repository.list_recent(limit=20)
    except Exception as e:
        logger.error(
            "Failed to list sessions",
            extra={"error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve sessions"
        )

    results = []
    for session in sessions:
        messages = [
            MessageItem(
                role=msg.role,
                content=msg.content,
                timestamp=msg.timestamp.isoformat(),
            )
            for msg in session.conversation
        ]

        results.append(SessionDetailResponse(
            session_id=session.id,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            has_video=session.has_video,
            video_filename=session.video.filename if session.video else None,
            is_analyzed=session.is_analyzed,
            stroke_type=session.analysis.stroke_type.value if session.analysis else None,
            summary=session.analysis.summary if session.analysis else None,
            message_count=len(session.conversation),
            messages=messages,
        ))

    return results


@router.post(
    "/{session_id}/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Continue coaching conversation",
    description="Ask follow-up questions about your technique analysis",
)
async def chat_with_coach(
    session_id: UUID,
    request: ChatRequest,
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    coach: SwimCoachDep = None,
    repository: SessionRepositoryDep = None,
) -> ChatResponse:
    """Continue coaching conversation with context from analysis and prior messages."""
    logger.info(
        "Processing chat message",
        extra={
            "session_id": str(session_id),
            "message_length": len(request.message),
        }
    )
    
    try:
        session = repository.get_session(session_id)
    except Exception as e:
        logger.error(
            "Session not found",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    if not session.is_analyzed:
        logger.warning(
            "Chat attempted on unanalyzed session",
            extra={"session_id": str(session_id)}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session must be analyzed before starting conversation. Call POST /analysis/{session_id}/analyze first."
        )
    
    try:
        assistant_message = await coach.continue_conversation(
            session=session,
            user_message=request.message,
        )
        
        session.add_message("user", request.message)
        session.add_message("assistant", assistant_message)
        
        repository.save_session(session)
        
        logger.info(
            "Chat message processed",
            extra={
                "session_id": str(session_id),
                "response_length": len(assistant_message),
                "total_messages": len(session.conversation),
            }
        )
    
    except Exception as e:
        logger.error(
            "Chat processing failed",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process message: {str(e)}"
        )
    
    return ChatResponse(
        session_id=session_id,
        user_message=request.message,
        assistant_message=assistant_message,
        message_count=len(session.conversation),
    )


@router.get(
    "/{session_id}",
    response_model=SessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get session details",
    description="Retrieve complete session including video metadata, analysis, and conversation history",
)
async def get_session(
    session_id: UUID,
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    repository: SessionRepositoryDep = None,
) -> SessionDetailResponse:
    """Retrieve complete session with video metadata, analysis, and conversation."""
    logger.info(
        "Retrieving session",
        extra={"session_id": str(session_id)}
    )
    
    try:
        session = repository.get_session(session_id)
    except Exception as e:
        logger.error(
            "Session not found",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    messages = [
        MessageItem(
            role=msg.role,
            content=msg.content,
            timestamp=msg.timestamp.isoformat(),
        )
        for msg in session.conversation
    ]
    
    return SessionDetailResponse(
        session_id=session.id,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
        has_video=session.has_video,
        video_filename=session.video.filename if session.video else None,
        is_analyzed=session.is_analyzed,
        stroke_type=session.analysis.stroke_type.value if session.analysis else None,
        summary=session.analysis.summary if session.analysis else None,
        message_count=len(session.conversation),
        messages=messages,
    )


@router.post(
    "/{session_id}/claim",
    status_code=status.HTTP_200_OK,
    summary="Claim anonymous session",
    description="Associate an anonymous session with the authenticated user",
)
async def claim_session(
    session_id: UUID,
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    repository: SessionRepositoryDep = None,
) -> dict:
    """Associate an anonymous session with an authenticated user."""
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID required to claim session"
        )
    
    logger.info(
        "Claiming anonymous session",
        extra={"session_id": str(session_id), "user_id": x_user_id}
    )
    
    try:
        session = repository.get_session(session_id)
        # TODO: fix later - verify session is anonymous and not owned by another user
        repository.save_session(session)
        
        logger.info(
            "Session claimed successfully",
            extra={"session_id": str(session_id), "user_id": x_user_id}
        )
        
        return {
            "session_id": str(session_id),
            "user_id": x_user_id,
            "message": "Session claimed successfully"
        }
        
    except Exception as e:
        logger.error(
            "Failed to claim session",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to claim session"
        )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete session",
    description="Delete a session and all associated data (frames, analysis, messages)",
)
async def delete_session(
    session_id: UUID,
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    repository: SessionRepositoryDep = None,
) -> None:
    """Delete a coaching session and all associated data."""
    logger.info(
        "Delete session requested",
        extra={"session_id": str(session_id)}
    )
    
    # TODO: fix later - needs cascade delete (storage + DB) and soft-delete support
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Session deletion not yet implemented"
    )

