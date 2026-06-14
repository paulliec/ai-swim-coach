"""
Video-based analysis API endpoints (server-side processing).

Agentic flow: upload video → sparse scan → AI requests specific frames → detailed analysis.
"""

import asyncio
import json
import logging
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from ...config.settings import Settings
from ...core.analysis.models import (
    ANALYSIS_COMPLETE,
    ANALYSIS_FAILED,
    ANALYSIS_PROCESSING,
    AnalysisResult,
    CoachingFeedback,
    CoachingSession,
    FeedbackPriority,
    StrokeType,
    TechniqueCategory,
    TechniqueObservation,
    VideoMetadata,
)
from ...infrastructure.snowflake.client import create_snowflake_connection
from ...infrastructure.snowflake.repositories.knowledge import KnowledgeRepository
from ...infrastructure.snowflake.repositories.sessions import (
    SessionNotFoundError,
    SessionRepository,
)
from ...infrastructure.video.processor import ExtractedFrame
from ..dependencies import (
    AuthenticatedUser,
    SessionRepositoryDep,
    SettingsDep,
    StorageClientDep,
    VideoProcessorDep,
    UsageLimitRepositoryDep,
    _snowflake_config,
    get_mock_snowflake_connection,
    get_storage_client,
    get_video_processor,
    get_vision_client,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _save_agentic_session(
    session_repo,
    session_id: UUID,
    video_info,
    stroke_type: str,
    summary: str,
    timestamp_feedback: list,
    frame_count: int,
    status: str = ANALYSIS_COMPLETE,
    error: Optional[str] = None,
) -> None:
    """Save agentic result to Snowflake. Enriches summary with feedback for follow-up chat context.

    Loads the existing session first so we don't clobber conversation/video metadata
    written earlier (e.g. a partial result the user chatted with before resuming).
    """
    try:
        video = VideoMetadata(
            filename=f"session_{session_id}.mp4",
            duration_seconds=getattr(video_info, "duration_seconds", 0.0),
            resolution=(
                getattr(video_info, "width", 0),
                getattr(video_info, "height", 0),
            ),
            fps=getattr(video_info, "fps", 0.0),
            file_size_bytes=0,
            storage_path=f"videos/{session_id}/original.mp4",
        )

        feedback_items = []
        feedback_lines = []
        for fb in timestamp_feedback:
            observation_text = getattr(fb, "observation", str(fb))
            recommendation_text = getattr(fb, "recommendation", "")
            priority_text = getattr(fb, "priority", "secondary")
            category_text = getattr(fb, "category", "general")
            start_fmt = getattr(fb, "start_formatted", "")
            end_fmt = getattr(fb, "end_formatted", "")
            timestamp_range = f"{start_fmt}-{end_fmt}" if start_fmt else ""

            line = f"- [{priority_text}] {timestamp_range}: {observation_text}"
            if recommendation_text:
                line += f" → {recommendation_text}"
            feedback_lines.append(line)

            obs = TechniqueObservation(
                category=TechniqueCategory.BODY_POSITION,
                description=observation_text,
            )
            try:
                cf = CoachingFeedback(
                    priority=FeedbackPriority.SECONDARY,
                    observation=obs,
                    recommendation=recommendation_text,
                )
                feedback_items.append(cf)
            except Exception:
                pass  # skip malformed items

        enriched_summary = summary
        if feedback_lines:
            enriched_summary += "\n\nDetailed feedback:\n" + "\n".join(feedback_lines)

        analysis = AnalysisResult(
            stroke_type=StrokeType(stroke_type) if stroke_type in [s.value for s in StrokeType] else StrokeType.FREESTYLE,
            summary=enriched_summary,
            feedback=feedback_items,
            frame_count_analyzed=frame_count,
        )

        try:
            session = session_repo.get_session(session_id)
            session.video = video
            session.analysis = analysis
            session.status = status
            session.error = error
        except Exception:
            session = CoachingSession(
                id=session_id, video=video, analysis=analysis,
                status=status, error=error,
            )

        session_repo.save_session(session)
        logger.info(
            "Saved agentic session to Snowflake",
            extra={"session_id": str(session_id), "status": status},
        )
    except Exception as e:
        logger.warning(f"Could not save session to Snowflake (chat won't work): {e}")


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class VideoUploadResponse(BaseModel):
    """Response after uploading video for analysis."""
    session_id: UUID = Field(description="Session ID for subsequent analysis")
    video_path: str = Field(description="Storage path of uploaded video")
    duration_seconds: float = Field(description="Video duration")
    resolution: str = Field(description="Video resolution (WxH)")
    fps: float = Field(description="Video frame rate")
    message: str = Field(description="Status message")


class TimestampFeedback(BaseModel):
    """Feedback tied to a specific moment in the video."""
    start_timestamp: float = Field(description="Start of relevant section (seconds)")
    end_timestamp: float = Field(description="End of relevant section (seconds)")
    start_formatted: str = Field(description="Start timestamp as MM:SS")
    end_formatted: str = Field(description="End timestamp as MM:SS")
    category: str = Field(description="Technique category")
    observation: str = Field(description="What was observed")
    recommendation: str = Field(description="What to do about it")
    priority: str = Field(description="Priority level")


class AgenticAnalysisRequest(BaseModel):
    """Request for agentic video analysis."""
    stroke_type: StrokeType = Field(
        default=StrokeType.FREESTYLE,
        description="Type of stroke being analyzed"
    )
    user_notes: str = Field(
        default="",
        description="Additional context from the swimmer"
    )
    initial_fps: float = Field(
        default=0.5,
        description="Frames per second for initial sparse pass (default: 1 every 2 seconds)"
    )
    max_iterations: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum agent loop iterations (1-5)"
    )


