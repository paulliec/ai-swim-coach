"""
Server-side video analysis API endpoints.

This is the "AI coach in a box" endpoint - users upload a video and get
timestamp-linked feedback like a real coach would provide.

Workflow:
1. User uploads video file
2. Server stores in R2, extracts metadata
3. Server runs agentic analysis (multi-pass with frame requests)
4. User receives timestamped feedback: "At 0:12-0:15, your elbow drops..."

Why server-side vs client-side frame extraction:
- Works on all browsers (no Safari issues)
- AI can request specific frames on demand
- More consistent results
- Better for longer videos
"""

import logging
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from ...core.analysis.agentic_coach import AgenticSwimCoach, AgenticAnalysisResult, TimestampedFeedback
from ...core.analysis.models import StrokeType
from ..dependencies import (
    KnowledgeRepositoryDep,
    SessionRepositoryDep,
    SettingsDep,
    StorageClientDep,
    UsageLimitRepositoryDep,
    VideoProcessorDep,
    VisionClientDep,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class VideoUploadResponse(BaseModel):
    """Response after uploading a video for analysis."""
    session_id: UUID = Field(description="Unique session identifier")
    video_duration_seconds: float = Field(description="Video duration")
    resolution: str = Field(description="Video resolution (WxH)")
    message: str = Field(description="Status message")


class TimestampedFeedbackItem(BaseModel):
    """Single piece of feedback linked to video timestamps."""
    timestamp: str = Field(description="Timestamp range, e.g., '0:12-0:15'")
    timestamp_start_seconds: float = Field(description="Start timestamp in seconds")
    timestamp_end_seconds: Optional[float] = Field(description="End timestamp in seconds")
    category: str = Field(description="Technique category")
    priority: str = Field(description="Priority: primary, secondary, refinement")
    observation: str = Field(description="What was observed")
    recommendation: str = Field(description="What to do about it")
    drills: list[str] = Field(default_factory=list, description="Suggested drills")


class VideoAnalysisResponse(BaseModel):
    """Response with timestamped coaching analysis."""
    session_id: UUID = Field(description="Session identifier")
    stroke_type: str = Field(description="Stroke type analyzed")
    video_duration_seconds: float = Field(description="Video duration")
    summary: str = Field(description="Overall analysis summary")
    feedback: list[TimestampedFeedbackItem] = Field(description="Timestamped feedback")
    total_frames_analyzed: int = Field(description="Total frames the AI reviewed")
    analysis_iterations: int = Field(description="Number of analysis passes")


class VideoAnalysisRequest(BaseModel):
    """Request body for video analysis."""
    stroke_type: StrokeType = Field(
        default=StrokeType.FREESTYLE,
        description="Type of stroke to analyze"
    )
    user_notes: str = Field(
        default="",
        description="Additional context from the user"
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=VideoUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload video for analysis",
    description="Upload a swimming video for server-side analysis with timestamp-linked feedback",
)
async def upload_video(
    video: Annotated[UploadFile, File(description="Swimming video file (MP4, MOV, etc.)")],
    stroke_type: Annotated[StrokeType, Form()] = StrokeType.FREESTYLE,
    user_notes: Annotated[str, Form()] = "",
    x_user_id: Annotated[Optional[str], Header()] = None,
    storage: StorageClientDep = None,
    video_processor: VideoProcessorDep = None,
    settings: SettingsDep = None,
) -> VideoUploadResponse:
    """
    Upload a video for server-side analysis.
    
    This endpoint:
    1. Validates the video file
    2. Stores it in R2 object storage
    3. Extracts video metadata (duration, resolution, fps)
    4. Returns session ID for analysis
    
    Supported formats: MP4, MOV, AVI, WebM
    Max file size: Configured in settings (default 100MB)
    """
    session_id = uuid4()
    
    logger.info(
        "Receiving video upload",
        extra={
            "session_id": str(session_id),
            "filename": video.filename,
            "content_type": video.content_type,
            "user_id": x_user_id or "anonymous",
        }
    )
    
    # validate content type
    allowed_types = ["video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"]
    if video.content_type and video.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported video format: {video.content_type}. Use MP4, MOV, AVI, or WebM."
        )
    
    # read video data
    video_data = await video.read()
    
    # check size
    max_size = settings.max_video_size_mb * 1024 * 1024
    if len(video_data) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Video exceeds maximum size of {settings.max_video_size_mb}MB"
        )
    
    # get video metadata using FFmpeg
    try:
        video_info = await video_processor.get_video_info(video_data)
        
        logger.info(
            "Extracted video metadata",
            extra={
                "session_id": str(session_id),
                "duration": video_info.duration_seconds,
                "resolution": f"{video_info.width}x{video_info.height}",
                "fps": video_info.fps,
            }
        )
    except Exception as e:
        logger.error(f"Failed to extract video info: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not process video. Please ensure it's a valid video file."
        )
    
    # check duration limit (don't process 60-minute videos)
    max_duration = settings.max_video_duration_seconds
    if video_info.duration_seconds > max_duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video too long ({video_info.duration_seconds:.0f}s). Maximum is {max_duration}s."
        )
    
    # store video in R2
    try:
        storage_path = await storage.upload_video(
            video_data=video_data,
            session_id=session_id,
            filename=video.filename or "video.mp4",
        )
        
        logger.info(
            "Stored video",
            extra={
                "session_id": str(session_id),
                "storage_path": storage_path,
                "size_bytes": len(video_data),
            }
        )
    except Exception as e:
        logger.error(f"Failed to store video: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store video"
        )
    
    return VideoUploadResponse(
        session_id=session_id,
        video_duration_seconds=video_info.duration_seconds,
        resolution=f"{video_info.width}x{video_info.height}",
        message=f"Video uploaded successfully. Ready for analysis."
    )


