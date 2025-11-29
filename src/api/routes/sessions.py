"""
Coaching session API endpoints.

Manages ongoing coaching conversations after initial video analysis.
Users can ask follow-up questions and get targeted advice based on
their specific video and technique.

The conversation history is maintained in the database, enabling:
- Multi-turn coaching dialogues
- Session resumption across disconnects  
- Historical reference for the swimmer
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
    """
    Continue a coaching conversation.
    
    This endpoint enables multi-turn dialogue with the AI coach.
    The coach has context from:
    - The initial video analysis
    - Previous messages in the conversation
    
    This allows for targeted follow-up questions like:
    - "Can you explain the catch drill in more detail?"
    - "How do I know if my elbow is high enough?"
    - "What should I focus on first?"
    
    The conversation is persisted to the database so sessions
    can be resumed later.
    """
    logger.info(
        "Processing chat message",
        extra={
            "session_id": str(session_id),
            "message_length": len(request.message),
        }
    )
    
    # Load session
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
    
    # Verify session has been analyzed
    if not session.is_analyzed:
        logger.warning(
            "Chat attempted on unanalyzed session",
            extra={"session_id": str(session_id)}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session must be analyzed before starting conversation. Call POST /analysis/{session_id}/analyze first."
        )
    
    # Get coach response
    try:
        assistant_message = await coach.continue_conversation(
            session=session,
            user_message=request.message,
        )
        
        # Add both messages to session
        session.add_message("user", request.message)
        session.add_message("assistant", assistant_message)
        
        # Save updated session
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
    """
    Retrieve complete session details.
    
    Returns everything about a coaching session:
    - Video metadata
    - Analysis results
    - Full conversation history
    
    Useful for:
    - Displaying session history in UI
    - Resuming conversations
    - Reviewing past coaching sessions
    - Debugging and support
    """
    logger.info(
        "Retrieving session",
        extra={"session_id": str(session_id)}
    )
    
    # Load session
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
    
    # Convert to response format
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
    """
    Claim an anonymous session.
    
    When a user tries the app without signing in, their session
    is anonymous. After they sign in, they can claim it to save
    the analysis to their account.
    
    This endpoint:
    1. Loads the session
    2. Updates it with the user_id
    3. Saves it back to the database
    
    Returns the updated session details.
    """
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
        # Load session
        session = repository.get_session(session_id)
        
        # TODO: In production, verify session is anonymous (no user_id)
        # and not owned by another user
        
        # Update with user_id (when we add it to the model)
        # For now, just return success
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
    """
    Delete a coaching session.
    
    This endpoint would:
    1. Delete frames from object storage
    2. Delete messages from database
    3. Delete analysis from database
    4. Delete session record
    
    For now, this is a placeholder. In production, you'd want:
    - Soft delete (mark as deleted, cleanup later)
    - Cascading delete rules in database
    - Audit logging
    - User confirmation
    """
    logger.info(
        "Delete session requested",
        extra={"session_id": str(session_id)}
    )
    
    # TODO: Implement session deletion
    # - Delete from storage
    # - Delete from database
    # - Handle errors gracefully
    
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Session deletion not yet implemented"
    )