class AnalysisIteration(BaseModel):
    """Progress from one iteration of the agentic loop."""
    iteration: int = Field(description="Iteration number (1-based)")
    frames_reviewed: int = Field(description="Frames reviewed so far")
    observations: str = Field(description="What the AI observed")
    areas_requested: list[str] = Field(
        default_factory=list,
        description="Timestamp ranges the AI wanted to examine closer"
    )


class AgenticJobResponse(BaseModel):
    """Acknowledgement that agentic analysis was queued (runs in the background)."""
    session_id: UUID = Field(description="Session identifier")
    status: str = Field(description="Job status (processing)")
    message: str = Field(description="Where to poll for the result")


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS."""
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins}:{secs:05.2f}"


# ---------------------------------------------------------------------------
# Agentic Analysis Prompts
# ---------------------------------------------------------------------------

INITIAL_ANALYSIS_SYSTEM_PROMPT = """You are an expert swim coach analyzing video frames. 

Your task is to:
1. Identify the swimmer's technique strengths
2. Spot areas that need improvement
3. **Crucially**: Identify specific timestamps where you need to see MORE frames for detailed analysis

After reviewing the initial frames, you MUST output in this JSON format:
{
    "initial_observations": "Brief description of what you see overall",
    "strengths": ["strength 1", "strength 2"],
    "areas_to_examine": [
        {
            "timestamp_start": 2.0,
            "timestamp_end": 3.5,
            "reason": "Need to see the catch phase more closely"
        }
    ],
    "ready_to_provide_feedback": false
}

If you've seen enough to provide complete feedback, set "ready_to_provide_feedback": true.
Otherwise, request specific timestamp ranges to examine more closely."""

DETAILED_ANALYSIS_SYSTEM_PROMPT = """You are an expert swim coach providing final analysis.

You have now seen:
- Initial sparse frames from the video
- Additional detailed frames from specific moments you requested

Provide your coaching feedback in this JSON format:
{
    "summary": "One paragraph overview of the swimmer's technique",
    "strengths": ["specific strength 1", "specific strength 2"],
    "timestamp_feedback": [
        {
            "start_timestamp": 2.0,
            "end_timestamp": 3.5,
            "category": "catch",
            "observation": "At 0:02-0:03.5, your elbow drops during the catch phase",
            "recommendation": "Focus on early vertical forearm - lead with your elbow",
            "priority": "primary"
        }
    ],
    "drills": ["Catch-up drill", "Fingertip drag"]
}

