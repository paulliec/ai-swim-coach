"""
Video-based analysis API endpoints (server-side processing).

This is the agentic analysis flow:
1. User uploads video → stored in R2
2. Server extracts initial frames (sparse)
3. AI analyzes and identifies areas needing more detail
4. Server extracts additional frames at requested timestamps
5. AI re-analyzes with full context
6. Returns feedback with timestamp references

Why this flow:
- Works on all browsers (no Safari headaches)
- AI can request specific moments ("show me 0:12-0:15")
- Enables timestamp-linked feedback like a real coach
- Separates upload from analysis for better UX
"""

import logging
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from ...core.analysis.models import StrokeType
from ..dependencies import (
    AuthenticatedUser,
    KnowledgeRepositoryDep,
    SessionRepositoryDep,
    SettingsDep,
    StorageClientDep,
    VideoProcessorDep,
    VisionClientDep,
    UsageLimitRepositoryDep,
)
from ...infrastructure.video.processor import ExtractedFrame

logger = logging.getLogger(__name__)

router = APIRouter()


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


class AgenticAnalysisResponse(BaseModel):
    """Response with timestamp-linked coaching feedback."""
    session_id: UUID = Field(description="Session identifier")
    stroke_type: str = Field(description="Stroke analyzed")
    summary: str = Field(description="Overall analysis summary")
    strengths: list[str] = Field(description="What's working well")
    timestamp_feedback: list[TimestampFeedback] = Field(
        description="Feedback linked to specific video moments"
    )
    drills: list[str] = Field(description="Recommended drills")
    total_frames_analyzed: int = Field(description="Total frames the AI reviewed")
    iterations_used: int = Field(description="How many analysis passes were needed")
    analysis_progress: list[AnalysisIteration] = Field(
        default_factory=list,
        description="Progress from each iteration (shows what AI observed along the way)"
    )
    partial: bool = Field(
        default=False,
        description="True if analysis was interrupted (e.g., rate limit) but partial results available"
    )


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
    settings: SettingsDep = None,
) -> VideoUploadResponse:
    """
    Upload a video for server-side analysis.
    
    The video is stored in R2 and metadata is extracted via FFmpeg.
    Returns a session_id for subsequent analysis.
    
    Max file size: configured in settings (default 100MB)
    Supported formats: MP4, MOV, AVI, WebM
    """
    # validate file type
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
    
    # read video data
    video_data = await video.read()
    
    # check size
    max_size_bytes = settings.max_video_size_mb * 1024 * 1024
    if len(video_data) > max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Video too large. Maximum size: {settings.max_video_size_mb}MB"
        )
    
    # get video metadata
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
    
    # check duration (limit to 2 minutes for now)
    max_duration = 120.0  # 2 minutes
    if video_info.duration_seconds > max_duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video too long ({video_info.duration_seconds:.0f}s). Maximum: {max_duration:.0f}s"
        )
    
    # store in R2
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
    
    return VideoUploadResponse(
        session_id=session_id,
        video_path=video_path,
        duration_seconds=video_info.duration_seconds,
        resolution=f"{video_info.width}x{video_info.height}",
        fps=video_info.fps,
        message=f"Video uploaded successfully. Duration: {video_info.duration_seconds:.1f}s. Ready for analysis.",
    )


