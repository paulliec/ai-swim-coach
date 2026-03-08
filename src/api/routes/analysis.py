"""
Video analysis API endpoints.

Upload frames, then analyze with AI.
"""

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from typing import Optional

from ...core.analysis.coach import FrameSet
from ...core.analysis.models import (
    AnalysisResult,
    CoachingSession,
    StrokeType,
    VideoMetadata,
)
from ..dependencies import (
    AuthenticatedUser,
    KnowledgeRepositoryDep,
    SessionRepositoryDep,
    SettingsDep,
    StorageClientDep,
    SwimCoachDep,
    UsageLimitRepositoryDep,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class FrameUploadResponse(BaseModel):
    """Response after uploading frames."""
    session_id: UUID = Field(description="Unique session identifier")
    frames_received: int = Field(description="Number of frames successfully uploaded")
    storage_paths: list[str] = Field(description="Storage paths for uploaded frames")
    message: str = Field(description="Status message")


class AnalysisRequest(BaseModel):
    """Request to analyze uploaded frames."""
    stroke_type: StrokeType = Field(
        default=StrokeType.FREESTYLE,
        description="Type of stroke to analyze"
    )
    user_notes: str = Field(
        default="",
        description="Additional context from the user (optional)"
    )


class FeedbackItem(BaseModel):
    """Single piece of coaching feedback."""
    priority: str = Field(description="Priority level (primary, secondary, refinement)")
    category: str = Field(description="Technique category")
    observation: str = Field(description="What was observed")
    recommendation: str = Field(description="What to do about it")
    drill_suggestions: list[str] = Field(
        default_factory=list,
        description="Suggested drills to practice"
    )


class AnalysisResponse(BaseModel):
    """Response with coaching analysis."""
    session_id: UUID = Field(description="Session identifier")
    stroke_type: str = Field(description="Stroke type analyzed")
    summary: str = Field(description="Overall analysis summary")
    feedback: list[FeedbackItem] = Field(description="Coaching feedback items")
    frame_count: int = Field(description="Number of frames analyzed")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=FrameUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload video frames",
    description="Upload frames extracted from swimming video for analysis",
)
async def upload_frames(
    frames: Annotated[list[UploadFile], File(description="Video frames as images (JPEG/PNG)")],
    stroke_type: Annotated[StrokeType, Form()] = StrokeType.FREESTYLE,
    user_notes: Annotated[str, Form()] = "",
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    storage: StorageClientDep = None,
    repository: SessionRepositoryDep = None,
    settings: SettingsDep = None,
) -> FrameUploadResponse:
    """Upload frames, create session, return session ID for analysis."""
    if not frames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one frame is required"
        )
    
    if len(frames) > settings.max_frames_per_upload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {settings.max_frames_per_upload} frames allowed"
        )
    
    session_id = uuid4()
    
    logger.info(
        "Processing frame upload",
        extra={
            "session_id": str(session_id),
            "frame_count": len(frames),
            "stroke_type": stroke_type.value,
            "user_id": x_user_id or "anonymous",
        }
    )
    
    storage_paths: list[str] = []
    total_size = 0
    
    try:
        for i, frame in enumerate(frames):
            frame_data = await frame.read()
            frame_size = len(frame_data)
            total_size += frame_size
            
            max_size_bytes = settings.max_upload_size_mb * 1024 * 1024
            if total_size > max_size_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Total upload size exceeds {settings.max_upload_size_mb}MB"
                )
            
            if not frame.content_type or not frame.content_type.startswith("image/"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Frame {i} is not an image (got {frame.content_type})"
                )
            
            storage_path = await storage.upload_frame(
                frame_data=frame_data,
                session_id=session_id,
                frame_number=i,
            )
            storage_paths.append(storage_path)
            
            logger.debug(
                "Uploaded frame",
                extra={
                    "session_id": str(session_id),
                    "frame_number": i,
                    "size_bytes": frame_size,
                    "storage_path": storage_path,
                }
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Frame upload failed",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}"
        )
    
    try:
        video = VideoMetadata(
            filename=f"session_{session_id}_frames.zip",
            duration_seconds=0.0,
            resolution=(0, 0),
            fps=0.0,
            file_size_bytes=total_size,
            storage_path=f"frames/{session_id}/",
        )
        
        session = CoachingSession(
            id=session_id,
            video=video,
        )

        repository.save_session(session)
        
        logger.info(
            "Created coaching session",
            extra={
                "session_id": str(session_id),
                "frame_count": len(frames),
            }
        )
    
    except Exception as e:
        logger.error(
            "Failed to create session",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create session"
        )
    
    return FrameUploadResponse(
        session_id=session_id,
        frames_received=len(frames),
        storage_paths=storage_paths,
        message=f"Successfully uploaded {len(frames)} frames. Use session ID to request analysis."
    )