IMPORTANT:
- Reference specific timestamps in your observations (e.g., "At 0:12, your...")
- Use concrete cues, not abstract instructions
- Prioritize 1-2 key improvements"""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=VideoUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload video for analysis",
    description="Upload swimming video for server-side processing and analysis",
)
async def upload_video(
    video: Annotated[UploadFile, File(description="Swimming video (MP4, MOV, etc.)")],
    x_user_id: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    storage: StorageClientDep = None,
    video_processor: VideoProcessorDep = None,
    repository: SessionRepositoryDep = None,
    settings: SettingsDep = None,
) -> VideoUploadResponse:
    """Upload video, extract metadata, return session ID."""
    allowed_types = ["video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"]
    if video.content_type and video.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported video type: {video.content_type}. Use MP4, MOV, AVI, or WebM."
        )
    
    session_id = uuid4()
    
    logger.info(
        "Video upload started",
        extra={
            "session_id": str(session_id),
            "video_filename": video.filename,
            "content_type": video.content_type,
            "user_id": x_user_id or "anonymous",
        }
    )
    
    video_data = await video.read()

    max_size_bytes = settings.max_video_size_mb * 1024 * 1024
    if len(video_data) > max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Video too large. Maximum size: {settings.max_video_size_mb}MB"
        )
    
    try:
        video_info = await video_processor.get_video_info(video_data)
        
        logger.info(
            "Video metadata extracted",
            extra={
                "session_id": str(session_id),
                "duration": video_info.duration_seconds,
                "resolution": f"{video_info.width}x{video_info.height}",
                "fps": video_info.fps,
            }
        )
    except Exception as e:
        logger.error(f"Failed to extract video metadata: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not process video: {str(e)}"
        )
    
    max_duration = 120.0
    if video_info.duration_seconds > max_duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video too long ({video_info.duration_seconds:.0f}s). Maximum: {max_duration:.0f}s"
        )
    
    try:
        video_path = await storage.upload_video(
            video_data=video_data,
            session_id=session_id,
            filename=video.filename or "video.mp4",
        )
        
        logger.info(
            "Video stored",
            extra={
                "session_id": str(session_id),
                "path": video_path,
                "size_bytes": len(video_data),
            }
        )
    except Exception as e:
        logger.error(f"Failed to store video: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store video"
        )

    # Create the session row now (status=pending) so analyze can just load it and
    # flip it to processing — mirrors the frame-mode upload flow.
    try:
        session_video = VideoMetadata(
            filename=video.filename or "video.mp4",
            duration_seconds=video_info.duration_seconds,
            resolution=(video_info.width, video_info.height),
            fps=video_info.fps,
            file_size_bytes=len(video_data),
            storage_path=video_path,
        )
        repository.save_session(CoachingSession(id=session_id, video=session_video))
    except Exception as e:
        # Non-fatal: analyze() will create the row if it's missing.
        logger.warning(f"Could not pre-create session row: {e}", extra={"session_id": str(session_id)})

    return VideoUploadResponse(
        session_id=session_id,
        video_path=video_path,
        duration_seconds=video_info.duration_seconds,
        resolution=f"{video_info.width}x{video_info.height}",
        fps=video_info.fps,
        message=f"Video uploaded successfully. Duration: {video_info.duration_seconds:.1f}s. Ready for analysis.",
    )


# ---------------------------------------------------------------------------
# Background agentic workers
#
# The agentic flow is long (multiple vision passes + throttle sleeps). Running it
# inline blocks the request for minutes, so we run it in a BackgroundTask and let
# the client poll GET /api/v1/sessions/{id}. BackgroundTasks isn't durable, so the
# stale-job sweeper (src/api/sweeper.py) unsticks anything orphaned by a restart.
# ---------------------------------------------------------------------------

API_CALL_DELAY_SECONDS = 5.0  # preventive throttle; the Anthropic client also backs off


async def _load_video(storage, video_processor, session_id: UUID):
    """Download the uploaded video (any supported ext) and probe its metadata."""
    video_data = None
    for ext in ("mp4", "mov", "avi", "webm"):
        try:
            video_data = await storage.download_video(f"videos/{session_id}/original.{ext}")
            break
        except Exception:
            continue
    if video_data is None:
        raise FileNotFoundError("Video not found. Upload a video first.")
    video_info = await video_processor.get_video_info(video_data)
    return video_data, video_info


def _record_failure(session_repo, session_id: UUID, message: str) -> None:
    """Mark the session failed so the frontend stops polling and can surface the error."""
    try:
        try:
            session = session_repo.get_session(session_id)
        except Exception:
            session = CoachingSession(id=session_id)
        session.status = ANALYSIS_FAILED
        session.error = str(message)[:1000]
        session_repo.save_session(session)
        logger.info("Recorded agentic failure", extra={"session_id": str(session_id)})
    except Exception as e:
        logger.error(
            "Could not record agentic failure",
            extra={"session_id": str(session_id), "error": str(e)},
        )


def _save_partial(session_repo, session_id, video_info,
                  stroke_type, last_observations, frame_count) -> None:
    """Persist a partial (rate-limited) result. Resumability is signalled by the
    leftover analysis_state in R2 — GET /sessions reads that to set can_resume."""
    partial_summary = (
        f"⚠️ Partial analysis (API rate limit reached). Here's what I observed:\n\n{last_observations}"
    )
    _save_agentic_session(
        session_repo, session_id, video_info, stroke_type, partial_summary,
        [], frame_count, status=ANALYSIS_COMPLETE,
    )


async def _agentic_analyze_and_record(
    session_id: UUID,
    request: "AgenticAnalysisRequest",
    storage,
    vision_client,
    video_processor,
    session_repo: SessionRepository,
    knowledge_repo: KnowledgeRepository,
) -> None:
    """Fresh agentic analysis: sparse scan → AI-requested frames → detailed feedback.

    Persists status=complete on success, status=failed on hard error, and on a rate
    limit saves resume state + a partial result (status=complete, can_resume via R2 state).
    """
    try:
        video_data, video_info = await _load_video(storage, video_processor, session_id)
    except FileNotFoundError as e:
        _record_failure(session_repo, session_id, str(e))
        return
    except Exception as e:
        logger.error(f"Could not load video for agentic analysis: {e}", extra={"session_id": str(session_id)})
        _record_failure(session_repo, session_id, f"Could not load video: {e}")
        return

    try:
        knowledge_context = []
        try:
            knowledge_chunks = knowledge_repo.get_relevant_for_stroke(
                stroke_type=request.stroke_type.value,
                analysis_summary=request.user_notes if request.user_notes else None,
                limit=5,
            )
            knowledge_context = [chunk.content for chunk in knowledge_chunks]
        except Exception as e:
            logger.warning(f"RAG retrieval failed: {e}")

        all_frames: list[ExtractedFrame] = []
        iterations = 0
        ready_for_final = False
        analysis_progress: list[AnalysisIteration] = []  # progress shown to the user
        last_observations = ""  # kept for partial results

        initial_frames = await video_processor.extract_frames_at_fps(
            video_data=video_data,
            fps=request.initial_fps,
            max_frames=30,
        )
        all_frames.extend(initial_frames)
        logger.info("Extracted initial frames", extra={"count": len(initial_frames), "fps": request.initial_fps})

        frame_descriptions = "\n".join([
            f"- Frame {i+1}: timestamp {f.timestamp_formatted} ({f.timestamp_seconds:.2f}s)"
            for i, f in enumerate(initial_frames)
        ])

        user_prompt = f"""Analyzing swimming video ({video_info.duration_seconds:.1f}s duration).

