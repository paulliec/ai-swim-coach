"""
Video processing infrastructure.

Handles server-side video processing using FFmpeg:
- Video metadata extraction
- Frame extraction at specific timestamps
- Frame extraction at given FPS rates

This enables the agentic analysis flow where the AI can request
additional frames from specific parts of the video.
"""

from .processor import (
    VideoProcessor,
    VideoInfo,
    ExtractedFrame,
    create_video_processor,
)

__all__ = [
    "VideoProcessor",
    "VideoInfo",
    "ExtractedFrame",
    "create_video_processor",
]
