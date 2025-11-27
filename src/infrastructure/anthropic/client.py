"""
Anthropic Claude API client wrapper.

This module provides a thin wrapper around the Anthropic SDK that:
1. Implements our VisionModelClient protocol
2. Handles API-specific details (base64 encoding, message format)
3. Provides consistent error handling
4. Enables easy mocking for tests

The wrapper is intentionally thin. We're not building a general-purpose
client library — just enough to serve our use case cleanly.
"""

import base64
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic
from anthropic import APIError, RateLimitError

from src.core.analysis.coach import VisionModelClient


logger = logging.getLogger(__name__)


class AnthropicClientError(Exception):
    """Raised when API calls fail."""
    pass


class RateLimitExceeded(AnthropicClientError):
    """Raised when we hit rate limits."""
    pass


@dataclass
class AnthropicConfig:
    """
    Configuration for the Anthropic client.
    
    Using a dataclass instead of raw values means:
    - Configuration is explicit and documented
    - We can validate at construction time
    - Easy to create test configurations
    """
    api_key: str
    model: str = "claude-sonnet-4-20250514"  # Good balance of capability and cost
    max_tokens: int = 4096
    temperature: float = 0.7  # Some creativity in coaching advice
    
    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("API key is required")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if not 0 <= self.temperature <= 1:
            raise ValueError("temperature must be between 0 and 1")


class AnthropicVisionClient(VisionModelClient):
    """
    Implementation of VisionModelClient using Claude.
    
    This class knows about Anthropic's API format but doesn't know
    about swimming or coaching. It just sends images and text,
    gets responses back.
    """
    
    def __init__(self, config: AnthropicConfig) -> None:
        self._config = config
        self._client = anthropic.Anthropic(api_key=config.api_key)
    
    async def analyze_images(
        self,
        images: list[bytes],
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """
        Send images to Claude for analysis.
        
        Images are base64 encoded and sent as part of the user message.
        Claude's vision models can handle multiple images in a single request.
        """
        if not images:
            raise ValueError("At least one image is required")
        
        # Build the content array with images and text
        content = self._build_image_content(images, user_prompt)
        
        try:
            response = self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": content}
                ],
            )
            
            return self._extract_text_response(response)
            
        except RateLimitError as e:
            logger.warning("Rate limit hit", extra={"error": str(e)})
            raise RateLimitExceeded("API rate limit exceeded. Please try again later.")
        except APIError as e:
            logger.error("API error", extra={"error": str(e), "status": e.status_code})
            raise AnthropicClientError(f"API error: {e.message}")
    
    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
    ) -> str:
        """
        Continue a conversation with Claude.
        
        Takes a list of messages in the format:
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        if not messages:
            raise ValueError("At least one message is required")
        
        # Validate message format
        validated_messages = self._validate_messages(messages)
        
        try:
            response = self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system_prompt,
                messages=validated_messages,
            )
            
            return self._extract_text_response(response)
            
        except RateLimitError as e:
            logger.warning("Rate limit hit during chat", extra={"error": str(e)})
            raise RateLimitExceeded("API rate limit exceeded. Please try again later.")
        except APIError as e:
            logger.error("API error during chat", extra={"error": str(e)})
            raise AnthropicClientError(f"API error: {e.message}")
    
    def _build_image_content(
        self,
        images: list[bytes],
        text_prompt: str,
    ) -> list[dict]:
        """
        Build the content array for a multi-image request.
        
        Claude expects:
        [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "..."}},
            {"type": "image", "source": {...}},
            {"type": "text", "text": "..."}
        ]
        """
        content = []
        
        for image in images:
            # Detect image type from magic bytes
            media_type = self._detect_image_type(image)
            
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image).decode("utf-8"),
                }
            })
        
        # Add the text prompt at the end
        content.append({
            "type": "text",
            "text": text_prompt,
        })
        
        return content
    
    def _detect_image_type(self, image_data: bytes) -> str:
        """
        Detect image MIME type from magic bytes.
        
        We could use a library like python-magic, but for our limited
        use case (jpg/png from ffmpeg), simple byte checking is fine.
        """
        if image_data[:3] == b'\xff\xd8\xff':
            return "image/jpeg"
        elif image_data[:8] == b'\x89PNG\r\n\x1a\n':
            return "image/png"
        elif image_data[:6] in (b'GIF87a', b'GIF89a'):
            return "image/gif"
        elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
            return "image/webp"
        else:
            # Default to JPEG since that's what ffmpeg produces
            return "image/jpeg"
    
    def _validate_messages(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """
        Validate and clean message format.
        
        Ensures messages alternate between user and assistant,
        starting with user (Claude's requirement).
        """
        validated = []
        expected_role = "user"
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role not in ("user", "assistant"):
                raise ValueError(f"Invalid message role: {role}")
            if not content:
                continue  # Skip empty messages
            
            # If roles don't alternate, we might need to merge messages
            # For now, just validate strictly
            if role != expected_role:
                # Insert a placeholder if needed
                logger.warning(
                    "Non-alternating message roles",
                    extra={"expected": expected_role, "got": role}
                )
            
            validated.append({"role": role, "content": content})
            expected_role = "assistant" if role == "user" else "user"
        
        return validated
    
    def _extract_text_response(self, response) -> str:
        """Extract text content from API response."""
        if not response.content:
            return ""
        
        # Response content is a list of blocks
        text_blocks = [
            block.text
            for block in response.content
            if hasattr(block, 'text')
        ]
        
        return "\n".join(text_blocks)


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------

def create_anthropic_client(
    api_key: Optional[str] = None,
    model: str = "claude-sonnet-4-20250514",
) -> AnthropicVisionClient:
    """
    Factory function to create configured client.
    
    Reads API key from parameter or environment variable.
    Using a factory function rather than direct construction:
    - Centralizes configuration logic
    - Provides sensible defaults
    - Makes the common case simple
    """
    import os
    
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "API key must be provided or set in ANTHROPIC_API_KEY environment variable"
        )
    
    config = AnthropicConfig(api_key=key, model=model)
    return AnthropicVisionClient(config)