Stroke type: {request.stroke_type.value}
Swimmer's notes: {request.user_notes or 'None provided'}

I'm showing you {len(initial_frames)} frames from the video:
{frame_descriptions}

Review these frames and tell me:
1. What you observe so far
2. What timestamp ranges you need to examine more closely

Respond in JSON format as instructed."""

        system_prompt = INITIAL_ANALYSIS_SYSTEM_PROMPT
        if knowledge_context:
            rag_section = "\n\nReference knowledge:\n" + "\n".join(
                f"- {chunk}" for chunk in knowledge_context[:3]
            )
            system_prompt += rag_section

        rate_limit_hit = False

        while iterations < request.max_iterations and not ready_for_final:
            iterations += 1

            if iterations > 1:
                logger.info(f"Throttling: waiting {API_CALL_DELAY_SECONDS}s before next API call")
                await asyncio.sleep(API_CALL_DELAY_SECONDS)

            frame_images = [f.data for f in all_frames]

            try:
                response = await vision_client.analyze_images(
                    images=frame_images,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Vision analysis failed: {error_msg}")
                # Rate limit with progress in hand → stop and offer resume.
                if "rate limit" in error_msg.lower() and analysis_progress:
                    rate_limit_hit = True
                    break
                raise  # other errors → outer handler records failure

            try:
                json_str = response
                if "```json" in response:
                    json_str = response.split("```json")[1].split("```")[0]
                elif "```" in response:
                    json_str = response.split("```")[1].split("```")[0]
                analysis = json.loads(json_str.strip())
            except json.JSONDecodeError:
                logger.warning("Could not parse JSON from response, treating as final")
                ready_for_final = True
                break

            current_observations = analysis.get("observations", analysis.get("summary", "Reviewing footage..."))
            last_observations = current_observations

            areas_to_examine = analysis.get("areas_to_examine", [])
            areas_requested_formatted = [
                f"{format_timestamp(a.get('timestamp_start', 0))} - {format_timestamp(a.get('timestamp_end', 0))}: {a.get('reason', 'closer look')}"
                for a in areas_to_examine[:3]
            ]

            analysis_progress.append(AnalysisIteration(
                iteration=iterations,
                frames_reviewed=len(all_frames),
                observations=current_observations[:500] if current_observations else "Analyzing...",
                areas_requested=areas_requested_formatted,
            ))

            logger.info(
                f"Iteration {iterations} complete",
                extra={
                    "observations": current_observations[:100] if current_observations else "",
                    "areas_requested": len(areas_to_examine),
                }
            )

            if analysis.get("ready_to_provide_feedback", False):
                ready_for_final = True
                break

            if not areas_to_examine:
                ready_for_final = True
                break

            additional_timestamps = []
            for area in areas_to_examine[:3]:  # limit to 3 areas per iteration
                start = area.get("timestamp_start", 0)
                end = area.get("timestamp_end", start + 1)
                t = start
                while t <= end and len(additional_timestamps) < 20:
                    additional_timestamps.append(t)
                    t += 0.2

            if additional_timestamps:
                new_frames = await video_processor.extract_frames_at_timestamps(
                    video_data=video_data,
                    timestamps=additional_timestamps,
                )
                all_frames.extend(new_frames)
                logger.info(
                    f"Extracted additional frames (iteration {iterations})",
                    extra={"count": len(new_frames), "timestamps": additional_timestamps[:5]}
                )

                frame_descriptions = "\n".join([
                    f"- Frame {i+1}: {f.timestamp_formatted}"
                    for i, f in enumerate(all_frames)
                ])
                user_prompt = f"""I've added more frames. You now have {len(all_frames)} total frames:
{frame_descriptions}

