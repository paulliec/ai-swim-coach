"""
Swim coaching logic and prompt management.

Prompts live here (not config) because they're core business logic.
Framework-agnostic — no HTTP or database dependencies.
"""

from dataclasses import dataclass
from typing import Protocol

from .models import (
    AnalysisResult,
    ChatMessage,
    CoachingFeedback,
    CoachingSession,
    FeedbackPriority,
    StrokeType,
    TechniqueCategory,
    TechniqueObservation,
)


# ---------------------------------------------------------------------------
# Protocols (interfaces)
# ---------------------------------------------------------------------------

class VisionModelClient(Protocol):
    """Interface for vision-capable LLM clients."""
    
    async def analyze_images(
        self,
        images: list[bytes],
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Analyze images and return text response."""
        ...
    
    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
    ) -> str:
        """Continue a conversation."""
        ...


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an experienced swim coach with deep knowledge of competitive and masters swimming technique. You analyze video footage to provide actionable feedback.

## Your Approach
- Observe before prescribing. Describe what you see, then explain why it matters, then suggest a fix.
- Prioritize one or two key improvements per session. Swimmers can't fix everything at once.
- Use concrete cues ("lead with your elbow," "press your chest") rather than abstract instructions.
- Reference common drills when appropriate (catch-up drill, fingertip drag, 6-kick switch).
- Acknowledge what's working well, not just problems.

## Stroke Analysis Framework
When analyzing footage, consider:
- Body position: horizontal alignment, head position, hip height
- Catch and pull: early vertical forearm, hand entry, pull path
- Recovery: elbow height, relaxation, hand entry point
- Kick: amplitude, knee bend, ankle flexibility
- Timing: stroke coordination, breathing integration, rotation sync
- Breathing: head rotation (not lift), timing relative to stroke

## Interaction Style
- Be encouraging but honest. Swimmers respect directness.
- Ask clarifying questions if video angle limits what you can assess.
- If asked about training, periodization, or race strategy, you can help — but technique is your primary focus.

## Response Format for Initial Analysis
Structure your analysis as:
1. SUMMARY: One paragraph overview of what you observed
2. STRENGTHS: What's working well (be specific)
3. PRIMARY FOCUS: The one thing that will help most right now
4. SECONDARY NOTES: Other observations to address later
5. DRILLS: Specific drills to practice, with brief descriptions"""


# RAG context template - inserted when knowledge chunks are available
RAG_CONTEXT_TEMPLATE = """
## Reference Knowledge
The following expert swimming technique knowledge may be relevant to your analysis. 
Use it to inform your feedback, but base your observations on what you actually see in the video.

{knowledge_chunks}

---
"""


ANALYSIS_USER_PROMPT_TEMPLATE = """I'm uploading {frame_count} frames from a swimming video.

Context provided by the swimmer:
- Stroke: {stroke_type}
- Additional notes: {user_notes}

Please analyze my technique and provide coaching feedback. Focus on what will help me improve most."""


FOLLOWUP_CONTEXT_TEMPLATE = """You previously analyzed this swimmer's video and provided coaching feedback. Here is your complete analysis:

{initial_analysis}

The swimmer is now asking follow-up questions. You have full context from your original video analysis above — refer to specific observations, timestamps, and recommendations when answering. Continue coaching them based on what you observed."""


# ---------------------------------------------------------------------------
# Coach Service
# ---------------------------------------------------------------------------

@dataclass
class FrameSet:
    """A collection of video frames for analysis."""
    frames: list[bytes]
    timestamps_seconds: list[float]
    
    def __post_init__(self) -> None:
        if len(self.frames) != len(self.timestamps_seconds):
            raise ValueError("Must have same number of frames and timestamps")
        if not self.frames:
            raise ValueError("Must provide at least one frame")


class SwimCoach:
    """Stateless coaching service — session state lives in CoachingSession."""
    
    def __init__(self, vision_client: VisionModelClient) -> None:
        self._vision_client = vision_client
    
    async def analyze_video(
        self,
        frames: FrameSet,
        stroke_type: StrokeType = StrokeType.FREESTYLE,
        user_notes: str = "",
        knowledge_context: list[str] | None = None,
    ) -> AnalysisResult:
        """Analyze video frames and return structured coaching feedback."""
        user_prompt = ANALYSIS_USER_PROMPT_TEMPLATE.format(
            frame_count=len(frames.frames),
            stroke_type=stroke_type.value,
            user_notes=user_notes or "None provided",
        )
        
        system_prompt = SYSTEM_PROMPT
        if knowledge_context:
            rag_content = "\n\n".join(
                f"**{i+1}. {chunk}**" 
                for i, chunk in enumerate(knowledge_context)
            )
            system_prompt = RAG_CONTEXT_TEMPLATE.format(
                knowledge_chunks=rag_content
            ) + system_prompt
        
        raw_response = await self._vision_client.analyze_images(
            images=frames.frames,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        
        result = self._parse_analysis_response(raw_response, stroke_type)
        result.frame_count_analyzed = len(frames.frames)
        
        return result
    
    async def continue_conversation(
        self,
        session: CoachingSession,
        user_message: str,
    ) -> str:
        """Handle follow-up questions using session history as context."""
        if not session.is_analyzed:
            raise ValueError("Cannot continue conversation without initial analysis")
        
        messages = self._build_message_history(session, user_message)
        context_prompt = FOLLOWUP_CONTEXT_TEMPLATE.format(
            initial_analysis=session.analysis.summary if session.analysis else ""
        )
        
        response = await self._vision_client.chat(
            messages=messages,
            system_prompt=SYSTEM_PROMPT + "\n\n" + context_prompt,
        )
        
        return response
    
    def _parse_analysis_response(
        self,
        raw_response: str,
        stroke_type: StrokeType,
    ) -> AnalysisResult:
        """Parse model text into structured feedback."""
        # TODO: fix later - raw text parsing, structured JSON output would be better
        
        result = AnalysisResult(
            stroke_type=stroke_type,
            summary=raw_response,
            observations=[],
            feedback=[],
        )
        
        # extract primary focus if model followed our format
        if "PRIMARY FOCUS:" in raw_response:
            primary_section = self._extract_section(raw_response, "PRIMARY FOCUS:")
            if primary_section:
                result.feedback.append(
                    CoachingFeedback(
                        priority=FeedbackPriority.PRIMARY,
                        observation=TechniqueObservation(
                            category=TechniqueCategory.BODY_POSITION,  # Would be parsed
                            description=primary_section,
                        ),
                        recommendation=primary_section,
                    )
                )
        
        return result
    
    def _extract_section(self, text: str, header: str) -> str:
        """Extract content between a header and the next section."""
        lines = text.split("\n")
        capturing = False
        captured = []
        
        for line in lines:
            if header in line:
                capturing = True
                # rest of line after header
                after_header = line.split(header, 1)[1].strip()
                if after_header:
                    captured.append(after_header)
                continue
            
            if capturing:
                if line.strip() and (
                    line.strip()[0].isdigit() or 
                    line.strip().isupper()
                ):
                    break
                captured.append(line)
        
        return "\n".join(captured).strip()
    
    def _build_message_history(
        self,
        session: CoachingSession,
        new_message: str,
    ) -> list[dict[str, str]]:
        """Convert session conversation to model message format."""
        messages = []

        if session.analysis:
            messages.append({
                "role": "assistant",
                "content": session.analysis.summary,
            })
        
        for msg in session.conversation:
            messages.append({
                "role": msg.role,
                "content": msg.content,
            })
        
        messages.append({
            "role": "user",
            "content": new_message,
        })
        
        return messages