@router.post(
    "/{session_id}/analyze",
    response_model=AgenticAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze video with agentic flow",
    description="Run multi-pass AI analysis with timestamp-specific frame requests",
)
async def analyze_video_agentic(
    session_id: UUID,
    request: AgenticAnalysisRequest,
    fastapi_request: Request,
    x_user_id: Annotated[Optional[str], Header()] = None,
    x_api_key: Annotated[Optional[str], Header()] = None,
    api_key: AuthenticatedUser = None,
    settings: SettingsDep = None,
    storage: StorageClientDep = None,
    video_processor: VideoProcessorDep = None,
    vision_client: VisionClientDep = None,
    knowledge_repo: KnowledgeRepositoryDep = None,
    usage_limit_repo: UsageLimitRepositoryDep = None,
) -> AgenticAnalysisResponse:
    """
    Perform agentic video analysis.
    
    This endpoint:
    1. Downloads the video from R2
    2. Extracts initial sparse frames
    3. AI reviews and requests more frames from specific timestamps
    4. Extracts additional frames at requested timestamps
    5. AI provides final feedback with timestamp references
    
    The agentic loop continues until:
    - AI says it has enough information
    - Max iterations reached
    - No more frames requested
    """
    logger.info(
        "Starting agentic analysis",
        extra={
            "session_id": str(session_id),
            "stroke_type": request.stroke_type.value,
            "initial_fps": request.initial_fps,
        }
    )
    
    # rate limit check
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
            period_hours=24,
        )
        
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily limit reached ({limit_max} analyses). Come back tomorrow!"
            )
    
    # download video from storage
    video_path = f"videos/{session_id}/original.mp4"  # assume mp4 for now
    try:
        video_data = await storage.download_video(video_path)
    except Exception as e:
        # try other extensions
        for ext in ["mov", "avi", "webm"]:
            try:
                video_path = f"videos/{session_id}/original.{ext}"
                video_data = await storage.download_video(video_path)
                break
            except:
                continue
        else:
            logger.error(f"Video not found for session {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Video not found. Upload a video first."
            )
    
    # get video info
    video_info = await video_processor.get_video_info(video_data)
    
    # fetch RAG knowledge
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
    
    # === AGENTIC LOOP ===
    all_frames: list[ExtractedFrame] = []
    iterations = 0
    ready_for_final = False
    analysis_progress: list[AnalysisIteration] = []  # track progress for user feedback
    last_observations = ""  # keep last observations for partial results
    
    # step 1: extract initial sparse frames
    initial_frames = await video_processor.extract_frames_at_fps(
        video_data=video_data,
        fps=request.initial_fps,
        max_frames=30,
    )
    all_frames.extend(initial_frames)
    
    logger.info(
        f"Extracted initial frames",
        extra={"count": len(initial_frames), "fps": request.initial_fps}
    )
    
    # build initial prompt with frame timestamps
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

    # add knowledge context if available
    system_prompt = INITIAL_ANALYSIS_SYSTEM_PROMPT
    if knowledge_context:
        rag_section = "\n\nReference knowledge:\n" + "\n".join(
            f"- {chunk}" for chunk in knowledge_context[:3]
        )
        system_prompt += rag_section
    
    # agentic loop
    import json
    rate_limit_hit = False
    
    while iterations < request.max_iterations and not ready_for_final:
        iterations += 1
        
        # send frames to vision model
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
            
            # check if rate limit - if we have any progress, return partial results
            if "rate limit" in error_msg.lower() and analysis_progress:
                rate_limit_hit = True
                logger.info("Rate limit hit but we have partial results, returning those")
                break
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AI analysis failed: {error_msg}"
            )
        
        # parse response
        try:
            # extract JSON from response (might be wrapped in markdown)
            json_str = response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0]
            
            analysis = json.loads(json_str.strip())
        except json.JSONDecodeError:
            logger.warning(f"Could not parse JSON from response, treating as final")
            ready_for_final = True
            break
        
        # track this iteration's progress
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
            observations=current_observations[:500] if current_observations else "Analyzing...",  # truncate long obs
            areas_requested=areas_requested_formatted,
        ))
        
        logger.info(
            f"Iteration {iterations} complete",
            extra={
                "observations": current_observations[:100] if current_observations else "",
                "areas_requested": len(areas_to_examine),
            }
        )
        
        # check if ready for final feedback
        if analysis.get("ready_to_provide_feedback", False):
            ready_for_final = True
            break
        
        # get requested timestamp ranges
        if not areas_to_examine:
            ready_for_final = True
            break
        
        # extract additional frames at requested timestamps
        additional_timestamps = []
        for area in areas_to_examine[:3]:  # limit to 3 areas per iteration
            start = area.get("timestamp_start", 0)
            end = area.get("timestamp_end", start + 1)
            # extract frames every 0.2s in the requested range
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
            
            # update prompt for next iteration
            frame_descriptions = "\n".join([
                f"- Frame {i+1}: {f.timestamp_formatted}"
                for i, f in enumerate(all_frames)
            ])
            user_prompt = f"""I've added more frames. You now have {len(all_frames)} total frames:
{frame_descriptions}

Continue your analysis. If you have enough information, provide final feedback.
Otherwise, request more specific timestamp ranges."""
    
    # === FINAL ANALYSIS ===
    # if rate limit hit during iterations, return partial results instead of failing
    if rate_limit_hit:
        logger.info("Returning partial results due to rate limit")
        return AgenticAnalysisResponse(
            session_id=session_id,
            stroke_type=request.stroke_type.value,
            summary=f"⚠️ Partial analysis (API rate limit reached). Here's what I observed:\n\n{last_observations}",
            strengths=[],
            timestamp_feedback=[],
            drills=["Try again in 1-2 minutes for complete analysis"],
            total_frames_analyzed=len(all_frames),
            iterations_used=iterations,
            analysis_progress=analysis_progress,
            partial=True,
        )
    
    # now get the detailed feedback with timestamp references
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
        
        # if rate limit on final pass, return what we have
        if "rate limit" in error_msg.lower() and analysis_progress:
            logger.info("Rate limit on final pass, returning partial results")
            return AgenticAnalysisResponse(
                session_id=session_id,
                stroke_type=request.stroke_type.value,
                summary=f"⚠️ Partial analysis (API rate limit reached). Here's what I observed:\n\n{last_observations}",
                strengths=[],
                timestamp_feedback=[],
                drills=["Try again in 1-2 minutes for complete analysis"],
                total_frames_analyzed=len(all_frames),
                iterations_used=iterations,
                analysis_progress=analysis_progress,
                partial=True,
            )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Final analysis failed"
        )
    
    # parse final response
    try:
        json_str = final_response
        if "```json" in final_response:
            json_str = final_response.split("```json")[1].split("```")[0]
        elif "```" in final_response:
            json_str = final_response.split("```")[1].split("```")[0]
        
        final_analysis = json.loads(json_str.strip())
    except json.JSONDecodeError:
        # fallback: use raw response as summary
        final_analysis = {
            "summary": final_response,
            "strengths": [],
            "timestamp_feedback": [],
            "drills": [],
        }
    
    # build response
    timestamp_feedback = []
    for fb in final_analysis.get("timestamp_feedback", []):
        start = fb.get("start_timestamp", 0)
        end = fb.get("end_timestamp", start)
        timestamp_feedback.append(TimestampFeedback(
            start_timestamp=start,
            end_timestamp=end,
            start_formatted=format_timestamp(start),
            end_formatted=format_timestamp(end),
            category=fb.get("category", "general"),
            observation=fb.get("observation", ""),
            recommendation=fb.get("recommendation", ""),
            priority=fb.get("priority", "secondary"),
        ))
    
    logger.info(
        "Agentic analysis complete",
        extra={
            "session_id": str(session_id),
            "total_frames": len(all_frames),
            "iterations": iterations,
            "feedback_items": len(timestamp_feedback),
        }
    )
    
    return AgenticAnalysisResponse(
        session_id=session_id,
        stroke_type=request.stroke_type.value,
        summary=final_analysis.get("summary", ""),
        strengths=final_analysis.get("strengths", []),
        timestamp_feedback=timestamp_feedback,
        drills=final_analysis.get("drills", []),
        total_frames_analyzed=len(all_frames),
        iterations_used=iterations,
        analysis_progress=analysis_progress,
        partial=False,
    )