Continue your analysis. If you have enough information, provide final feedback.
Otherwise, request more specific timestamp ranges."""

        if rate_limit_hit:
            logger.info("Returning partial results due to rate limit, saving state for resume")
            state_to_save = {
                "iteration": iterations,
                "frame_timestamps": [f.timestamp_seconds for f in all_frames],
                "observations": last_observations,
                "analysis_progress": [p.model_dump() for p in analysis_progress],
                "stroke_type": request.stroke_type.value,
                "user_notes": request.user_notes,
                "initial_fps": request.initial_fps,
                "max_iterations": request.max_iterations,
                "ready_for_final": ready_for_final,
                "video_duration": video_info.duration_seconds,
            }
            try:
                await storage.save_analysis_state(session_id, state_to_save)
            except Exception as e:
                logger.warning(f"Failed to save state for resume: {e}")
            _save_partial(session_repo, session_id, video_info,
                          request.stroke_type.value, last_observations, len(all_frames))
            return

        logger.info(f"Throttling: waiting {API_CALL_DELAY_SECONDS}s before final analysis")
        await asyncio.sleep(API_CALL_DELAY_SECONDS)

        final_user_prompt = f"""You've reviewed {len(all_frames)} frames from this {video_info.duration_seconds:.1f}s swimming video.

Stroke: {request.stroke_type.value}
Swimmer's notes: {request.user_notes or 'None provided'}

Frame timestamps reviewed:
{chr(10).join([f"- {f.timestamp_formatted}" for f in all_frames[:20]])}
{"..." if len(all_frames) > 20 else ""}

Now provide your complete coaching feedback in JSON format.
IMPORTANT: Reference specific timestamps (e.g., "At 0:12-0:14, your catch...")"""

        try:
            final_response = await vision_client.analyze_images(
                images=[f.data for f in all_frames],
                system_prompt=DETAILED_ANALYSIS_SYSTEM_PROMPT,
                user_prompt=final_user_prompt,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Final analysis failed: {error_msg}")
            if "rate limit" in error_msg.lower() and analysis_progress:
                logger.info("Rate limit on final pass, saving state for resume")
                state_to_save = {
                    "iteration": iterations,
                    "frame_timestamps": [f.timestamp_seconds for f in all_frames],
                    "observations": last_observations,
                    "analysis_progress": [p.model_dump() for p in analysis_progress],
                    "stroke_type": request.stroke_type.value,
                    "user_notes": request.user_notes,
                    "initial_fps": request.initial_fps,
                    "max_iterations": request.max_iterations,
                    "ready_for_final": True,
                    "video_duration": video_info.duration_seconds,
                }
                try:
                    await storage.save_analysis_state(session_id, state_to_save)
                except Exception as e:
                    logger.warning(f"Failed to save state for resume: {e}")
                _save_partial(session_repo, session_id, video_info,
                              request.stroke_type.value, last_observations, len(all_frames))
                return
            raise

        try:
            json_str = final_response
            if "```json" in final_response:
                json_str = final_response.split("```json")[1].split("```")[0]
            elif "```" in final_response:
                json_str = final_response.split("```")[1].split("```")[0]
            final_analysis = json.loads(json_str.strip())
        except json.JSONDecodeError:
            final_analysis = {"summary": final_response, "strengths": [], "timestamp_feedback": [], "drills": []}

        timestamp_feedback = _build_timestamp_feedback(final_analysis)

        try:
            await storage.delete_analysis_state(session_id)
        except Exception:
            pass  # ignore cleanup errors

        _save_agentic_session(
            session_repo, session_id, video_info,
            request.stroke_type.value, final_analysis.get("summary", ""),
            timestamp_feedback, len(all_frames),
            status=ANALYSIS_COMPLETE,
        )
        logger.info(
            "Agentic analysis complete",
            extra={
                "session_id": str(session_id),
                "total_frames": len(all_frames),
                "iterations": iterations,
                "feedback_items": len(timestamp_feedback),
            }
        )

    except Exception as e:
        logger.error(f"Agentic analysis failed: {e}", extra={"session_id": str(session_id)})
        _record_failure(session_repo, session_id, str(e))


async def _agentic_resume_and_record(
    session_id: UUID,
    storage,
    vision_client,
    video_processor,
    session_repo: SessionRepository,
    knowledge_repo: KnowledgeRepository,
) -> None:
    """Resume an interrupted agentic analysis from saved R2 state."""
    saved_state = await storage.load_analysis_state(session_id)
    if not saved_state:
        _record_failure(session_repo, session_id, "No saved state to resume. Start a new analysis instead.")
        return

    try:
        video_data, video_info = await _load_video(storage, video_processor, session_id)
    except FileNotFoundError as e:
        _record_failure(session_repo, session_id, str(e))
        return
    except Exception as e:
        logger.error(f"Could not load video for resume: {e}", extra={"session_id": str(session_id)})
        _record_failure(session_repo, session_id, f"Could not load video: {e}")
        return

    try:
        saved_iteration = saved_state.get("iteration", 0)
        saved_frame_timestamps = saved_state.get("frame_timestamps", [])
        saved_observations = saved_state.get("observations", "")
        saved_progress = saved_state.get("analysis_progress", [])
        stroke_type = saved_state.get("stroke_type", "freestyle")
        user_notes = saved_state.get("user_notes", "")
        max_iterations = saved_state.get("max_iterations", 3)
        ready_for_final = saved_state.get("ready_for_final", False)

        analysis_progress = [AnalysisIteration(**p) for p in saved_progress]

        logger.info(f"Re-extracting {len(saved_frame_timestamps)} frames from saved timestamps")
        all_frames = await video_processor.extract_frames_at_timestamps(
            video_data=video_data,
            timestamps=saved_frame_timestamps,
        )

        knowledge_context = []
        try:
            knowledge_chunks = knowledge_repo.get_relevant_for_stroke(
                stroke_type=stroke_type,
                analysis_summary=user_notes if user_notes else None,
                limit=5,
            )
            knowledge_context = [chunk.content for chunk in knowledge_chunks]
        except Exception as e:
            logger.warning(f"RAG retrieval failed: {e}")

        rate_limit_hit = False
        iterations = saved_iteration
        last_observations = saved_observations
        resume_delay = 2.0  # resuming pays a smaller throttle than a fresh run

        system_prompt = INITIAL_ANALYSIS_SYSTEM_PROMPT
        if knowledge_context:
            rag_section = "\n\nReference knowledge:\n" + "\n".join(
                f"- {chunk}" for chunk in knowledge_context[:3]
            )
            system_prompt += rag_section

        if not ready_for_final:
            frame_descriptions = "\n".join([
                f"- Frame {i+1}: {f.timestamp_formatted}"
                for i, f in enumerate(all_frames)
            ])
            user_prompt = f"""Continuing analysis of swimming video. You have {len(all_frames)} frames:
{frame_descriptions}

