"""
Infrastructure layer - external service integrations.

Each subdirectory wraps an external dependency:
- anthropic: Claude API client
- snowflake: Database persistence
- storage: Object storage (R2/S3)

These wrappers translate between external formats and our domain models.
"""

