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

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status
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
    SessionRepositoryDep,
    SettingsDep,
    StorageClientDep,
    SwimCoachDep,
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
    request: AnalysisRequest,
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    coach: SwimCoachDep = None,
    storage: StorageClientDep = None,
    repository: SessionRepositoryDep = None,
) -> AnalysisResponse:
    """
    Analyze frames and generate coaching feedback.
    
    This is the expensive operation - it:
    1. Loads frames from storage
    2. Sends them to Claude for analysis
    3. Parses the response into structured feedback
    4. Saves results to database
    
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
            "stroke_type": request.stroke_type.value,
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
    
    # Analyze with AI
    try:
        frames = FrameSet(frames=frame_data, timestamps_seconds=frame_timestamps)
        
        analysis = await coach.analyze_video(
            frames=frames,
            stroke_type=request.stroke_type,
            user_notes=request.user_notes,
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