Previous observations: {saved_observations}

Continue your analysis. If you have enough information, provide final feedback.
Otherwise, request more specific timestamp ranges."""

            while iterations < max_iterations and not ready_for_final:
                iterations += 1

                logger.info(f"Throttling: waiting {resume_delay}s before API call")
                await asyncio.sleep(resume_delay)

                frame_images = [f.data for f in all_frames]

                try:
                    response = await vision_client.analyze_images(
                        images=frame_images,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Vision analysis failed during resume: {error_msg}")
                    if "rate limit" in error_msg.lower() and analysis_progress:
                        rate_limit_hit = True
                        break
                    raise

                try:
                    json_str = response
                    if "```json" in response:
                        json_str = response.split("```json")[1].split("```")[0]
                    elif "```" in response:
                        json_str = response.split("```")[1].split("```")[0]
                    analysis = json.loads(json_str.strip())
                except json.JSONDecodeError:
                    ready_for_final = True
                    break

                current_observations = analysis.get("observations", analysis.get("summary", ""))
                last_observations = current_observations

                areas_to_examine = analysis.get("areas_to_examine", [])
                areas_requested_formatted = [
                    f"{format_timestamp(a.get('timestamp_start', 0))} - {format_timestamp(a.get('timestamp_end', 0))}"
                    for a in areas_to_examine[:3]
                ]

                analysis_progress.append(AnalysisIteration(
                    iteration=iterations,
                    frames_reviewed=len(all_frames),
                    observations=current_observations[:500] if current_observations else "Analyzing...",
                    areas_requested=areas_requested_formatted,
                ))

                if analysis.get("ready_to_provide_feedback", False) or not areas_to_examine:
                    ready_for_final = True
                    break

                additional_timestamps = []
                for area in areas_to_examine[:3]:
                    start = area.get("timestamp_start", 0)
                    end = area.get("timestamp_end", start + 1)
                    t = start
                    while t <= end and len(additional_timestamps) < 20:
                        additional_timestamps.append(t)
                        t += 0.2

                if additional_timestamps:
                    new_frames = await video_processor.extract_frames_at_timestamps(
                        video_data=video_data,
                        timestamps=additional_timestamps,
                    )
                    all_frames.extend(new_frames)
                    frame_descriptions = "\n".join([
                        f"- Frame {i+1}: {f.timestamp_formatted}"
                        for i, f in enumerate(all_frames)
                    ])
                    user_prompt = f"""I've added more frames. You now have {len(all_frames)} total frames:
{frame_descriptions}

