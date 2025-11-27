"""
Swim technique analysis logic.

Contains the coaching service, domain models, and frame extraction strategies.
"""

from .models import (
    AnalysisResult,
    ChatMessage,
    CoachingFeedback,
    CoachingSession,
    FeedbackPriority,
    StrokeType,
    TechniqueCategory,
    TechniqueObservation,
    VideoMetadata,
)
from .coach import SwimCoach, FrameSet

__all__ = [
    "AnalysisResult",
    "ChatMessage",
    "CoachingFeedback",
    "CoachingSession",
    "FeedbackPriority",
    "StrokeType",
    "TechniqueCategory",
    "TechniqueObservation",
    "VideoMetadata",
    "SwimCoach",
    "FrameSet",
]

