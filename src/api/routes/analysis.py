"""
Video analysis API endpoints.

Handles the core workflow:
1. Client uploads frames (POST /upload)
2. Server analyzes frames with AI (POST /{session_id}/analyze)
3. Client receives coaching feedback

Frames are uploaded separately from analysis to support:
- Progress indicators during upload
- Validation before expensive AI calls
- Separation of upload and processing infrastructure
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
    """
    Upload frames for a new coaching session.
    
    This endpoint:
    1. Validates frame count and sizes
    2. Creates a new session
    3. Stores frames in object storage
    4. Saves session metadata to database
    5. Returns session ID for subsequent analysis
    
    Frames should be JPEGs extracted from video at key moments.
    The client is responsible for frame extraction because:
    - Keeps server logic simple
    - Enables client-side preview
    - Reduces server processing load
    - Works with any video format the client supports
    """
    # Validate frame count
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
    
    # Create new session
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
    
    # Upload frames to storage
    storage_paths: list[str] = []
    total_size = 0
    
    try:
        for i, frame in enumerate(frames):
            # Read frame data
            frame_data = await frame.read()
            frame_size = len(frame_data)
            total_size += frame_size
            
            # Check total size
            max_size_bytes = settings.max_upload_size_mb * 1024 * 1024
            if total_size > max_size_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Total upload size exceeds {settings.max_upload_size_mb}MB"
                )
            
            # Validate it's actually an image (basic check)
            if not frame.content_type or not frame.content_type.startswith("image/"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Frame {i} is not an image (got {frame.content_type})"
                )
            
            # Upload to storage
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
        # Re-raise HTTP exceptions as-is
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
    
    # Create session in database
    try:
        # Create minimal video metadata (we don't have full video info since client sent frames)
        video = VideoMetadata(
            filename=f"session_{session_id}_frames.zip",
            duration_seconds=0.0,  # Unknown without full video
            resolution=(0, 0),  # Unknown
            fps=0.0,  # Unknown
            file_size_bytes=total_size,
            storage_path=f"frames/{session_id}/",
        )
        
        # Create session
        session = CoachingSession(
            id=session_id,
            video=video,
        )
        
        # Save to database
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
    """
    Analyze frames and generate coaching feedback.
    
    This is the expensive operation - it:
    1. Checks rate limits (3 per day per user/IP, unless bypassed)
    2. Loads frames from storage
    3. Sends them to Claude for analysis
    4. Parses the response into structured feedback
    5. Saves results to database
    
    Rate Limiting:
    - Default: 3 analyses per day per user (Clerk ID) or IP address
    - Bypass: API keys in RATE_LIMIT_BYPASS_KEYS skip rate limiting
    - Future: Email addresses in RATE_LIMIT_BYPASS_EMAILS will also bypass
    
    The analysis can take 10-30 seconds depending on:
    - Number of frames
    - Model performance
    - API latency
    
    In production, you might want to:
    - Make this async with a job queue
    - Add progress updates via websocket
    - Implement timeouts
    """
    logger.info(
        "Starting analysis",
        extra={
            "session_id": str(session_id),
            "stroke_type": analysis_request.stroke_type.value,
        }
    )
    
    # Check if API key bypasses rate limiting
    bypass_rate_limit = False
    if x_api_key and x_api_key in settings.rate_limit_bypass_keys_list:
        bypass_rate_limit = True
        logger.info(
            "Rate limit bypassed: trusted API key",
            extra={"api_key_prefix": x_api_key[:8] + "..." if len(x_api_key) > 8 else x_api_key}
        )
    
    # Check rate limits (3 analyses per day per user/IP) unless bypassed
    if not bypass_rate_limit:
        # Use user ID if authenticated, otherwise use IP address
        if x_user_id:
            identifier = x_user_id
            identifier_type = "user_id"
        else:
            # Get client IP from request
            identifier = fastapi_request.client.host if fastapi_request.client else "unknown"
            identifier_type = "ip_address"
        
        allowed, current_count, limit_max = usage_limit_repo.check_and_increment(
            identifier=identifier,
            identifier_type=identifier_type,
            resource_type="video_analysis",
            limit_max=3,  # 3 analyses per day
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
    
    # Check if already analyzed
    if session.is_analyzed:
        logger.warning(
            "Session already analyzed",
            extra={"session_id": str(session_id)}
        )
        # Could return existing analysis or reject - for now we allow re-analysis
    
    # Load frames from storage
    # We need to know which frames exist - in real implementation, this would be tracked
    # For now, try to load frames 0-19 (max we allow)
    frame_data: list[bytes] = []
    frame_timestamps: list[float] = []
    
    try:
        for frame_num in range(20):  # Try up to max frames
            try:
                storage_path = f"frames/{session_id}/{frame_num:04d}.jpg"
                data = await storage.download_frame(storage_path)
                frame_data.append(data)
                # Estimate timestamp based on frame number (assume 0.5s apart)
                frame_timestamps.append(frame_num * 0.5)
            except Exception:
                # Frame doesn't exist, we're done
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
    
    # Fetch relevant knowledge for RAG (optional - gracefully degrades if no knowledge)
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
        # RAG is optional - don't fail the analysis if it fails
        logger.warning(
            "RAG knowledge retrieval failed, proceeding without",
            extra={"session_id": str(session_id), "error": str(e)}
        )
    
    # Analyze with AI
    try:
        frames = FrameSet(frames=frame_data, timestamps_seconds=frame_timestamps)
        
        analysis = await coach.analyze_video(
            frames=frames,
            stroke_type=analysis_request.stroke_type,
            user_notes=analysis_request.user_notes,
            knowledge_context=knowledge_context if knowledge_context else None,
        )
        
        # Update session with analysis
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
    
    # Convert to response format
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