Continue your analysis. If you have enough information, provide final feedback.
Otherwise, request more specific timestamp ranges."""

        if rate_limit_hit:
            logger.info("Rate limit hit again during resume, saving state")
            state_to_save = {
                "iteration": iterations,
                "frame_timestamps": [f.timestamp_seconds for f in all_frames],
                "observations": last_observations,
                "analysis_progress": [p.model_dump() for p in analysis_progress],
                "stroke_type": stroke_type,
                "user_notes": user_notes,
                "initial_fps": saved_state.get("initial_fps", 0.5),
                "max_iterations": max_iterations,
                "ready_for_final": ready_for_final,
                "video_duration": video_info.duration_seconds,
            }
            try:
                await storage.save_analysis_state(session_id, state_to_save)
            except Exception as e:
                logger.warning(f"Failed to save state: {e}")
            _save_partial(session_repo, session_id, video_info,
                          stroke_type, last_observations, len(all_frames))
            return

        logger.info(f"Throttling: waiting {resume_delay}s before final analysis")
        await asyncio.sleep(resume_delay)

        final_user_prompt = f"""You've reviewed {len(all_frames)} frames from this {video_info.duration_seconds:.1f}s swimming video.

Stroke: {stroke_type}
Swimmer's notes: {user_notes or 'None provided'}

Frame timestamps reviewed:
{chr(10).join([f"- {f.timestamp_formatted}" for f in all_frames[:20]])}
{"..." if len(all_frames) > 20 else ""}

Now provide your complete coaching feedback in JSON format.
IMPORTANT: Reference specific timestamps (e.g., "At 0:12-0:14, your catch...")"""

        try:
            final_response = await vision_client.analyze_images(
                images=[f.data for f in all_frames],
                system_prompt=DETAILED_ANALYSIS_SYSTEM_PROMPT,
                user_prompt=final_user_prompt,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Final analysis failed during resume: {error_msg}")
            if "rate limit" in error_msg.lower():
                state_to_save = {
                    "iteration": iterations,
                    "frame_timestamps": [f.timestamp_seconds for f in all_frames],
                    "observations": last_observations,
                    "analysis_progress": [p.model_dump() for p in analysis_progress],
                    "stroke_type": stroke_type,
                    "user_notes": user_notes,
                    "initial_fps": saved_state.get("initial_fps", 0.5),
                    "max_iterations": max_iterations,
                    "ready_for_final": True,
                    "video_duration": video_info.duration_seconds,
                }
                try:
                    await storage.save_analysis_state(session_id, state_to_save)
                except Exception:
                    pass
                _save_partial(session_repo, session_id, video_info,
                              stroke_type, last_observations, len(all_frames))
                return
            raise

        try:
            json_str = final_response
            if "```json" in final_response:
                json_str = final_response.split("```json")[1].split("```")[0]
            elif "```" in final_response:
                json_str = final_response.split("```")[1].split("```")[0]
            final_analysis = json.loads(json_str.strip())
        except json.JSONDecodeError:
            final_analysis = {"summary": final_response, "strengths": [], "timestamp_feedback": [], "drills": []}

        timestamp_feedback = _build_timestamp_feedback(final_analysis)

        try:
            await storage.delete_analysis_state(session_id)
        except Exception:
            pass

        _save_agentic_session(
            session_repo, session_id, video_info,
            stroke_type, final_analysis.get("summary", ""),
            timestamp_feedback, len(all_frames),
            status=ANALYSIS_COMPLETE,
        )
        logger.info(
            "Resume analysis complete",
            extra={"session_id": str(session_id), "total_frames": len(all_frames), "iterations": iterations}
        )

    except Exception as e:
        logger.error(f"Resume analysis failed: {e}", extra={"session_id": str(session_id)})
        _record_failure(session_repo, session_id, str(e))


def _build_timestamp_feedback(final_analysis: dict) -> list:
    """Map the model's JSON feedback into TimestampFeedback items."""
    items = []
    for fb in final_analysis.get("timestamp_feedback", []):
        start = fb.get("start_timestamp", 0)
        end = fb.get("end_timestamp", start)
        items.append(TimestampFeedback(
            start_timestamp=start,
            end_timestamp=end,
            start_formatted=format_timestamp(start),
            end_formatted=format_timestamp(end),
            category=fb.get("category", "general"),
            observation=fb.get("observation", ""),
            recommendation=fb.get("recommendation", ""),
            priority=fb.get("priority", "secondary"),
        ))
    return items


async def _run_agentic_analysis(session_id: UUID, request: "AgenticAnalysisRequest", settings: Settings) -> None:
    """Background entrypoint — builds its own resources (request-scoped deps are gone here)."""
    storage = get_storage_client(settings)
    vision_client = get_vision_client(settings)
    video_processor = get_video_processor(settings)
    if settings.snowflake_mock_mode:
        conn = get_mock_snowflake_connection()
        await _agentic_analyze_and_record(
            session_id, request, storage, vision_client, video_processor,
            SessionRepository(conn), KnowledgeRepository(conn),
        )
    else:
        with create_snowflake_connection(config=_snowflake_config(settings)) as conn:
            await _agentic_analyze_and_record(
                session_id, request, storage, vision_client, video_processor,
                SessionRepository(conn), KnowledgeRepository(conn),
            )


