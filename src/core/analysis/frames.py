"""
Video frame extraction strategies.

This module handles extracting frames from video files for analysis.
Different strategies work better for different scenarios:
- Uniform sampling for general technique review
- Scene-based extraction for turn analysis
- High-frequency extraction for specific moments

The module is designed to be testable (pure functions where possible)
and framework-agnostic (uses subprocess for ffmpeg, not a framework binding).
"""

import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class ExtractedFrame:
    """
    A single frame extracted from video.
    
    Frozen because frames are values — two frames with the same
    data and timestamp are equivalent.
    """
    image_data: bytes
    timestamp_seconds: float
    frame_number: int
    
    @property
    def timestamp_formatted(self) -> str:
        """Human-readable timestamp."""
        minutes = int(self.timestamp_seconds // 60)
        seconds = self.timestamp_seconds % 60
        return f"{minutes:02d}:{seconds:05.2f}"


@dataclass(frozen=True)
class VideoInfo:
    """Technical information about a video file."""
    duration_seconds: float
    fps: float
    width: int
    height: int
    codec: str
    
    @property
    def total_frames(self) -> int:
        return int(self.duration_seconds * self.fps)


class FrameExtractionError(Exception):
    """Raised when frame extraction fails."""
    pass


# ---------------------------------------------------------------------------
# Extraction Strategies
# ---------------------------------------------------------------------------

class ExtractionStrategy(ABC):
    """
    Base class for frame extraction strategies.
    
    Using a strategy pattern here means we can:
    - Add new strategies without changing existing code
    - Test strategies in isolation
    - Let users choose appropriate strategies for their videos
    """
    
    @abstractmethod
    def calculate_timestamps(
        self,
        video_info: VideoInfo,
        max_frames: int = 20,
    ) -> list[float]:
        """
        Determine which timestamps to extract frames from.
        
        Returns a list of timestamps in seconds.
        """
        ...


class UniformSamplingStrategy(ExtractionStrategy):
    """
    Extract frames at uniform intervals.
    
    Best for: General technique analysis of continuous swimming.
    Ensures coverage across the entire video duration.
    """
    
    def calculate_timestamps(
        self,
        video_info: VideoInfo,
        max_frames: int = 20,
    ) -> list[float]:
        duration = video_info.duration_seconds
        
        # Don't extract more frames than we have seconds
        frame_count = min(max_frames, int(duration))
        
        if frame_count <= 1:
            return [duration / 2]  # Just grab the middle
        
        # Calculate interval, leaving small buffer at start/end
        buffer = 0.5  # Skip first/last 0.5 seconds
        usable_duration = max(0, duration - (2 * buffer))
        
        if usable_duration <= 0:
            return [duration / 2]
        
        interval = usable_duration / (frame_count - 1)
        
        return [
            buffer + (i * interval)
            for i in range(frame_count)
        ]


class StrokeCycleStrategy(ExtractionStrategy):
    """
    Attempt to capture complete stroke cycles.
    
    Best for: Detailed stroke analysis where you want to see
    the full motion (catch, pull, recovery, entry).
    
    Assumes roughly 1.5 seconds per stroke cycle for freestyle.
    """
    
    def __init__(self, estimated_stroke_rate: float = 1.5):
        self.stroke_period = estimated_stroke_rate
    
    def calculate_timestamps(
        self,
        video_info: VideoInfo,
        max_frames: int = 20,
    ) -> list[float]:
        duration = video_info.duration_seconds
        
        # Calculate how many stroke cycles fit in the video
        cycle_count = duration / self.stroke_period
        
        # We want multiple frames per cycle for good coverage
        frames_per_cycle = 6  # catch, pull-start, pull-end, exit, recovery, entry
        
        # How many complete cycles can we capture?
        cycles_to_capture = min(
            cycle_count,
            max_frames / frames_per_cycle
        )
        
        if cycles_to_capture < 1:
            # Video is shorter than one stroke cycle, fall back to uniform
            return UniformSamplingStrategy().calculate_timestamps(
                video_info, max_frames
            )
        
        timestamps = []
        for cycle in range(int(cycles_to_capture)):
            cycle_start = cycle * self.stroke_period + 0.5  # Small offset
            for frame in range(frames_per_cycle):
                ts = cycle_start + (frame * self.stroke_period / frames_per_cycle)
                if ts < duration - 0.5:  # Stay away from end
                    timestamps.append(ts)
        
        return timestamps[:max_frames]


class KeyMomentStrategy(ExtractionStrategy):
    """
    Focus on specific moments (turns, starts, finishes).
    
    Best for: Wall work analysis, race start review.
    Extracts more densely around specified target times.
    """
    
    def __init__(self, target_times: list[float], window_seconds: float = 2.0):
        self.target_times = target_times
        self.window = window_seconds
    
    def calculate_timestamps(
        self,
        video_info: VideoInfo,
        max_frames: int = 20,
    ) -> list[float]:
        duration = video_info.duration_seconds
        frames_per_target = max_frames // max(len(self.target_times), 1)
        
        timestamps = []
        for target in self.target_times:
            # Extract frames in a window around the target
            window_start = max(0, target - self.window / 2)
            window_end = min(duration, target + self.window / 2)
            
            interval = (window_end - window_start) / max(frames_per_target - 1, 1)
            
            for i in range(frames_per_target):
                ts = window_start + (i * interval)
                timestamps.append(ts)
        
        return sorted(set(timestamps))[:max_frames]


# ---------------------------------------------------------------------------
# Frame Extractor
# ---------------------------------------------------------------------------

class FrameExtractor:
    """
    Extracts frames from video files using ffmpeg.
    
    This class wraps the ffmpeg subprocess calls and handles
    the messy details of temporary files and error handling.
    """
    
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
    
    def get_video_info(self, video_path: Path) -> VideoInfo:
        """
        Extract technical information about a video file.
        
        Uses ffprobe to get duration, resolution, fps, etc.
        """
        if not video_path.exists():
            raise FrameExtractionError(f"Video file not found: {video_path}")
        
        cmd = [
            self.ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,codec_name,duration",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(video_path),
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise FrameExtractionError("ffprobe timed out")
        except subprocess.CalledProcessError as e:
            raise FrameExtractionError(f"ffprobe failed: {e.stderr}")
        
        return self._parse_ffprobe_output(result.stdout)
    
    def extract_frames(
        self,
        video_path: Path,
        strategy: ExtractionStrategy,
        max_frames: int = 20,
        output_format: str = "jpg",
        quality: int = 2,  # 2-31, lower is better
    ) -> Iterator[ExtractedFrame]:
        """
        Extract frames from video using the specified strategy.
        
        Yields ExtractedFrame objects as they're extracted.
        Using a generator here means we don't load all frames
        into memory at once.
        """
        video_info = self.get_video_info(video_path)
        timestamps = strategy.calculate_timestamps(video_info, max_frames)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, timestamp in enumerate(timestamps):
                output_path = Path(tmpdir) / f"frame_{i:04d}.{output_format}"
                
                self._extract_single_frame(
                    video_path=video_path,
                    timestamp=timestamp,
                    output_path=output_path,
                    quality=quality,
                )
                
                if output_path.exists():
                    yield ExtractedFrame(
                        image_data=output_path.read_bytes(),
                        timestamp_seconds=timestamp,
                        frame_number=i,
                    )
    
    def _extract_single_frame(
        self,
        video_path: Path,
        timestamp: float,
        output_path: Path,
        quality: int,
    ) -> None:
        """Extract a single frame at the specified timestamp."""
        cmd = [
            self.ffmpeg,
            "-ss", str(timestamp),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", str(quality),
            "-y",  # Overwrite without asking
            str(output_path),
        ]
        
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                timeout=10,
            )
        except subprocess.CalledProcessError as e:
            # Log but don't fail — we can continue with other frames
            pass
    
    def _parse_ffprobe_output(self, output: str) -> VideoInfo:
        """Parse ffprobe CSV output into VideoInfo."""
        lines = output.strip().split("\n")
        
        # ffprobe output format: width,height,fps,codec,duration (stream)
        # followed by: duration (format) on next line
        
        if not lines:
            raise FrameExtractionError("Empty ffprobe output")
        
        stream_parts = lines[0].split(",")
        
        try:
            width = int(stream_parts[0])
            height = int(stream_parts[1])
            
            # FPS comes as a fraction like "30/1"
            fps_parts = stream_parts[2].split("/")
            fps = float(fps_parts[0]) / float(fps_parts[1])
            
            codec = stream_parts[3]
            
            # Duration might be in stream or format line
            duration = 0.0
            if len(stream_parts) > 4 and stream_parts[4]:
                duration = float(stream_parts[4])
            elif len(lines) > 1 and lines[1]:
                duration = float(lines[1])
            
        except (ValueError, IndexError) as e:
            raise FrameExtractionError(f"Failed to parse ffprobe output: {e}")
        
        return VideoInfo(
            duration_seconds=duration,
            fps=fps,
            width=width,
            height=height,
            codec=codec,
        )


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def extract_frames_uniform(
    video_path: Path,
    max_frames: int = 20,
    ffmpeg_path: str = "ffmpeg",
) -> list[ExtractedFrame]:
    """
    Convenience function for simple uniform frame extraction.
    
    For cases where you just want frames without configuring strategies.
    """
    extractor = FrameExtractor(ffmpeg_path=ffmpeg_path)
    strategy = UniformSamplingStrategy()
    
    return list(extractor.extract_frames(video_path, strategy, max_frames))
