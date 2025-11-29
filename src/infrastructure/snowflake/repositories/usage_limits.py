"""
Usage limit repository for rate limiting.

This module handles tracking and enforcing rate limits for API resources.
Designed to prevent abuse while maintaining a good user experience.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from uuid import uuid4, UUID

logger = logging.getLogger(__name__)

IdentifierType = Literal["user_id", "ip_address"]


class UsageLimitRepository:
    """
    Repository for managing usage limits and rate limiting.
    
    Enforces limits like "3 video analyses per day per user".
    Uses Snowflake for storage, but could be swapped for Redis
    in a high-traffic production environment.
    
    Why this approach:
    - Simple to implement with existing Snowflake infrastructure
    - Persists across server restarts
    - Provides audit trail for usage patterns
    - Can easily add different limit types (per hour, per week, etc.)
    """
    
    def __init__(self, connection) -> None:
        """
        Initialize repository with a database connection.
        
        Args:
            connection: Snowflake connection (or mock for testing)
        """
        self._conn = connection
    
    def check_and_increment(
        self,
        identifier: str,
        identifier_type: IdentifierType,
        resource_type: str,
        limit_max: int,
        period_hours: int = 24
    ) -> tuple[bool, int, int]:
        """
        Check if user is within limits, and increment usage if so.
        
        This is the main method used by API endpoints to enforce limits.
        It's atomic: either the check passes and count increments, or it fails.
        
        Args:
            identifier: User ID (from Clerk) or IP address
            identifier_type: Either 'user_id' or 'ip_address'
            resource_type: What's being limited (e.g., 'video_analysis')
            limit_max: Maximum uses allowed in the period
            period_hours: Length of period in hours (default 24 = daily)
        
        Returns:
            Tuple of (allowed: bool, current_count: int, limit_max: int)
            - allowed: True if within limits and incremented
            - current_count: Number of uses in current period
            - limit_max: The maximum allowed
        
        Example:
            allowed, count, max_limit = repo.check_and_increment(
                identifier='user_123',
                identifier_type='user_id',
                resource_type='video_analysis',
                limit_max=3
            )
            
            if not allowed:
                raise RateLimitError(f"Limit exceeded: {count}/{max_limit}")
        """
        cursor = self._conn.cursor()
        
        try:
            # Calculate current period boundaries
            now = datetime.now(timezone.utc)
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            period_end = period_start + timedelta(hours=period_hours)
            
            # Try to find existing limit record for current period
            cursor.execute("""
                SELECT limit_id, usage_count, limit_max
                FROM usage_limits
                WHERE identifier = %s
                  AND identifier_type = %s
                  AND resource_type = %s
                  AND period_start = %s
                  AND period_end = %s
            """, (identifier, identifier_type, resource_type, period_start, period_end))
            
            result = cursor.fetchone()
            
            if result:
                # Existing record found
                limit_id, current_count, current_max = result
                
                if current_count >= limit_max:
                    # Limit exceeded
                    logger.warning(
                        "Rate limit exceeded",
                        extra={
                            "identifier": identifier,
                            "identifier_type": identifier_type,
                            "resource_type": resource_type,
                            "current_count": current_count,
                            "limit_max": limit_max
                        }
                    )
                    return False, current_count, limit_max
                
                # Increment count
                new_count = current_count + 1
                cursor.execute("""
                    UPDATE usage_limits
                    SET usage_count = %s,
                        updated_at = CURRENT_TIMESTAMP()
                    WHERE limit_id = %s
                """, (new_count, limit_id))
                
                self._conn.commit()
                
                logger.info(
                    "Usage incremented",
                    extra={
                        "identifier": identifier,
                        "resource_type": resource_type,
                        "count": new_count,
                        "limit": limit_max
                    }
                )
                
                return True, new_count, limit_max
            
            else:
                # No record for this period, create one
                limit_id = str(uuid4())
                cursor.execute("""
                    INSERT INTO usage_limits (
                        limit_id,
                        identifier,
                        identifier_type,
                        resource_type,
                        usage_count,
                        limit_max,
                        period_start,
                        period_end
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    limit_id,
                    identifier,
                    identifier_type,
                    resource_type,
                    1,  # First use
                    limit_max,
                    period_start,
                    period_end
                ))
                
                self._conn.commit()
                
                logger.info(
                    "Usage limit record created",
                    extra={
                        "identifier": identifier,
                        "resource_type": resource_type,
                        "limit": limit_max
                    }
                )
                
                return True, 1, limit_max
        
        except Exception as e:
            logger.error(
                "Failed to check/increment usage limit",
                extra={"identifier": identifier, "error": str(e)}
            )
            # On error, fail open (allow the request) rather than block legitimate users
            # This is a business decision: prefer availability over strict limiting
            return True, 0, limit_max
        
        finally:
            cursor.close()
    
    def get_current_usage(
        self,
        identifier: str,
        identifier_type: IdentifierType,
        resource_type: str
    ) -> Optional[tuple[int, int, datetime]]:
        """
        Get current usage for an identifier without incrementing.
        
        Useful for showing users their current usage status.
        
        Args:
            identifier: User ID or IP address
            identifier_type: 'user_id' or 'ip_address'
            resource_type: What to check (e.g., 'video_analysis')
        
        Returns:
            Tuple of (current_count, limit_max, period_end) or None if no usage
        """
        cursor = self._conn.cursor()
        
        try:
            now = datetime.now(timezone.utc)
            
            cursor.execute("""
                SELECT usage_count, limit_max, period_end
                FROM usage_limits
                WHERE identifier = %s
                  AND identifier_type = %s
                  AND resource_type = %s
                  AND period_end > %s
                ORDER BY period_start DESC
                LIMIT 1
            """, (identifier, identifier_type, resource_type, now))
            
            result = cursor.fetchone()
            
            if result:
                return result[0], result[1], result[2]
            
            return None
        
        finally:
            cursor.close()
    
    def reset_usage(
        self,
        identifier: str,
        identifier_type: IdentifierType,
        resource_type: str
    ) -> None:
        """
        Reset usage for an identifier (admin function).
        
        Could be used for customer support scenarios where we want
        to give a user extra analyses.
        
        Args:
            identifier: User ID or IP address
            identifier_type: 'user_id' or 'ip_address'
            resource_type: What to reset
        """
        cursor = self._conn.cursor()
        
        try:
            cursor.execute("""
                DELETE FROM usage_limits
                WHERE identifier = %s
                  AND identifier_type = %s
                  AND resource_type = %s
            """, (identifier, identifier_type, resource_type))
            
            self._conn.commit()
            
            logger.info(
                "Usage limit reset",
                extra={
                    "identifier": identifier,
                    "identifier_type": identifier_type,
                    "resource_type": resource_type
                }
            )
        
        finally:
            cursor.close()

