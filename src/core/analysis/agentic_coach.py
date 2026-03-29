"""
Agentic swim coach — multi-pass video analysis.

Two passes: cheap wide scan first, targeted detail second.
Mimics how a real coach reviews video.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol
from uuid import UUID, uuid4

from .coach import FrameSet, SwimCoach, VisionModelClient
from .models import (
    AnalysisResult,
    CoachingFeedback,
    FeedbackPriority,
    StrokeType,
    TechniqueCategory,
    TechniqueObservation,
    TimeRange,
    Timestamp,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

class VideoFrameExtractor(Protocol):
    """Interface for extracting frames from video."""
    
    async def extract_frames_at_timestamps(
        self,
        video_data: bytes,
        timestamps: list[float],
    ) -> list[tuple[float, bytes]]:
        """Extract frames at specific timestamps, return (timestamp, jpeg_data) pairs."""
        ...
    
    async def extract_frames_at_fps(
        self,
        video_data: bytes,
        fps: float,
        max_frames: int = 60,
    ) -> list[tuple[float, bytes]]:
        """Extract frames at regular intervals."""
        ...


# ---------------------------------------------------------------------------
# Data Models for Agentic Analysis
# ---------------------------------------------------------------------------

@dataclass
class TimestampedFeedback:
    """Feedback linked to specific video timestamps."""
    category: TechniqueCategory
    description: str
    recommendation: str
    start_seconds: float
    end_seconds: Optional[float] = None
    priority: FeedbackPriority = FeedbackPriority.SECONDARY
    drill_suggestions: list[str] = field(default_factory=list)
    
    @property
    def timestamp_display(self) -> str:
        """Format timestamps for display: '0:12-0:15' or '0:12'"""
        start = self._format_timestamp(self.start_seconds)
        if self.end_seconds and self.end_seconds != self.start_seconds:
            end = self._format_timestamp(self.end_seconds)
            return f"{start}-{end}"
        return start
    
    def _format_timestamp(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}:{secs:04.1f}"


@dataclass
class FrameRequest:
    """A request from the AI for more frames at specific times."""
    start_seconds: float
    end_seconds: float
    reason: str
    fps: float = 2.0  # how many frames per second in this range
    
    # max window the AI can request - forces narrow, targeted asks
    MAX_WINDOW_SECONDS: float = 2.0

    @property
    def timestamps(self) -> list[float]:
        """Generate list of timestamps to extract."""
        capped_end = min(self.end_seconds, self.start_seconds + self.MAX_WINDOW_SECONDS)
        result = []
        t = self.start_seconds
        interval = 1.0 / self.fps
        while t <= capped_end:
            result.append(round(t, 2))
            t += interval
        return result


@dataclass
class AgentIteration:
    """Record of one iteration of the agent loop."""
    iteration_number: int
    frames_analyzed: int
    timestamps: list[float]
    response_summary: str
    frame_requests: list[FrameRequest]
    feedback_items: list[TimestampedFeedback]


@dataclass
class AgenticAnalysisResult:
    """Complete result of multi-pass agentic analysis."""
    session_id: UUID
    stroke_type: StrokeType
    video_duration_seconds: float
    iterations: list[AgentIteration] = field(default_factory=list)
    final_summary: str = ""
    timestamped_feedback: list[TimestampedFeedback] = field(default_factory=list)
    total_frames_analyzed: int = 0
    
    def to_standard_result(self) -> AnalysisResult:
        """Convert to standard AnalysisResult for API compatibility."""
        feedback_items = []
        for tf in self.timestamped_feedback:
            time_range = None
            if tf.start_seconds is not None:
                time_range = TimeRange(
                    start=Timestamp(tf.start_seconds),
                    end=Timestamp(tf.end_seconds or tf.start_seconds)
                )
            
            feedback_items.append(CoachingFeedback(
                priority=tf.priority,
                observation=TechniqueObservation(
                    category=tf.category,
                    description=f"[{tf.timestamp_display}] {tf.description}",
                    time_range=time_range,
                ),
                recommendation=tf.recommendation,
                drill_suggestions=tf.drill_suggestions,
            ))
        
        return AnalysisResult(
            stroke_type=self.stroke_type,
            summary=self.final_summary,
            observations=[],
            feedback=feedback_items,
            frame_count_analyzed=self.total_frames_analyzed,
        )


# ---------------------------------------------------------------------------
# Prompts for Agentic Analysis
# ---------------------------------------------------------------------------

AGENTIC_SYSTEM_PROMPT = """You are an experienced swim coach doing video analysis. You're in an interactive session where you can request additional frames if you need to see specific moments more closely.

