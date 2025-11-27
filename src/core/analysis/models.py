"""
Domain models for swim technique analysis.

These models represent the core business concepts. They have no dependencies
on external frameworks, databases, or APIs. This is intentional — the domain
should be expressible without knowing how it's stored or transmitted.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


class StrokeType(Enum):
    """The four competitive strokes plus general/mixed."""
    FREESTYLE = "freestyle"
    BACKSTROKE = "backstroke"
    BREASTSTROKE = "breaststroke"
    BUTTERFLY = "butterfly"
    MIXED = "mixed"  # For videos with multiple strokes or unclear


class TechniqueCategory(Enum):
    """
    High-level categories for technique observations.
    
    These map to how coaches think about stroke mechanics,
    not to how the AI happens to structure its output.
    """
    BODY_POSITION = "body_position"
    CATCH_AND_PULL = "catch_and_pull"
    RECOVERY = "recovery"
    KICK = "kick"
    TIMING = "timing"
    BREATHING = "breathing"
    TURNS = "turns"
    STARTS = "starts"


class FeedbackPriority(Enum):
    """
    How important is this feedback?
    
    A good coach doesn't dump everything at once. They prioritize
    what will have the most impact for this swimmer right now.
    """
    PRIMARY = "primary"      # Fix this first
    SECONDARY = "secondary"  # Address after primary issues improve
    REFINEMENT = "refinement"  # Nice to have, for advanced swimmers


@dataclass(frozen=True)
class Timestamp:
    """
    A point in time within a video.
    
    Frozen because timestamps are values, not entities.
    Two timestamps at the same position are the same timestamp.
    """
    seconds: float
    
    def __post_init__(self) -> None:
        if self.seconds < 0:
            raise ValueError("Timestamp cannot be negative")
    
    @property
    def formatted(self) -> str:
        """Human-readable format: MM:SS.ms"""
        minutes = int(self.seconds // 60)
        secs = self.seconds % 60
        return f"{minutes:02d}:{secs:05.2f}"


@dataclass(frozen=True)
class TimeRange:
    """A span of time within a video."""
    start: Timestamp
    end: Timestamp
    
    def __post_init__(self) -> None:
        if self.end.seconds < self.start.seconds:
            raise ValueError("End timestamp must be after start timestamp")
    
    @property
    def duration_seconds(self) -> float:
        return self.end.seconds - self.start.seconds


@dataclass
class TechniqueObservation:
    """
    A single observation about the swimmer's technique.
    
    This is what the AI "sees" — a specific thing at a specific time.
    """
    category: TechniqueCategory
    description: str
    time_range: Optional[TimeRange] = None  # None if applies to whole video
    
    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Observation description cannot be empty")


@dataclass
class CoachingFeedback:
    """
    Actionable coaching advice derived from observations.
    
    The distinction between Observation and Feedback matters:
    - Observation: "Your elbow drops below your wrist at the catch"
    - Feedback: "Focus on leading with a high elbow. Try the fingertip drag drill."
    
    Observations are what we see. Feedback is what to do about it.
    """
    id: UUID = field(default_factory=uuid4)
    priority: FeedbackPriority = FeedbackPriority.SECONDARY
    observation: TechniqueObservation = field(default_factory=lambda: TechniqueObservation(
        category=TechniqueCategory.BODY_POSITION,
        description="placeholder"
    ))
    recommendation: str = ""
    drill_suggestions: list[str] = field(default_factory=list)
    
    def __post_init__(self) -> None:
        if not self.recommendation.strip():
            raise ValueError("Feedback must include a recommendation")


@dataclass
class VideoMetadata:
    """
    Information about an uploaded video.
    
    Separate from the video content itself — this is what we know
    about the file without analyzing the swimming.
    """
    id: UUID = field(default_factory=uuid4)
    filename: str = ""
    duration_seconds: float = 0.0
    resolution: tuple[int, int] = (0, 0)  # width, height
    fps: float = 0.0
    file_size_bytes: int = 0
    uploaded_at: datetime = field(default_factory=datetime.utcnow)
    storage_path: str = ""  # Where it lives in object storage
    
    @property
    def resolution_display(self) -> str:
        return f"{self.resolution[0]}x{self.resolution[1]}"


@dataclass
class AnalysisResult:
    """
    The complete result of analyzing a video.
    
    This is the output of the analysis service — everything we
    learned from looking at the footage.
    """
    id: UUID = field(default_factory=uuid4)
    video_id: UUID = field(default_factory=uuid4)
    stroke_type: StrokeType = StrokeType.FREESTYLE
    observations: list[TechniqueObservation] = field(default_factory=list)
    feedback: list[CoachingFeedback] = field(default_factory=list)
    summary: str = ""
    analyzed_at: datetime = field(default_factory=datetime.utcnow)
    frame_count_analyzed: int = 0
    
    @property
    def primary_feedback(self) -> list[CoachingFeedback]:
        """The most important things to work on."""
        return [f for f in self.feedback if f.priority == FeedbackPriority.PRIMARY]


@dataclass
class ChatMessage:
    """A single message in the coaching conversation."""
    id: UUID = field(default_factory=uuid4)
    role: str = "user"  # "user" or "assistant"
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CoachingSession:
    """
    A complete coaching interaction.
    
    This is the aggregate root for a coaching session — it owns
    the video, the analysis, and the conversation that follows.
    """
    id: UUID = field(default_factory=uuid4)
    video: Optional[VideoMetadata] = None
    analysis: Optional[AnalysisResult] = None
    conversation: list[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def add_message(self, role: str, content: str) -> ChatMessage:
        """Add a message to the conversation and update timestamp."""
        message = ChatMessage(role=role, content=content)
        self.conversation.append(message)
        self.updated_at = datetime.utcnow()
        return message
    
    @property
    def is_analyzed(self) -> bool:
        return self.analysis is not None
    
    @property
    def has_video(self) -> bool:
        return self.video is not None
