"""
Application configuration via Pydantic settings.

Mock modes enable local development without external services.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables. Comma-separated for lists."""
    
    # API Configuration
    api_title: str = "SwimCoach AI API"
    api_version: str = "v1"
    api_keys: str = Field(default="dev-key-1,dev-key-2")
    rate_limit_bypass_keys: str = Field(default="")
    rate_limit_bypass_emails: str = Field(default="")
    rate_limit_bypass_user_ids: str = Field(default="")
    
    # Anthropic
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514")
    anthropic_max_tokens: int = Field(default=4096)
    anthropic_temperature: float = Field(default=0.7)
    
    # Snowflake
    snowflake_account: str = Field(default="")
    snowflake_user: str = Field(default="")
    snowflake_password: str = Field(default="")
    snowflake_private_key_path: Optional[str] = Field(default=None)
    snowflake_private_key_base64: Optional[str] = Field(default=None)
    snowflake_database: str = Field(default="SWIMCOACH")
    snowflake_schema: str = Field(default="COACHING")
    snowflake_warehouse: str = Field(default="COMPUTE_WH")
    snowflake_role: Optional[str] = Field(default=None)
    snowflake_mock_mode: bool = Field(default=False)
    
    # R2/S3 Storage
    r2_account_id: str = Field(default="")
    r2_access_key_id: str = Field(default="")
    r2_secret_access_key: str = Field(default="")
    r2_bucket_name: str = Field(default="swimcoach-videos")
    r2_endpoint_url: Optional[str] = Field(default=None)
    r2_mock_mode: bool = Field(default=False)
    
    # Application
    max_frames_per_upload: int = Field(default=60)
    max_upload_size_mb: int = Field(default=100)
    max_video_size_mb: int = Field(default=100)
    video_processor_mock_mode: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # Stale-job sweeper — BackgroundTasks is in-process/non-durable, so a worker
    # restart mid-analysis leaves a session stuck in "processing" forever. The
    # sweeper flips anything stuck past the threshold to "failed".
    stale_job_threshold_minutes: int = Field(default=10)
    sweeper_interval_seconds: int = Field(default=120)
    sweeper_enabled: bool = Field(default=True)
    cors_origins: str = Field(default="http://localhost:3000,https://ai-swim-coach.pages.dev")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    @property
    def api_keys_list(self) -> list[str]:
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]

    @property
    def rate_limit_bypass_keys_list(self) -> list[str]:
        return [key.strip() for key in self.rate_limit_bypass_keys.split(",") if key.strip()]

    @property
    def rate_limit_bypass_emails_list(self) -> list[str]:
        return [email.strip().lower() for email in self.rate_limit_bypass_emails.split(",") if email.strip()]

    @property
    def rate_limit_bypass_user_ids_list(self) -> list[str]:
        return [uid.strip() for uid in self.rate_limit_bypass_user_ids.split(",") if uid.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    
    @property
    def r2_endpoint(self) -> str:
        """Construct R2 endpoint URL from account ID, or use override."""
        if self.r2_endpoint_url:
            return self.r2_endpoint_url
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
    
    def validate_required_fields(self) -> list[str]:
        """Returns missing required fields (requirements vary by mock mode)."""
        missing = []

        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        
        if not self.snowflake_mock_mode and not self.snowflake_private_key_path:
            if not self.snowflake_account:
                missing.append("SNOWFLAKE_ACCOUNT")
            if not self.snowflake_user:
                missing.append("SNOWFLAKE_USER")
            if not self.snowflake_password and not self.snowflake_private_key_path:
                missing.append("SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH")
        
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
    """Cached settings singleton. Call get_settings.cache_clear() in tests."""
    return Settings()

