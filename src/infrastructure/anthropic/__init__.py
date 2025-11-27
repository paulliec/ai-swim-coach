"""
Anthropic Claude API client wrapper.

Implements the VisionModelClient protocol from core.analysis.coach.
"""

from .client import AnthropicVisionClient, AnthropicConfig, create_anthropic_client

__all__ = ["AnthropicVisionClient", "AnthropicConfig", "create_anthropic_client"]