## Your Workflow
1. First, review the frames provided to understand the overall technique
2. Identify areas that need closer inspection
3. Request additional frames at specific timestamps if needed
4. Provide timestamp-linked feedback

## Response Format
Structure your response as JSON with these fields:

```json
{
  "summary": "Brief overview of what you observed",
  "need_more_frames": true/false,
  "frame_requests": [
    {
      "start_seconds": 2.5,
      "end_seconds": 3.5,
      "reason": "Need to see catch phase more closely",
      "fps": 3.0
    }
  ],
  "feedback": [
    {
      "timestamp_start": 2.5,
      "timestamp_end": 3.0,
      "category": "catch_and_pull",
      "priority": "primary",
      "observation": "Your elbow drops below your wrist during the catch",
      "recommendation": "Focus on early vertical forearm - imagine reaching over a barrel",
      "drills": ["fingertip drag", "catch-up drill"]
    }
  ],
  "strengths": [
    {
      "timestamp_start": 0.0,
      "observation": "Good horizontal body position throughout"
    }
  ]
}
```

## Guidelines
- Reference specific timestamps: "At 0:12, your elbow..." not "your elbow sometimes..."
- Only request more frames if genuinely needed (limit requests to 2-3 per iteration)
- Keep frame request windows NARROW (under 2 seconds). Target a single stroke cycle or moment, not a broad time range. For example, request 3.0-4.0s to see one catch, not 2.0-8.0s to scan the whole pull
- Each frame request should target a specific technique element
- Prioritize feedback: one PRIMARY issue, then SECONDARY
- Be specific about what you see vs. what to do

## Categories
Use these categories: body_position, catch_and_pull, recovery, kick, timing, breathing, turns, starts"""


FOLLOWUP_PROMPT = """I've provided the additional frames you requested. Here's what we have now:

{frame_context}

Please analyze these new frames and update your feedback. 
- If you have enough information, set need_more_frames to false
- If you still need clarification, you can request one more set of frames
- Integrate your new observations with your previous feedback
- Reference specific timestamps in your feedback