@router.post(
    "/{session_id}/analyze",
    response_model=VideoAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze uploaded video",
    description="Run AI analysis on uploaded video with timestamp-linked feedback",
)
async def analyze_video(
    session_id: UUID,
    analysis_request: VideoAnalysisRequest,
    fastapi_request: Request,
    x_user_id: Annotated[Optional[str], Header()] = None,
    x_api_key: Annotated[Optional[str], Header()] = None,
    settings: SettingsDep = None,
    storage: StorageClientDep = None,
    video_processor: VideoProcessorDep = None,
    vision_client: VisionClientDep = None,
    usage_limit_repo: UsageLimitRepositoryDep = None,
    knowledge_repo: KnowledgeRepositoryDep = None,
) -> VideoAnalysisResponse:
    """
    Analyze an uploaded video with the agentic swim coach.
    
    This is the "AI coach in a box" endpoint. It:
    1. Checks rate limits
    2. Downloads video from storage
    3. Runs multi-pass agentic analysis
    4. Returns timestamp-linked feedback
    
    The analysis can take 30-90 seconds depending on video length.
    """
    logger.info(
        "Starting video analysis",
        extra={
            "session_id": str(session_id),
            "stroke_type": analysis_request.stroke_type.value,
        }
    )
    
    # check rate limits (unless bypassed)
    bypass_rate_limit = False
    if x_api_key and x_api_key in settings.rate_limit_bypass_keys_list:
        bypass_rate_limit = True
    
    if not bypass_rate_limit:
        identifier = x_user_id if x_user_id else (
            fastapi_request.client.host if fastapi_request.client else "unknown"
        )
        identifier_type = "user_id" if x_user_id else "ip_address"
        
        allowed, current_count, limit_max = usage_limit_repo.check_and_increment(
            identifier=identifier,
            identifier_type=identifier_type,
            resource_type="video_analysis",
            limit_max=3,
            period_hours=24
        )
        
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily limit of {limit_max} analyses reached. Try again tomorrow!"
            )
    
    # download video from storage
    try:
        # construct storage path (same pattern as upload)
        # try common extensions
        video_data = None
        for ext in ['mp4', 'mov', 'avi', 'webm']:
            try:
                storage_path = f"videos/{session_id}/original.{ext}"
                video_data = await storage.download_video(storage_path)
                break
            except Exception:
                continue
        
        if not video_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Video not found. Please upload first."
            )
        
        logger.debug(f"Downloaded video: {len(video_data)} bytes")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download video: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve video"
        )
    
    # get video info for duration
    try:
        video_info = await video_processor.get_video_info(video_data)
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process video"
        )
    
    # fetch RAG knowledge
    knowledge_context: list[str] = []
    try:
        chunks = knowledge_repo.get_relevant_for_stroke(
            stroke_type=analysis_request.stroke_type.value,
            analysis_summary=analysis_request.user_notes or None,
            limit=5
        )
        knowledge_context = [c.content for c in chunks]
        
        if knowledge_context:
            logger.info(f"Retrieved {len(knowledge_context)} RAG chunks")
    except Exception as e:
        logger.warning(f"RAG retrieval failed: {e}")
    
    # create agentic coach and analyze
    try:
        # wrap video processor to match VideoFrameExtractor protocol
        class FrameExtractorAdapter:
            def __init__(self, processor):
                self._processor = processor
            
            async def extract_frames_at_timestamps(self, video_data, timestamps):
                frames = await self._processor.extract_frames_at_timestamps(video_data, timestamps)
                return [(f.timestamp_seconds, f.data) for f in frames]
            
            async def extract_frames_at_fps(self, video_data, fps, max_frames=60):
                frames = await self._processor.extract_frames_at_fps(video_data, fps, max_frames)
                return [(f.timestamp_seconds, f.data) for f in frames]
        
        coach = AgenticSwimCoach(
            vision_client=vision_client,
            frame_extractor=FrameExtractorAdapter(video_processor),
            max_iterations=3,
            initial_fps=0.5,
        )
        
        result = await coach.analyze_video(
            video_data=video_data,
            video_duration=video_info.duration_seconds,
            stroke_type=analysis_request.stroke_type,
            user_notes=analysis_request.user_notes,
            knowledge_context=knowledge_context if knowledge_context else None,
        )
        
        logger.info(
            "Analysis complete",
            extra={
                "session_id": str(session_id),
                "iterations": len(result.iterations),
                "total_frames": result.total_frames_analyzed,
                "feedback_items": len(result.timestamped_feedback),
            }
        )
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}"
        )
    
    # convert to response
    feedback_items = [
        TimestampedFeedbackItem(
            timestamp=fb.timestamp_display,
            timestamp_start_seconds=fb.start_seconds,
            timestamp_end_seconds=fb.end_seconds,
            category=fb.category.value,
            priority=fb.priority.value,
            observation=fb.description,
            recommendation=fb.recommendation,
            drills=fb.drill_suggestions,
        )
        for fb in result.timestamped_feedback
    ]
    
    return VideoAnalysisResponse(
        session_id=session_id,
        stroke_type=result.stroke_type.value,
        video_duration_seconds=result.video_duration_seconds,
        summary=result.final_summary,
        feedback=feedback_items,
        total_frames_analyzed=result.total_frames_analyzed,
        analysis_iterations=len(result.iterations),
    )
