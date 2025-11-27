"""
Application configuration using Pydantic settings.

Configuration comes from environment variables with sensible defaults.
Supports mock modes for local development.
"""

from .settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]