Respond in the same JSON format."""


# ---------------------------------------------------------------------------
# Agentic Coach
# ---------------------------------------------------------------------------

class AgenticSwimCoach:
    """Multi-pass coach: sparse scan, then targeted frame requests."""
    
    def __init__(
        self,
        vision_client: VisionModelClient,
        frame_extractor: VideoFrameExtractor,
        max_iterations: int = 3,
        initial_fps: float = 0.5,  # one frame every 2 seconds for initial pass
        max_frames_per_request: int = 5,
    ) -> None:
        self._vision_client = vision_client
        self._frame_extractor = frame_extractor
        self._max_iterations = max_iterations
        self._initial_fps = initial_fps
        self._max_frames_per_request = max_frames_per_request
    
    async def analyze_video(
        self,
        video_data: bytes,
        video_duration: float,
        stroke_type: StrokeType = StrokeType.FREESTYLE,
        user_notes: str = "",
        knowledge_context: list[str] | None = None,
    ) -> AgenticAnalysisResult:
        """Multi-pass analysis: sparse overview → targeted detail → timestamped feedback."""
        session_id = uuid4()
        result = AgenticAnalysisResult(
            session_id=session_id,
            stroke_type=stroke_type,
            video_duration_seconds=video_duration,
        )
        
        logger.info(
            "Starting agentic analysis",
            extra={
                "session_id": str(session_id),
                "video_duration": video_duration,
                "initial_fps": self._initial_fps,
            }
        )
        
        initial_frames = await self._frame_extractor.extract_frames_at_fps(
            video_data=video_data,
            fps=self._initial_fps,
            max_frames=20,
        )
        
        current_frames = initial_frames
        all_analyzed_timestamps = set(ts for ts, _ in initial_frames)
        
        for iteration in range(self._max_iterations):
            logger.info(
                f"Agentic iteration {iteration + 1}",
                extra={
                    "session_id": str(session_id),
                    "frame_count": len(current_frames),
                }
            )
            
            frame_context = self._build_frame_context(
                [(ts, i) for i, (ts, _) in enumerate(current_frames)],
                video_duration,
            )
            
            user_prompt = self._build_user_prompt(
                iteration=iteration,
                frame_context=frame_context,
                stroke_type=stroke_type,
                user_notes=user_notes,
                video_duration=video_duration,
            )
            
            system_prompt = AGENTIC_SYSTEM_PROMPT
            if knowledge_context:
                rag_content = "\n".join(f"- {chunk}" for chunk in knowledge_context)
                system_prompt = f"""## Expert Swimming Knowledge
Use this reference material to inform your coaching:

{rag_content}

---

{system_prompt}"""
            
            frame_bytes = [data for _, data in current_frames]
            
            response = await self._vision_client.analyze_images(
                images=frame_bytes,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            
            parsed = self._parse_response(response)

            iter_record = AgentIteration(
                iteration_number=iteration + 1,
                frames_analyzed=len(current_frames),
                timestamps=[ts for ts, _ in current_frames],
                response_summary=parsed.get("summary", ""),
                frame_requests=[
                    FrameRequest(
                        start_seconds=req.get("start_seconds", 0),
                        end_seconds=req.get("end_seconds", 0),
                        reason=req.get("reason", ""),
                        fps=req.get("fps", 2.0),
                    )
                    for req in parsed.get("frame_requests", [])
                ],
                feedback_items=self._parse_feedback(parsed),
            )
            result.iterations.append(iter_record)
            result.total_frames_analyzed += len(current_frames)
            
            if not parsed.get("need_more_frames", False):
                logger.info(
                    "Agent satisfied, completing analysis",
                    extra={"session_id": str(session_id), "iterations": iteration + 1}
                )
                break
            
            frame_requests = parsed.get("frame_requests", [])
            if not frame_requests:
                break
            
            new_frames = []
            for req in frame_requests[:3]:  # max 3 requests per iteration
                timestamps = FrameRequest(
                    start_seconds=req.get("start_seconds", 0),
                    end_seconds=req.get("end_seconds", 0),
                    fps=min(req.get("fps", 2.0), 5.0),  # cap at 5 fps
                    reason=req.get("reason", ""),
                ).timestamps

                new_timestamps = [
                    ts for ts in timestamps 
                    if ts not in all_analyzed_timestamps
                ][:self._max_frames_per_request]
                
                if new_timestamps:
                    extracted = await self._frame_extractor.extract_frames_at_timestamps(
                        video_data=video_data,
                        timestamps=new_timestamps,
                    )
                    new_frames.extend(extracted)
                    all_analyzed_timestamps.update(ts for ts, _ in extracted)
            
            if not new_frames:
                logger.info(
                    "No new frames to extract, completing",
                    extra={"session_id": str(session_id)}
                )
                break
            
            current_frames = new_frames
            
            logger.info(
                "Extracted additional frames",
                extra={
                    "session_id": str(session_id),
                    "new_frame_count": len(new_frames),
                }
            )
        
        result.timestamped_feedback = self._compile_feedback(result.iterations)
        result.final_summary = self._compile_summary(result.iterations)
        
        logger.info(
            "Agentic analysis complete",
            extra={
                "session_id": str(session_id),
                "total_iterations": len(result.iterations),
                "total_frames": result.total_frames_analyzed,
                "feedback_items": len(result.timestamped_feedback),
            }
        )
        
        return result
    
    def _build_frame_context(
        self,
        frame_info: list[tuple[float, int]],
        video_duration: float,
    ) -> str:
        lines = [f"Video duration: {video_duration:.1f} seconds"]
        lines.append(f"Analyzing {len(frame_info)} frames at these timestamps:")
        
        for ts, idx in frame_info:
            lines.append(f"  Frame {idx + 1}: {ts:.2f}s")
        
        return "\n".join(lines)
    
    def _build_user_prompt(
        self,
        iteration: int,
        frame_context: str,
        stroke_type: StrokeType,
        user_notes: str,
        video_duration: float,
    ) -> str:
        if iteration == 0:
            prompt = f"""I'm uploading frames from a swimming video for analysis.

