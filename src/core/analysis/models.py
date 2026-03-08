"""
Domain models for swim technique analysis.

Pure domain objects — no framework, database, or API dependencies.
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
    """Categories for technique observations, mapped to coaching terminology."""
    BODY_POSITION = "body_position"
    CATCH_AND_PULL = "catch_and_pull"
    RECOVERY = "recovery"
    KICK = "kick"
    TIMING = "timing"
    BREATHING = "breathing"
    TURNS = "turns"
    STARTS = "starts"


class FeedbackPriority(Enum):
    """Prioritize what helps most right now."""
    PRIMARY = "primary"      # Fix this first
    SECONDARY = "secondary"  # Address after primary issues improve
    REFINEMENT = "refinement"  # Nice to have, for advanced swimmers


@dataclass(frozen=True)
class Timestamp:
    """A point in time within a video. Value object (frozen)."""
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
    """What the AI sees — a specific thing at a specific time."""
    category: TechniqueCategory
    description: str
    time_range: Optional[TimeRange] = None  # None if applies to whole video
    
    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Observation description cannot be empty")


@dataclass
class CoachingFeedback:
    """Actionable advice: observation (what we see) + recommendation (what to do)."""
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
    """File-level info about an uploaded video (not swimming analysis)."""
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
    """Everything we learned from analyzing the footage."""
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
    """Aggregate root: video + analysis + conversation."""
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
