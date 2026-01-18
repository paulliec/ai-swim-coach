"""
Video processing service using FFmpeg.

This module handles server-side video processing for the agentic analysis flow:
1. Extract video metadata (duration, fps, resolution)
2. Extract frames at specific timestamps (for AI to request "show me 0:12-0:15")
3. Extract frames at regular intervals (initial sparse pass)

Why server-side processing instead of client-side:
- Works on all browsers (no Safari headaches)
- More control over frame quality and timing
- Enables AI to request specific frames on demand
- Consistent results regardless of client device

Why FFmpeg:
- Industry standard, battle-tested
- Handles any video format
- Fast and efficient
- Available everywhere (including Docker)
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    """Video metadata extracted via FFprobe."""
    duration_seconds: float
    width: int
    height: int
    fps: float
    codec: str
    file_size_bytes: int


@dataclass
class ExtractedFrame:
    """A frame extracted from video with its timestamp."""
    timestamp_seconds: float
    frame_number: int
    data: bytes  # jpeg image data
    
    @property
    def timestamp_formatted(self) -> str:
        """Format timestamp as MM:SS.ms for display."""
        mins = int(self.timestamp_seconds // 60)
        secs = self.timestamp_seconds % 60
        return f"{mins}:{secs:05.2f}"


class VideoProcessor(Protocol):
    """Protocol for video processing operations."""
    
    async def get_video_info(self, video_data: bytes) -> VideoInfo:
        """Extract metadata from video."""
        ...
    
    async def extract_frames_at_timestamps(
        self,
        video_data: bytes,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        """Extract frames at specific timestamps."""
        ...
    
    async def extract_frames_at_fps(
        self,
        video_data: bytes,
        fps: float,
        max_frames: int = 60,
    ) -> list[ExtractedFrame]:
        """Extract frames at regular intervals."""
        ...


class FFmpegVideoProcessor:
    """
    Video processor using FFmpeg/FFprobe.
    
    All operations use temporary files because FFmpeg works best with
    file paths. We write the video data to a temp file, process it,
    read the output, then clean up.
    """
    
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        """
        Initialize processor with FFmpeg paths.
        
        Args:
            ffmpeg_path: Path to ffmpeg binary (default assumes it's in PATH)
            ffprobe_path: Path to ffprobe binary
        """
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path
        
        # verify ffmpeg is available
        try:
            result = subprocess.run(
                [self._ffmpeg, "-version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                raise RuntimeError("FFmpeg not working properly")
            logger.info("FFmpeg video processor initialized")
        except FileNotFoundError:
            raise RuntimeError(
                "FFmpeg not found. Install with: apt-get install ffmpeg"
            )
    
    async def get_video_info(self, video_data: bytes) -> VideoInfo:
        """
        Extract video metadata using FFprobe.
        
        FFprobe outputs JSON with stream info - we parse that to get
        duration, resolution, fps, codec.
        """
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_data)
            tmp_path = tmp.name
        
        try:
            # run ffprobe to get video info as JSON
            cmd = [
                self._ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                tmp_path
            ]
            
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"FFprobe failed: {result.stderr}")
            
            info = json.loads(result.stdout)
            
            # find video stream
            video_stream = None
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break
            
            if not video_stream:
                raise RuntimeError("No video stream found")
            
            # parse fps (can be a fraction like "30000/1001")
            fps_str = video_stream.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                num, denom = fps_str.split("/")
                fps = float(num) / float(denom)
            else:
                fps = float(fps_str)
            
            # get duration from format or stream
            duration = float(info.get("format", {}).get("duration", 0))
            if duration == 0:
                duration = float(video_stream.get("duration", 0))
            
            return VideoInfo(
                duration_seconds=duration,
                width=int(video_stream.get("width", 0)),
                height=int(video_stream.get("height", 0)),
                fps=fps,
                codec=video_stream.get("codec_name", "unknown"),
                file_size_bytes=len(video_data),
            )
            
        finally:
            os.unlink(tmp_path)
    
    async def extract_frames_at_timestamps(
        self,
        video_data: bytes,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        """
        Extract frames at specific timestamps.
        
        This is the key method for agentic analysis - the AI can say
        "show me frames at 0:12, 0:13, 0:14" and we extract just those.
        
        Uses FFmpeg's -ss (seek) option for each timestamp, outputting
        a single JPEG per timestamp.
        """
        if not timestamps:
            return []
        
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_data)
            video_path = tmp.name
        
        frames: list[ExtractedFrame] = []
        
        try:
            with tempfile.TemporaryDirectory() as output_dir:
                for i, ts in enumerate(timestamps):
                    output_path = os.path.join(output_dir, f"frame_{i:04d}.jpg")
                    
                    # -ss before -i for fast seeking
                    # -frames:v 1 to extract just one frame
                    # -q:v 2 for good jpeg quality
                    cmd = [
                        self._ffmpeg,
                        "-ss", str(ts),
                        "-i", video_path,
                        "-frames:v", "1",
                        "-q:v", "2",
                        "-y",  # overwrite
                        output_path
                    ]
                    
                    result = await asyncio.to_thread(
                        subprocess.run,
                        cmd,
                        capture_output=True,
                        timeout=10
                    )
                    
                    if result.returncode == 0 and os.path.exists(output_path):
                        with open(output_path, "rb") as f:
                            frame_data = f.read()
                        
                        frames.append(ExtractedFrame(
                            timestamp_seconds=ts,
                            frame_number=i,
                            data=frame_data,
                        ))
                    else:
                        logger.warning(
                            f"Failed to extract frame at {ts}s: {result.stderr.decode()}"
                        )
            
            logger.info(
                "Extracted frames at timestamps",
                extra={"count": len(frames), "timestamps": timestamps[:5]}
            )
            
            return frames
            
        finally:
            os.unlink(video_path)
    
    async def extract_frames_at_fps(
        self,
        video_data: bytes,
        fps: float,
        max_frames: int = 60,
    ) -> list[ExtractedFrame]:
        """
        Extract frames at regular intervals.
        
        Used for the initial analysis pass - extract every 0.5 seconds
        (or whatever fps is set to) to get a sparse overview.
        
        Args:
            video_data: Raw video bytes
            fps: Frames per second to extract (e.g., 0.5 = one every 2 seconds)
            max_frames: Maximum number of frames to extract
        """
        # first get video duration
        info = await self.get_video_info(video_data)
        
        # calculate timestamps
        interval = 1.0 / fps
        timestamps = []
        t = 0.0
        while t < info.duration_seconds and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval
        
        logger.info(
            "Extracting frames at FPS",
            extra={
                "fps": fps,
                "duration": info.duration_seconds,
                "frame_count": len(timestamps),
            }
        )
        
        return await self.extract_frames_at_timestamps(video_data, timestamps)


class MockVideoProcessor:
    """
    Mock video processor for local development without FFmpeg.
    
    Returns dummy video info and placeholder frames. Useful for
    testing the API flow without actual video processing.
    """
    
    def __init__(self):
        logger.info("Initialized mock video processor")
    
    async def get_video_info(self, video_data: bytes) -> VideoInfo:
        """Return dummy video info."""
        return VideoInfo(
            duration_seconds=30.0,
            width=1920,
            height=1080,
            fps=30.0,
            codec="h264",
            file_size_bytes=len(video_data),
        )
    
    async def extract_frames_at_timestamps(
        self,
        video_data: bytes,
        timestamps: list[float],
    ) -> list[ExtractedFrame]:
        """Return placeholder frames."""
        # create tiny 1x1 red jpeg for each timestamp
        # this is a valid minimal JPEG
        minimal_jpeg = bytes([
            0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
            0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
            0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
            0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
            0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
            0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
            0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
            0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
            0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
            0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
            0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
            0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
            0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
            0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
            0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
            0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0xFF,
            0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
            0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04,
            0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00,
            0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
            0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32,
            0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1,
            0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
            0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A,
            0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35,
            0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
            0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55,
            0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65,
            0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
            0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85,
            0x86, 0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94,
            0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
            0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2,
            0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA,
            0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
            0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8,
            0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6,
            0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
            0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA,
            0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00,
            0xFB, 0xD3, 0x28, 0xA0, 0x02, 0x8A, 0x28, 0x03,
            0xFF, 0xD9
        ])
        
        return [
            ExtractedFrame(
                timestamp_seconds=ts,
                frame_number=i,
                data=minimal_jpeg,
            )
            for i, ts in enumerate(timestamps)
        ]
    
    async def extract_frames_at_fps(
        self,
        video_data: bytes,
        fps: float,
        max_frames: int = 60,
    ) -> list[ExtractedFrame]:
        """Return placeholder frames at intervals."""
        # assume 30 second video
        duration = 30.0
        interval = 1.0 / fps
        timestamps = []
        t = 0.0
        while t < duration and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval
        
        return await self.extract_frames_at_timestamps(video_data, timestamps)


def create_video_processor(mock_mode: bool = False) -> VideoProcessor:
    """
    Factory function for video processor.
    
    Args:
        mock_mode: If True, return mock processor (no FFmpeg required)
    
    Returns:
        VideoProcessor implementation
    """
    if mock_mode:
        return MockVideoProcessor()
    
    return FFmpegVideoProcessor()