async def _run_agentic_resume(session_id: UUID, settings: Settings) -> None:
    """Background entrypoint for resume."""
    storage = get_storage_client(settings)
    vision_client = get_vision_client(settings)
    video_processor = get_video_processor(settings)
    if settings.snowflake_mock_mode:
        conn = get_mock_snowflake_connection()
        await _agentic_resume_and_record(
            session_id, storage, vision_client, video_processor,
            SessionRepository(conn), KnowledgeRepository(conn),
        )
    else:
        with create_snowflake_connection(config=_snowflake_config(settings)) as conn:
            await _agentic_resume_and_record(
                session_id, storage, vision_client, video_processor,
                SessionRepository(conn), KnowledgeRepository(conn),
            )


# ---------------------------------------------------------------------------
# Endpoints (async: return 202, work runs in the background)
# ---------------------------------------------------------------------------

@router.post(
    "/{session_id}/analyze",
    response_model=AgenticJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start agentic video analysis (async)",
    description="Queue multi-pass AI analysis. Returns immediately; poll GET /api/v1/sessions/{session_id} for status and feedback.",
)
async def analyze_video_agentic(
    session_id: UUID,
    request: AgenticAnalysisRequest,
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    x_user_id: Annotated[Optional[str], Header()] = None,
    x_api_key: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    settings: SettingsDep = None,
    session_repo: SessionRepositoryDep = None,
    usage_limit_repo: UsageLimitRepositoryDep = None,
) -> AgenticJobResponse:
    """Queue agentic analysis and return 202. Rate-limited (3/day per user unless bypassed)."""
    logger.info(
        "Queuing agentic analysis",
        extra={
            "session_id": str(session_id),
            "stroke_type": request.stroke_type.value,
            "initial_fps": request.initial_fps,
        }
    )

    bypass_rate_limit = False
    if x_api_key and x_api_key in settings.rate_limit_bypass_keys_list:
        bypass_rate_limit = True
        logger.info("Rate limit bypassed via API key")
    elif x_user_id and x_user_id in settings.rate_limit_bypass_user_ids_list:
        bypass_rate_limit = True
        logger.info(f"Rate limit bypassed for user ID {x_user_id}")

    x_user_email = fastapi_request.headers.get("x-user-email", "").lower()
    if x_user_email and x_user_email in settings.rate_limit_bypass_emails_list:
        bypass_rate_limit = True
        logger.info(f"Rate limit bypassed for email {x_user_email}")

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
            period_hours=24,
        )

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily limit reached ({limit_max} analyses). Come back tomorrow!"
            )

    # Verify the session exists (created at upload), then flag processing so the
    # first poll is honest. The heavy work runs in the background.
    try:
        session = session_repo.get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found. Upload a video first.")
    except Exception as e:
        logger.error("Session lookup failed", extra={"session_id": str(session_id), "error": str(e)})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found. Upload a video first.")

    session.status = ANALYSIS_PROCESSING
    session.error = None
    try:
        session_repo.save_session(session)
    except Exception as e:
        logger.error("Failed to mark session processing", extra={"session_id": str(session_id), "error": str(e)})
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to start analysis")

    background_tasks.add_task(_run_agentic_analysis, session_id, request, settings)

    return AgenticJobResponse(
        session_id=session_id,
        status=ANALYSIS_PROCESSING,
        message=f"Analysis started. Poll GET /api/v1/sessions/{session_id} for status and feedback.",
    )


@router.post(
    "/{session_id}/resume",
    response_model=AgenticJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resume interrupted analysis (async)",
    description="Continue an interrupted analysis in the background. Poll GET /api/v1/sessions/{session_id} for the result.",
)
async def resume_video_analysis(
    session_id: UUID,
    background_tasks: BackgroundTasks,
    x_user_id: Annotated[Optional[str], Header()] = None,
    x_api_key: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    settings: SettingsDep = None,
    storage: StorageClientDep = None,
    session_repo: SessionRepositoryDep = None,
) -> AgenticJobResponse:
    """Queue a resume of an interrupted analysis. Resuming is free (already paid for)."""
    logger.info(f"Queuing resume for session {session_id}")

    saved_state = await storage.load_analysis_state(session_id)
    if not saved_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No saved state found for this session. Start a new analysis instead."
        )

    try:
        session = session_repo.get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    except Exception as e:
        logger.error("Session lookup failed", extra={"session_id": str(session_id), "error": str(e)})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    session.status = ANALYSIS_PROCESSING
    session.error = None
    try:
        session_repo.save_session(session)
    except Exception as e:
        logger.error("Failed to mark session processing", extra={"session_id": str(session_id), "error": str(e)})
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to start resume")

    background_tasks.add_task(_run_agentic_resume, session_id, settings)

    return AgenticJobResponse(
        session_id=session_id,
        status=ANALYSIS_PROCESSING,
        message=f"Resuming analysis. Poll GET /api/v1/sessions/{session_id} for status and feedback.",
    )
