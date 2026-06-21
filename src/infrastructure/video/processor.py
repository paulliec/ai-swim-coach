"""
Video processing via FFmpeg.

Server-side so the AI can request specific frames on demand.
"""

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)

# Generous timeout — Fly.io shared VMs can be slow
FFMPEG_TIMEOUT_SECONDS = 30


class VideoProcessingError(Exception):
    """Raised when video processing fails."""
    pass


async def _run_ffmpeg(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    """Run an ffmpeg/ffprobe command via the event loop's child watcher.

    NOT subprocess.run-on-a-threadpool: forking from a threadpool worker while other
    threads hold locks deadlocks the child before exec under the live server (fine in
    isolation, hangs in-process). create_subprocess_exec goes through the loop's child
    watcher instead. stdin is /dev/null so ffmpeg never blocks waiting on input.
    Returns (returncode, stdout, stderr); raises asyncio.TimeoutError on timeout.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, stdout, stderr


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
    """FFmpeg/FFprobe video processor. Uses temp files for all operations."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        # No blocking `ffmpeg -version` probe here: subprocess.run forks from the
        # threadpool worker that builds this dep and deadlocks the child before exec
        # under the live server. ffmpeg presence is verified at deploy time; if it's
        # genuinely missing, the first extraction call fails loudly instead.
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path
        logger.info("FFmpeg video processor initialized")
    
    async def get_video_info(self, video_data: bytes) -> VideoInfo:
        """Extract metadata via FFprobe."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_data)
            tmp_path = tmp.name
        
        try:
            cmd = [
                self._ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                tmp_path
            ]

            try:
                returncode, stdout, stderr = await _run_ffmpeg(cmd, timeout=30)
            except asyncio.TimeoutError:
                raise RuntimeError("FFprobe timed out reading video metadata")

            if returncode != 0:
                raise RuntimeError(f"FFprobe failed: {stderr.decode(errors='ignore')}")

            info = json.loads(stdout)

            video_stream = None
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break
            
            if not video_stream:
                raise RuntimeError("No video stream found")
            
            # fps can be a fraction like "30000/1001"
            fps_str = video_stream.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                num, denom = fps_str.split("/")
                fps = float(num) / float(denom)
            else:
                fps = float(fps_str)
            
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
        """Extract specific frames — key method for agentic analysis."""
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

                    cmd = [
                        self._ffmpeg,
                        "-nostdin",  # never wait on stdin (also DEVNULL'd in _run_ffmpeg)
                        "-ss", str(ts),
                        "-i", video_path,
                        "-frames:v", "1",
                        "-q:v", "2",
                        "-y",  # overwrite
                        output_path
                    ]

                    try:
                        returncode, _stdout, stderr = await _run_ffmpeg(
                            cmd, timeout=FFMPEG_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            f"FFmpeg timed out extracting frame at {ts}s after {FFMPEG_TIMEOUT_SECONDS}s"
                        )
                        raise VideoProcessingError(
                            f"Video processing timed out. The video may be too large or in an unsupported format. "
                            f"Try a shorter video (<2 minutes) or convert to MP4/H.264."
                        )

                    if returncode == 0 and os.path.exists(output_path):
                        with open(output_path, "rb") as f:
                            frame_data = f.read()

                        frames.append(ExtractedFrame(
                            timestamp_seconds=ts,
                            frame_number=i,
                            data=frame_data,
                        ))
                    else:
                        logger.warning(
                            f"Failed to extract frame at {ts}s: {stderr.decode(errors='ignore')}"
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
        """Extract frames at regular intervals for initial sparse pass."""
        info = await self.get_video_info(video_data)

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
    """Mock processor for local dev without FFmpeg. Returns placeholder frames."""
    
    def __init__(self):
        logger.info("Initialized mock video processor")
    
    async def get_video_info(self, video_data: bytes) -> VideoInfo:
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
        # Valid minimal 1x1 JPEG
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
        duration = 30.0
        interval = 1.0 / fps
        timestamps = []
        t = 0.0
        while t < duration and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval
        
        return await self.extract_frames_at_timestamps(video_data, timestamps)


def create_video_processor(mock_mode: bool = False) -> VideoProcessor:
    """Factory: returns FFmpeg or mock processor."""
    if mock_mode:
        return MockVideoProcessor()
    
    return FFmpegVideoProcessor()
