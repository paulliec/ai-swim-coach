"""
Anthropic Claude API client wrapper.

Thin wrapper implementing VisionModelClient protocol.
Handles base64 encoding, message format, and rate limit retries.
"""

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic
from anthropic import APIError, RateLimitError

# Retry configuration for rate limit errors
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_BASE_DELAY_SECONDS = 20  # 20s, 40s — max 60s total per encounter

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
    """Configuration for the Anthropic client."""
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
    """VisionModelClient implementation using Claude. Knows API format, not swimming."""
    
    def __init__(self, config: AnthropicConfig) -> None:
        self._config = config
        # Disable SDK's built-in retry so our _call_with_retry handles
        # all rate-limit backoff — prevents double-retry compounding.
        self._client = anthropic.Anthropic(api_key=config.api_key, max_retries=0)
    
    async def _call_with_retry(self, operation_name: str, api_call):
        """
        Call the Anthropic API with exponential backoff on rate limit errors.

        Retries up to RATE_LIMIT_MAX_RETRIES times with increasing delays
        (30s, 60s, 90s) before giving up. This keeps the request alive
        server-side so the client doesn't need to poll or resume.
        """
        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                return api_call()
            except RateLimitError as e:
                if attempt >= RATE_LIMIT_MAX_RETRIES:
                    logger.warning(
                        f"Rate limit: all {RATE_LIMIT_MAX_RETRIES} retries exhausted for {operation_name}",
                        extra={"error": str(e)},
                    )
                    raise RateLimitExceeded("API rate limit exceeded after retries. Please try again later.")

                delay = RATE_LIMIT_BASE_DELAY_SECONDS * (attempt + 1)
                logger.info(
                    f"Rate limit hit on {operation_name}, retrying in {delay}s "
                    f"(attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES})",
                    extra={"error": str(e), "delay": delay},
                )
                await asyncio.sleep(delay)

    async def analyze_images(
        self,
        images: list[bytes],
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Send images to Claude for analysis. Retries on rate limits."""
        if not images:
            raise ValueError("At least one image is required")

        content = self._build_image_content(images, user_prompt)

        def _call():
            return self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": content}
                ],
            )

        try:
            response = await self._call_with_retry("analyze_images", _call)
            return self._extract_text_response(response)
        except RateLimitExceeded:
            raise
        except APIError as e:
            logger.error("API error", extra={"error": str(e), "status": e.status_code})
            raise AnthropicClientError(f"API error: {e.message}")

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
    ) -> str:
        """Continue a conversation. Retries on rate limits."""
        if not messages:
            raise ValueError("At least one message is required")

        validated_messages = self._validate_messages(messages)

        def _call():
            return self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system_prompt,
                messages=validated_messages,
            )

        try:
            response = await self._call_with_retry("chat", _call)
            return self._extract_text_response(response)
        except RateLimitExceeded:
            raise
        except APIError as e:
            logger.error("API error during chat", extra={"error": str(e)})
            raise AnthropicClientError(f"API error: {e.message}")
    
    def _build_image_content(
        self,
        images: list[bytes],
        text_prompt: str,
    ) -> list[dict]:
        """Build content array: base64 images + text prompt."""
        content = []
        
        for image in images:
            media_type = self._detect_image_type(image)
            
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image).decode("utf-8"),
                }
            })
        
        content.append({
            "type": "text",
            "text": text_prompt,
        })
        
        return content
    
    def _detect_image_type(self, image_data: bytes) -> str:
        """Detect MIME type from magic bytes. Defaults to JPEG (ffmpeg output)."""
        if image_data[:3] == b'\xff\xd8\xff':
            return "image/jpeg"
        elif image_data[:8] == b'\x89PNG\r\n\x1a\n':
            return "image/png"
        elif image_data[:6] in (b'GIF87a', b'GIF89a'):
            return "image/gif"
        elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
            return "image/webp"
        else:
            return "image/jpeg"
    
    def _validate_messages(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Validate alternating user/assistant roles (Claude requirement)."""
        validated = []
        expected_role = "user"
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role not in ("user", "assistant"):
                raise ValueError(f"Invalid message role: {role}")
            if not content:
                continue  # Skip empty messages
            
            if role != expected_role:
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
    """Create client from param or ANTHROPIC_API_KEY env var."""
    import os
    
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "API key must be provided or set in ANTHROPIC_API_KEY environment variable"
        )
    
    config = AnthropicConfig(api_key=key, model=model)
    return AnthropicVisionClient(config)
