"""
Unit tests for the coaching domain logic.

These tests verify the core business logic without touching
external services (no API calls, no database, no file system).

Testing philosophy:
- Test behavior, not implementation
- Each test should have a clear "given/when/then" structure
- Use descriptive names that explain what we're testing
- Prefer real objects over mocks where practical
"""

from datetime import datetime
from uuid import uuid4

import pytest

from src.core.analysis.models import (
    AnalysisResult,
    ChatMessage,
    CoachingFeedback,
    CoachingSession,
    FeedbackPriority,
    StrokeType,
    TechniqueCategory,
    TechniqueObservation,
    TimeRange,
    Timestamp,
    VideoMetadata,
)
from src.core.analysis.frames import (
    UniformSamplingStrategy,
    StrokeCycleStrategy,
    KeyMomentStrategy,
    VideoInfo,
)


# ---------------------------------------------------------------------------
# Timestamp and TimeRange Tests
# ---------------------------------------------------------------------------

class TestTimestamp:
    """Tests for the Timestamp value object."""
    
    def test_timestamp_formats_correctly(self):
        """Timestamps should format as MM:SS.ms"""
        ts = Timestamp(seconds=65.5)
        assert ts.formatted == "01:05.50"
    
    def test_timestamp_rejects_negative(self):
        """Negative timestamps don't make sense for video."""
        with pytest.raises(ValueError, match="cannot be negative"):
            Timestamp(seconds=-1)
    
    def test_timestamp_zero_is_valid(self):
        """Zero is a valid timestamp (start of video)."""
        ts = Timestamp(seconds=0)
        assert ts.formatted == "00:00.00"


class TestTimeRange:
    """Tests for the TimeRange value object."""
    
    def test_time_range_calculates_duration(self):
        """Duration should be the difference between end and start."""
        range_ = TimeRange(
            start=Timestamp(10.0),
            end=Timestamp(15.5)
        )
        assert range_.duration_seconds == 5.5
    
    def test_time_range_rejects_inverted_range(self):
        """End must be after start."""
        with pytest.raises(ValueError, match="after start"):
            TimeRange(
                start=Timestamp(10.0),
                end=Timestamp(5.0)
            )


# ---------------------------------------------------------------------------
# Coaching Session Tests
# ---------------------------------------------------------------------------

class TestCoachingSession:
    """Tests for the CoachingSession aggregate."""
    
    def test_new_session_starts_empty(self):
        """A new session has no video, analysis, or conversation."""
        session = CoachingSession()
        
        assert not session.has_video
        assert not session.is_analyzed
        assert session.conversation == []
    
    def test_add_message_appends_to_conversation(self):
        """Adding a message should grow the conversation."""
        session = CoachingSession()
        
        session.add_message("user", "How's my catch?")
        session.add_message("assistant", "Your catch looks good!")
        
        assert len(session.conversation) == 2
        assert session.conversation[0].role == "user"
        assert session.conversation[1].role == "assistant"
    
    def test_add_message_updates_timestamp(self):
        """Adding a message should update the session's updated_at."""
        session = CoachingSession()
        original_updated = session.updated_at
        
        # Small delay to ensure timestamp changes
        import time
        time.sleep(0.01)
        
        session.add_message("user", "Question")
        
        assert session.updated_at > original_updated
    
    def test_is_analyzed_reflects_analysis_presence(self):
        """is_analyzed should be True only when analysis exists."""
        session = CoachingSession()
        assert not session.is_analyzed
        
        session.analysis = AnalysisResult(
            stroke_type=StrokeType.FREESTYLE,
            summary="Good form overall."
        )
        assert session.is_analyzed


class TestAnalysisResult:
    """Tests for analysis result behavior."""
    
    def test_primary_feedback_filters_correctly(self):
        """primary_feedback should return only PRIMARY priority items."""
        analysis = AnalysisResult(
            stroke_type=StrokeType.FREESTYLE,
            summary="Analysis",
            feedback=[
                CoachingFeedback(
                    priority=FeedbackPriority.PRIMARY,
                    observation=TechniqueObservation(
                        category=TechniqueCategory.CATCH_AND_PULL,
                        description="Elbow dropping"
                    ),
                    recommendation="Keep high elbow"
                ),
                CoachingFeedback(
                    priority=FeedbackPriority.SECONDARY,
                    observation=TechniqueObservation(
                        category=TechniqueCategory.KICK,
                        description="Slight scissor kick"
                    ),
                    recommendation="Focus on narrow kick"
                ),
                CoachingFeedback(
                    priority=FeedbackPriority.PRIMARY,
                    observation=TechniqueObservation(
                        category=TechniqueCategory.BREATHING,
                        description="Lifting head too much"
                    ),
                    recommendation="Rotate to breathe"
                ),
            ]
        )
        
        primary = analysis.primary_feedback
        
        assert len(primary) == 2
        assert all(f.priority == FeedbackPriority.PRIMARY for f in primary)


# ---------------------------------------------------------------------------
# Frame Extraction Strategy Tests
# ---------------------------------------------------------------------------