{frame_context}

Swimmer context:
- Stroke: {stroke_type.value}
- Notes: {user_notes or "None provided"}

Please analyze my technique. If you need to see specific moments more closely, request additional frames at those timestamps."""
        else:
            prompt = FOLLOWUP_PROMPT.format(frame_context=frame_context)
        
        return prompt
    
    def _parse_response(self, response: str) -> dict:
        """Parse JSON from model response (handles markdown code blocks)."""
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                return {
                    "summary": response,
                    "need_more_frames": False,
                    "feedback": [],
                }
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON response, using fallback")
            return {
                "summary": response,
                "need_more_frames": False,
                "feedback": [],
            }
    
    def _parse_feedback(self, parsed: dict) -> list[TimestampedFeedback]:
        items = []
        
        for fb in parsed.get("feedback", []):
            try:
                category = TechniqueCategory(fb.get("category", "body_position"))
            except ValueError:
                category = TechniqueCategory.BODY_POSITION
            
            try:
                priority = FeedbackPriority(fb.get("priority", "secondary"))
            except ValueError:
                priority = FeedbackPriority.SECONDARY
            
            items.append(TimestampedFeedback(
                category=category,
                description=fb.get("observation", ""),
                recommendation=fb.get("recommendation", ""),
                start_seconds=fb.get("timestamp_start", 0.0),
                end_seconds=fb.get("timestamp_end"),
                priority=priority,
                drill_suggestions=fb.get("drills", []),
            ))
        
        return items
    
    def _compile_feedback(
        self,
        iterations: list[AgentIteration],
    ) -> list[TimestampedFeedback]:
        """Deduplicate feedback across iterations, prefer latest."""
        if not iterations:
            return []
        
        feedback = list(iterations[-1].feedback_items)
        seen_descriptions = {f.description for f in feedback}
        
        for iter_record in iterations[:-1]:
            for fb in iter_record.feedback_items:
                if fb.description not in seen_descriptions:
                    feedback.append(fb)
                    seen_descriptions.add(fb.description)
        
        priority_order = {
            FeedbackPriority.PRIMARY: 0,
            FeedbackPriority.SECONDARY: 1,
            FeedbackPriority.REFINEMENT: 2,
        }
        feedback.sort(key=lambda f: (priority_order[f.priority], f.start_seconds))
        
        return feedback
    
    def _compile_summary(self, iterations: list[AgentIteration]) -> str:
        if not iterations:
            return "No analysis completed."
        
        summaries = [iterations[-1].response_summary]
        total_frames = sum(it.frames_analyzed for it in iterations)
        summaries.append(
            f"\n\nAnalyzed {total_frames} frames across {len(iterations)} passes "
            f"for a thorough technique review."
        )
        
        return " ".join(summaries)
