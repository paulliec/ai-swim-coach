"""
Video analysis API endpoints.

Upload frames, then analyze with AI.
"""

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from typing import Optional

from ...config.settings import Settings
from ...core.analysis.coach import FrameSet, SwimCoach
from ...core.analysis.models import (
    ANALYSIS_COMPLETE,
    ANALYSIS_FAILED,
    ANALYSIS_PROCESSING,
    CoachingSession,
    StrokeType,
    VideoMetadata,
)
from ...infrastructure.snowflake.client import create_snowflake_connection
from ...infrastructure.snowflake.repositories.knowledge import KnowledgeRepository
from ...infrastructure.snowflake.repositories.sessions import SessionRepository
from ..dependencies import (
    AuthenticatedUser,
    SessionRepositoryDep,
    SettingsDep,
    StorageClientDep,
    SwimCoachDep,
    UsageLimitRepositoryDep,
    _snowflake_config,
    get_mock_snowflake_connection,
    get_storage_client,
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


class AnalysisJobResponse(BaseModel):
    """Acknowledgement that analysis has been queued (run in the background)."""
    session_id: UUID = Field(description="Session identifier")
    status: str = Field(description="Job status (processing)")
    message: str = Field(description="Where to poll for the result")


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


# ---------------------------------------------------------------------------
# Background analysis job
# ---------------------------------------------------------------------------

# Frames are uploaded as frames/{session_id}/{n:04d}.jpg at 0.5s spacing.
# TODO: fix later - frame count is probed, not tracked. Kept at 20 to preserve prior
# behavior; bump toward max_frames_per_upload once cost/latency tradeoff is decided.
_MAX_PROBE_FRAMES = 20


async def _load_frames(storage, session_id: UUID) -> tuple[list[bytes], list[float]]:
    """Pull uploaded frames back out of storage, in order, until they run out."""
    frame_data: list[bytes] = []
    timestamps: list[float] = []
    for frame_num in range(_MAX_PROBE_FRAMES):
        try:
            data = await storage.download_frame(f"frames/{session_id}/{frame_num:04d}.jpg")
        except Exception:
            break
        frame_data.append(data)
        timestamps.append(frame_num * 0.5)
    return frame_data, timestamps


async def _analyze_and_record(
    session_id: UUID,
    analysis_request: "AnalysisRequest",
    coach: SwimCoach,
    storage,
    repository: SessionRepository,
    knowledge_repo: KnowledgeRepository,
) -> None:
    """The actual work: load frames, run Claude, persist result or failure."""
    try:
        session = repository.get_session(session_id)
    except Exception as e:
        # No session row to record status against — nothing we can do but log.
        logger.error("Background analysis: session vanished", extra={"session_id": str(session_id), "error": str(e)})
        return

    try:
        frame_data, frame_timestamps = await _load_frames(storage, session_id)
        if not frame_data:
            raise RuntimeError("No frames found for this session")

        # RAG is optional — gracefully degrades if no knowledge
        knowledge_context: list[str] = []
        try:
            chunks = knowledge_repo.get_relevant_for_stroke(
                stroke_type=analysis_request.stroke_type.value,
                analysis_summary=analysis_request.user_notes or None,
                limit=5,
            )
            knowledge_context = [c.content for c in chunks]
        except Exception as e:
            logger.warning("RAG retrieval failed, proceeding without", extra={"session_id": str(session_id), "error": str(e)})

        frames = FrameSet(frames=frame_data, timestamps_seconds=frame_timestamps)
        analysis = await coach.analyze_video(
            frames=frames,
            stroke_type=analysis_request.stroke_type,
            user_notes=analysis_request.user_notes,
            knowledge_context=knowledge_context or None,
        )

        session.analysis = analysis
        session.status = ANALYSIS_COMPLETE
        session.error = None
        repository.save_session(session)
        logger.info("Analysis complete", extra={"session_id": str(session_id), "frame_count": len(frame_data)})

    except Exception as e:
        logger.error("Analysis failed", extra={"session_id": str(session_id), "error": str(e)})
        try:
            session.status = ANALYSIS_FAILED
            session.error = str(e)[:1000]
            repository.save_session(session)
        except Exception as save_err:
            logger.error("Could not record failure status", extra={"session_id": str(session_id), "error": str(save_err)})


async def _run_analysis(
    session_id: UUID,
    analysis_request: "AnalysisRequest",
    settings: Settings,
    coach: SwimCoach,
) -> None:
    """Background entrypoint — builds its own resources.

    Request-scoped `yield` dependencies are torn down before background tasks run
    (FastAPI >=0.106), so we can't reuse the request's repo connection here.
    """
    storage = get_storage_client(settings)
    if settings.snowflake_mock_mode:
        conn = get_mock_snowflake_connection()
        await _analyze_and_record(
            session_id, analysis_request, coach, storage,
            SessionRepository(conn), KnowledgeRepository(conn),
        )
    else:
        with create_snowflake_connection(config=_snowflake_config(settings)) as conn:
            await _analyze_and_record(
                session_id, analysis_request, coach, storage,
                SessionRepository(conn), KnowledgeRepository(conn),
            )


@router.post(
    "/{session_id}/analyze",
    response_model=AnalysisJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start frame analysis (async)",
    description="Queue AI analysis. Returns immediately; poll GET /api/v1/sessions/{session_id} for status and feedback.",
)
async def analyze_session(
    session_id: UUID,
    analysis_request: AnalysisRequest,
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    x_user_id: Annotated[Optional[str], Header()] = None,
    x_api_key: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    settings: SettingsDep = None,
    coach: SwimCoachDep = None,
    repository: SessionRepositoryDep = None,
    usage_limit_repo: UsageLimitRepositoryDep = None,
) -> AnalysisJobResponse:
    """Queue analysis and return 202. Rate-limited (3/day per user unless bypassed)."""
    logger.info(
        "Queuing analysis",
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

    # Verify the session exists, then flag it processing so the first poll is honest.
    try:
        session = repository.get_session(session_id)
    except Exception as e:
        logger.error("Session not found", extra={"session_id": str(session_id), "error": str(e)})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    session.status = ANALYSIS_PROCESSING
    session.error = None
    try:
        repository.save_session(session)
    except Exception as e:
        logger.error("Failed to mark session processing", extra={"session_id": str(session_id), "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start analysis"
        )

    background_tasks.add_task(_run_analysis, session_id, analysis_request, settings, coach)

    return AnalysisJobResponse(
        session_id=session_id,
        status=ANALYSIS_PROCESSING,
        message=f"Analysis started. Poll GET /api/v1/sessions/{session_id} for status and feedback.",
    )