class TestUniformSamplingStrategy:
    """Tests for uniform frame sampling."""
    
    @pytest.fixture
    def sample_video_info(self) -> VideoInfo:
        """Standard 60-second video for testing."""
        return VideoInfo(
            duration_seconds=60.0,
            fps=30.0,
            width=1920,
            height=1080,
            codec="h264"
        )
    
    def test_extracts_requested_number_of_frames(self, sample_video_info):
        """Should return the requested number of timestamps."""
        strategy = UniformSamplingStrategy()
        
        timestamps = strategy.calculate_timestamps(sample_video_info, max_frames=10)
        
        assert len(timestamps) == 10
    
    def test_timestamps_are_evenly_spaced(self, sample_video_info):
        """Timestamps should be roughly equidistant."""
        strategy = UniformSamplingStrategy()
        
        timestamps = strategy.calculate_timestamps(sample_video_info, max_frames=10)
        
        # Calculate intervals between consecutive timestamps
        intervals = [
            timestamps[i+1] - timestamps[i]
            for i in range(len(timestamps) - 1)
        ]
        
        # All intervals should be approximately equal
        avg_interval = sum(intervals) / len(intervals)
        for interval in intervals:
            assert abs(interval - avg_interval) < 0.1
    
    def test_respects_video_duration(self, sample_video_info):
        """All timestamps should be within video bounds."""
        strategy = UniformSamplingStrategy()
        
        timestamps = strategy.calculate_timestamps(sample_video_info, max_frames=20)
        
        assert all(0 <= ts <= sample_video_info.duration_seconds for ts in timestamps)
    
    def test_handles_short_video(self):
        """Short videos should get fewer frames."""
        short_video = VideoInfo(
            duration_seconds=3.0,
            fps=30.0,
            width=1920,
            height=1080,
            codec="h264"
        )
        strategy = UniformSamplingStrategy()
        
        timestamps = strategy.calculate_timestamps(short_video, max_frames=20)
        
        # Shouldn't try to extract more frames than seconds
        assert len(timestamps) <= 3


class TestStrokeCycleStrategy:
    """Tests for stroke-cycle-aware sampling."""
    
    def test_captures_multiple_phases_per_cycle(self):
        """Should extract multiple frames within each stroke cycle."""
        video = VideoInfo(
            duration_seconds=10.0,  # ~6-7 stroke cycles
            fps=30.0,
            width=1920,
            height=1080,
            codec="h264"
        )
        strategy = StrokeCycleStrategy(estimated_stroke_rate=1.5)
        
        timestamps = strategy.calculate_timestamps(video, max_frames=20)
        
        # Should have multiple frames per 1.5-second cycle
        # With 10 seconds, we expect to see some clustering
        assert len(timestamps) >= 6  # At least one frame per cycle


class TestKeyMomentStrategy:
    """Tests for key-moment focused sampling."""
    
    def test_concentrates_frames_around_targets(self):
        """Frames should cluster around specified target times."""
        video = VideoInfo(
            duration_seconds=60.0,
            fps=30.0,
            width=1920,
            height=1080,
            codec="h264"
        )
        # Target the turns at 25s and 50s
        strategy = KeyMomentStrategy(
            target_times=[25.0, 50.0],
            window_seconds=3.0
        )
        
        timestamps = strategy.calculate_timestamps(video, max_frames=20)
        
        # Most frames should be within windows around targets
        in_window_count = sum(
            1 for ts in timestamps
            if (23.5 <= ts <= 26.5) or (48.5 <= ts <= 51.5)
        )
        
        assert in_window_count >= len(timestamps) * 0.8


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------

class TestModelValidation:
    """Tests for domain model validation rules."""
    
    def test_observation_requires_description(self):
        """Observations must have non-empty descriptions."""
        with pytest.raises(ValueError, match="cannot be empty"):
            TechniqueObservation(
                category=TechniqueCategory.BODY_POSITION,
                description=""
            )
    
    def test_feedback_requires_recommendation(self):
        """Feedback must include actionable advice."""
        with pytest.raises(ValueError, match="recommendation"):
            CoachingFeedback(
                priority=FeedbackPriority.PRIMARY,
                observation=TechniqueObservation(
                    category=TechniqueCategory.KICK,
                    description="Kick too wide"
                ),
                recommendation=""
            )


# ---------------------------------------------------------------------------
# Integration-Ready Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def complete_session() -> CoachingSession:
    """
    A fully populated session for integration tests.
    
    This fixture creates a realistic session with all components,
    useful for testing serialization, API responses, etc.
    """
    video = VideoMetadata(
        filename="freestyle_drill.mp4",
        storage_path="s3://swimcoach/videos/abc123.mp4",
        duration_seconds=45.0,
        resolution=(1920, 1080),
        fps=60.0,
        file_size_bytes=50_000_000,
    )
    
    analysis = AnalysisResult(
        video_id=video.id,
        stroke_type=StrokeType.FREESTYLE,
        summary="Overall good body position with room to improve catch.",
        observations=[
            TechniqueObservation(
                category=TechniqueCategory.BODY_POSITION,
                description="Head position is good, staying neutral."
            ),
            TechniqueObservation(
                category=TechniqueCategory.CATCH_AND_PULL,
                description="Elbow drops below wrist at catch initiation."
            ),
        ],
        feedback=[
            CoachingFeedback(
                priority=FeedbackPriority.PRIMARY,
                observation=TechniqueObservation(
                    category=TechniqueCategory.CATCH_AND_PULL,
                    description="Early vertical forearm not established."
                ),
                recommendation="Focus on keeping elbow high as hand enters.",
                drill_suggestions=["Fingertip drag", "Catch-up drill"],
            ),
        ],
        frame_count_analyzed=15,
    )
    
    session = CoachingSession(
        video=video,
        analysis=analysis,
    )
    
    session.add_message("user", "What should I focus on first?")
    session.add_message(
        "assistant",
        "Let's work on your catch. Try the fingertip drag drill."
    )
    
    return session