@router.post(
    "/{session_id}/analyze",
    response_model=AnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze uploaded frames",
    description="Trigger AI analysis of previously uploaded frames",
)
async def analyze_session(
    session_id: UUID,
    analysis_request: AnalysisRequest,
    fastapi_request: Request,
    x_user_id: Annotated[Optional[str], Header()] = None,
    x_api_key: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    settings: SettingsDep = None,
    coach: SwimCoachDep = None,
    storage: StorageClientDep = None,
    repository: SessionRepositoryDep = None,
    usage_limit_repo: UsageLimitRepositoryDep = None,
    knowledge_repo: KnowledgeRepositoryDep = None,
) -> AnalysisResponse:
    """Analyze frames with AI. Rate-limited (3/day per user unless bypassed)."""
    logger.info(
        "Starting analysis",
        extra={
            "session_id": str(session_id),
            "stroke_type": analysis_request.stroke_type.value,
        }
    )
    
    bypass_rate_limit = False
    if x_api_key and x_api_key in settings.rate_limit_bypass_keys_list:
        bypass_rate_limit = True
        logger.info(
            "Rate limit bypassed: trusted API key",
            extra={"api_key_prefix": x_api_key[:8] + "..." if len(x_api_key) > 8 else x_api_key}
        )
    elif x_user_id and x_user_id in settings.rate_limit_bypass_user_ids_list:
        bypass_rate_limit = True
        logger.info(f"Rate limit bypassed for user ID {x_user_id}")
    
    x_user_email = fastapi_request.headers.get("x-user-email", "").lower()
    if x_user_email and x_user_email in settings.rate_limit_bypass_emails_list:
        bypass_rate_limit = True
        logger.info(f"Rate limit bypassed for email {x_user_email}")
    
    if not bypass_rate_limit:
        if x_user_id:
            identifier = x_user_id
            identifier_type = "user_id"
        else:
            identifier = fastapi_request.client.host if fastapi_request.client else "unknown"
            identifier_type = "ip_address"
        
        allowed, current_count, limit_max = usage_limit_repo.check_and_increment(
            identifier=identifier,
            identifier_type=identifier_type,
            resource_type="video_analysis",
            limit_max=3,
            period_hours=24
        )
        
        if not allowed:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "identifier": identifier,
                    "identifier_type": identifier_type,
                    "current_count": current_count,
                    "limit": limit_max
                }
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"You've reached your daily limit of {limit_max} analyses. Come back tomorrow!"
            )
        
        logger.info(
            "Rate limit check passed",
            extra={
                "identifier": identifier,
                "count": current_count,
                "limit": limit_max
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
    
    if session.is_analyzed:
        logger.warning(
            "Session already analyzed",
            extra={"session_id": str(session_id)}
        )
        # TODO: fix later - should return existing analysis or reject re-analysis

    # TODO: fix later - frame count should be tracked, not guessed
    frame_data: list[bytes] = []
    frame_timestamps: list[float] = []
    
    try:
        for frame_num in range(20):
            try:
                storage_path = f"frames/{session_id}/{frame_num:04d}.jpg"
                data = await storage.download_frame(storage_path)
                frame_data.append(data)
                frame_timestamps.append(frame_num * 0.5)
            except Exception:
                break
        
        if not frame_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No frames found for this session"
            )
        
        logger.debug(
            "Loaded frames from storage",
            extra={"session_id": str(session_id), "count": len(frame_data)}
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to load frames",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load frames"
        )
    
    # RAG is optional — gracefully degrades if no knowledge
    knowledge_context: list[str] = []
    try:
        knowledge_chunks = knowledge_repo.get_relevant_for_stroke(
            stroke_type=analysis_request.stroke_type.value,
            analysis_summary=analysis_request.user_notes if analysis_request.user_notes else None,
            limit=5
        )
        knowledge_context = [chunk.content for chunk in knowledge_chunks]
        
        if knowledge_context:
            logger.info(
                "Retrieved RAG knowledge",
                extra={
                    "session_id": str(session_id),
                    "chunk_count": len(knowledge_context),
                    "stroke_type": analysis_request.stroke_type.value
                }
            )
    except Exception as e:
        logger.warning(
            "RAG knowledge retrieval failed, proceeding without",
            extra={"session_id": str(session_id), "error": str(e)}
        )
    
    try:
        frames = FrameSet(frames=frame_data, timestamps_seconds=frame_timestamps)
        
        analysis = await coach.analyze_video(
            frames=frames,
            stroke_type=analysis_request.stroke_type,
            user_notes=analysis_request.user_notes,
            knowledge_context=knowledge_context if knowledge_context else None,
        )
        
        session.analysis = analysis
        repository.save_session(session)
        
        logger.info(
            "Analysis complete",
            extra={
                "session_id": str(session_id),
                "frame_count": len(frame_data),
            }
        )
    
    except Exception as e:
        logger.error(
            "Analysis failed",
            extra={"session_id": str(session_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}"
        )
    
    feedback_items = [
        FeedbackItem(
            priority=fb.priority.value,
            category=fb.observation.category.value,
            observation=fb.observation.description,
            recommendation=fb.recommendation,
            drill_suggestions=fb.drill_suggestions,
        )
        for fb in analysis.feedback
    ]
    
    return AnalysisResponse(
        session_id=session_id,
        stroke_type=analysis.stroke_type.value,
        summary=analysis.summary,
        feedback=feedback_items,
        frame_count=len(frame_data),
    )

