"""
Usage limit repository for rate limiting.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from uuid import uuid4, UUID

logger = logging.getLogger(__name__)

IdentifierType = Literal["user_id", "ip_address"]


class UsageLimitRepository:
    """Rate limiting via Snowflake. Could swap for Redis at higher traffic."""

    def __init__(self, connection) -> None:
        self._conn = connection
    
    def check_and_increment(
        self,
        identifier: str,
        identifier_type: IdentifierType,
        resource_type: str,
        limit_max: int,
        period_hours: int = 24
    ) -> tuple[bool, int, int]:
        """Check limit and increment if allowed. Returns (allowed, count, max)."""
        cursor = self._conn.cursor()
        
        try:
            now = datetime.now(timezone.utc)
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            period_end = period_start + timedelta(hours=period_hours)
            
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
                limit_id, current_count, current_max = result

                if current_count >= limit_max:
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
                    1,
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
            # Fail open — prefer availability over strict limiting
            return True, 0, limit_max
        
        finally:
            cursor.close()
    
    def get_current_usage(
        self,
        identifier: str,
        identifier_type: IdentifierType,
        resource_type: str
    ) -> Optional[tuple[int, int, datetime]]:
        """Get usage without incrementing. Returns (count, max, period_end) or None."""
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
        """Admin: reset usage for an identifier."""
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

