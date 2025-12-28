"""
Application configuration using Pydantic settings.

Configuration is loaded from environment variables with sensible defaults.
Using Pydantic's BaseSettings means we get:
- Type validation at startup (fail fast if config is wrong)
- Documentation of what's required vs optional
- Easy testing with different configurations

Mock modes enable local development without external services.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    All settings can be overridden via environment variables.
    For lists (like api_keys), use comma-separated values in env.
    """
    
    # API Configuration
    api_title: str = "SwimCoach AI API"
    api_version: str = "v1"
    api_keys: str = Field(
        default="dev-key-1,dev-key-2",
        description="Comma-separated API keys. Using a list enables key rotation without downtime."
    )
    rate_limit_bypass_keys: str = Field(
        default="",
        description="Comma-separated API keys that bypass rate limiting. For trusted users/admins."
    )
    rate_limit_bypass_emails: str = Field(
        default="",
        description="Comma-separated email addresses (from Clerk user ID) that bypass rate limiting."
    )
    
    # Anthropic Configuration
    anthropic_api_key: str = Field(
        default="",
        description="Claude API key. Required unless in mock mode."
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Claude model to use. Sonnet 4 provides good balance of capability and cost."
    )
    anthropic_max_tokens: int = Field(
        default=4096,
        description="Max tokens for Claude responses. Coaching feedback can be detailed."
    )
    anthropic_temperature: float = Field(
        default=0.7,
        description="Temperature for Claude. 0.7 allows some creativity in coaching advice."
    )
    
    # Snowflake Configuration
    snowflake_account: str = Field(
        default="",
        description="Snowflake account identifier"
    )
    snowflake_user: str = Field(
        default="",
        description="Snowflake service account username"
    )
    snowflake_password: str = Field(
        default="",
        description="Snowflake service account password"
    )
    snowflake_private_key_path: Optional[str] = Field(
        default=None,
        description="Path to RSA private key file for key-pair authentication"
    )
    snowflake_private_key_base64: Optional[str] = Field(
        default=None,
        description="Base64-encoded private key (for deployment, alternative to file path)"
    )
    snowflake_database: str = Field(
        default="SWIMCOACH",
        description="Snowflake database name"
    )
    snowflake_schema: str = Field(
        default="COACHING",
        description="Snowflake schema name"
    )
    snowflake_warehouse: str = Field(
        default="COMPUTE_WH",
        description="Snowflake warehouse for query execution"
    )
    snowflake_role: Optional[str] = Field(
        default=None,
        description="Snowflake role to use (optional)"
    )
    snowflake_mock_mode: bool = Field(
        default=False,
        description="Use in-memory mock instead of real Snowflake connection. Enables local dev without DB."
    )
    snowflake_private_key_base64: Optional[str] = Field(
        default=None,
        description="Base64-encoded private key (for deployment, alternative to file path)"
    )
    
    # R2/S3 Storage Configuration
    r2_account_id: str = Field(
        default="",
        description="Cloudflare account ID for R2"
    )
    r2_access_key_id: str = Field(
        default="",
        description="R2 access key ID"
    )
    r2_secret_access_key: str = Field(
        default="",
        description="R2 secret access key"
    )
    r2_bucket_name: str = Field(
        default="swimcoach-videos",
        description="R2 bucket name for video/frame storage"
    )
    r2_endpoint_url: Optional[str] = Field(
        default=None,
        description="R2 endpoint URL. Auto-constructed from account_id if not provided."
    )
    r2_mock_mode: bool = Field(
        default=False,
        description="Use in-memory mock instead of real R2. Enables local dev without object storage."
    )
    
    # Application Behavior
    max_frames_per_upload: int = Field(
        default=60,
        description="Maximum frames per video upload. Limits API payload size and analysis cost."
    )
    max_upload_size_mb: int = Field(
        default=100,
        description="Maximum total upload size in MB. Prevents abuse and controls costs."
    )
    
    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    
    # CORS
    cors_origins: str = Field(
        default="http://localhost:3000,https://ai-swim-coach.pages.dev",
        description="Comma-separated list of allowed CORS origins. Use * for development only."
    )
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    @property
    def api_keys_list(self) -> list[str]:
        """Parse comma-separated API keys into a list."""
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]
    
    @property
    def rate_limit_bypass_keys_list(self) -> list[str]:
        """Parse comma-separated bypass API keys into a list."""
        return [key.strip() for key in self.rate_limit_bypass_keys.split(",") if key.strip()]
    
    @property
    def rate_limit_bypass_emails_list(self) -> list[str]:
        """Parse comma-separated bypass emails into a list."""
        return [email.strip().lower() for email in self.rate_limit_bypass_emails.split(",") if email.strip()]
    
    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    
    @property
    def r2_endpoint(self) -> str:
        """
        Construct R2 endpoint URL from account ID.
        
        R2 endpoints follow the pattern: https://{account_id}.r2.cloudflarestorage.com
        This is S3-compatible but uses Cloudflare's network.
        """
        if self.r2_endpoint_url:
            return self.r2_endpoint_url
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
    
    def validate_required_fields(self) -> list[str]:
        """
        Validate that required fields are set based on mock mode settings.
        
        Returns list of missing required fields.
        This is separate from Pydantic validation because requirements
        depend on whether we're in mock mode.
        """
        missing = []
        
        # Anthropic is always required (no mock for LLM yet)
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        
        # Snowflake only required if not in mock mode
        if not self.snowflake_mock_mode and not self.snowflake_private_key_path:
            if not self.snowflake_account:
                missing.append("SNOWFLAKE_ACCOUNT")
            if not self.snowflake_user:
                missing.append("SNOWFLAKE_USER")
            # Need either password or private key
            if not self.snowflake_password and not self.snowflake_private_key_path:
                missing.append("SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH")
        
        # R2 only required if not in mock mode
        if not self.r2_mock_mode:
            if not self.r2_account_id:
                missing.append("R2_ACCOUNT_ID")
            if not self.r2_access_key_id:
                missing.append("R2_ACCESS_KEY_ID")
            if not self.r2_secret_access_key:
                missing.append("R2_SECRET_ACCESS_KEY")
        
        return missing


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    
    Using lru_cache means we only load settings once per process.
    This is safe because settings don't change during runtime.
    For tests, you can call get_settings.cache_clear() to reset.
    """
    return Settings()

