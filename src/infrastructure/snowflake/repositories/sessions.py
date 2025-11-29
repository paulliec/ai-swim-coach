"""
Snowflake repository for coaching sessions.

This module implements the repository pattern for session data access.
The repository:
1. Translates between domain models and database representations
2. Encapsulates all SQL queries
3. Provides a clean interface for the application layer

The application code never writes SQL directly — it asks the repository
for what it needs in domain terms.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID

from src.core.analysis.models import (
    AnalysisResult,
    ChatMessage,
    CoachingFeedback,
    CoachingSession,
    FeedbackPriority,
    StrokeType,
    TechniqueCategory,
    TechniqueObservation,
    VideoMetadata,
)


logger = logging.getLogger(__name__)


class SnowflakeConnection(Protocol):
    """
    Protocol for Snowflake connections.
    
    Using a protocol means tests can provide a mock without
    importing the actual snowflake-connector-python.
    """
    
    def cursor(self): ...
    def commit(self) -> None: ...


@dataclass
class SnowflakeConfig:
    """Configuration for Snowflake connection."""
    account: str
    user: str
    password: Optional[str] = None
    private_key_path: Optional[str] = None
    database: str = "SWIMCOACH"
    schema: str = "COACHING"
    warehouse: str = "COMPUTE_WH"
    role: Optional[str] = None


class SessionNotFoundError(Exception):
    """Raised when a requested session doesn't exist."""
    pass


class SessionRepository:
    """
    Repository for coaching session persistence.
    
    Each method corresponds to a use case the application needs:
    - save_session: Persist a new or updated session
    - get_session: Load a session by ID
    - list_recent: Get recent sessions for display
    
    The repository handles the translation between our rich domain
    objects and the flat database representation.
    """
    
    def __init__(self, connection: SnowflakeConnection) -> None:
        self._conn = connection
    
    def save_session(self, session: CoachingSession) -> None:
        """
        Persist a coaching session and all its related data.
        
        This method is idempotent — calling it multiple times with
        the same session will update rather than duplicate.
        """
        cursor = self._conn.cursor()
        
        try:
            # Save video if present
            if session.video:
                self._upsert_video(cursor, session.video)
            
            # Save session record
            self._upsert_session(cursor, session)
            
            # Save analysis if present
            if session.analysis:
                self._upsert_analysis(cursor, session.analysis, session.id)
            
            # Save new messages (we don't update messages, only append)
            self._save_new_messages(cursor, session)
            
            self._conn.commit()
            
        except Exception as e:
            logger.error(
                "Failed to save session",
                extra={"session_id": str(session.id), "error": str(e)}
            )
            raise
        finally:
            cursor.close()
    
    def get_session(self, session_id: UUID) -> CoachingSession:
        """
        Load a complete coaching session by ID.
        
        Returns the session with video metadata, analysis results,
        and full conversation history.
        """
        cursor = self._conn.cursor()
        
        try:
            # Get session with video and analysis
            cursor.execute("""
                SELECT 
                    s.session_id,
                    s.created_at,
                    s.updated_at,
                    s.status,
                    v.video_id,
                    v.filename,
                    v.storage_path,
                    v.duration_seconds,
                    v.resolution_width,
                    v.resolution_height,
                    v.fps,
                    v.file_size_bytes,
                    v.uploaded_at,
                    v.stroke_type AS video_stroke_type,
                    a.analysis_id,
                    a.stroke_type AS analysis_stroke_type,
                    a.summary,
                    a.observations,
                    a.feedback,
                    a.frame_count_analyzed,
                    a.analyzed_at
                FROM coaching_sessions s
                LEFT JOIN videos v ON s.video_id = v.video_id
                LEFT JOIN analyses a ON s.analysis_id = a.analysis_id
                WHERE s.session_id = %s
            """, (str(session_id),))
            
            row = cursor.fetchone()
            if not row:
                raise SessionNotFoundError(f"Session {session_id} not found")
            
            # Get conversation messages
            cursor.execute("""
                SELECT message_id, role, content, created_at
                FROM messages
                WHERE session_id = %s
                ORDER BY sequence_number
            """, (str(session_id),))
            
            message_rows = cursor.fetchall()
            
            return self._build_session_from_rows(row, message_rows)
            
        finally:
            cursor.close()
    
    def list_recent(
        self,
        limit: int = 20,
        include_completed: bool = True,
    ) -> list[CoachingSession]:
        """
        List recent coaching sessions.
        
        Returns sessions with basic info (no full conversation history).
        Useful for dashboard/list views.
        """
        cursor = self._conn.cursor()
        
        try:
            status_filter = "" if include_completed else "AND s.status = 'active'"
            
            cursor.execute(f"""
                SELECT 
                    s.session_id,
                    s.created_at,
                    s.updated_at,
                    s.status,
                    v.video_id,
                    v.filename,
                    v.duration_seconds,
                    v.stroke_type,
                    a.analysis_id,
                    a.summary
                FROM coaching_sessions s
                LEFT JOIN videos v ON s.video_id = v.video_id
                LEFT JOIN analyses a ON s.analysis_id = a.analysis_id
                WHERE v.deleted_at IS NULL
                {status_filter}
                ORDER BY s.updated_at DESC
                LIMIT %s
            """, (limit,))
            
            rows = cursor.fetchall()
            return [self._build_session_summary(row) for row in rows]
            
        finally:
            cursor.close()
    
    # -----------------------------------------------------------------------
    # Private Methods
    # -----------------------------------------------------------------------
    
    def _upsert_video(self, cursor, video: VideoMetadata) -> None:
        """Insert or update video metadata."""
        cursor.execute("""
            MERGE INTO videos AS target
            USING (SELECT %s AS video_id) AS source
            ON target.video_id = source.video_id
            WHEN MATCHED THEN UPDATE SET
                filename = %s,
                storage_path = %s,
                duration_seconds = %s,
                resolution_width = %s,
                resolution_height = %s,
                fps = %s,
                file_size_bytes = %s
            WHEN NOT MATCHED THEN INSERT (
                video_id, filename, storage_path, duration_seconds,
                resolution_width, resolution_height, fps, file_size_bytes,
                uploaded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            str(video.id),
            video.filename, video.storage_path, video.duration_seconds,
            video.resolution[0], video.resolution[1], video.fps, video.file_size_bytes,
            str(video.id), video.filename, video.storage_path, video.duration_seconds,
            video.resolution[0], video.resolution[1], video.fps, video.file_size_bytes,
            video.uploaded_at,
        ))
    
    def _upsert_session(self, cursor, session: CoachingSession) -> None:
        """Insert or update session record."""
        cursor.execute("""
            MERGE INTO coaching_sessions AS target
            USING (SELECT %s AS session_id) AS source
            ON target.session_id = source.session_id
            WHEN MATCHED THEN UPDATE SET
                video_id = %s,
                analysis_id = %s,
                updated_at = %s
            WHEN NOT MATCHED THEN INSERT (
                session_id, video_id, analysis_id, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s)
        """, (
            str(session.id),
            str(session.video.id) if session.video else None,
            str(session.analysis.id) if session.analysis else None,
            session.updated_at,
            str(session.id),
            str(session.video.id) if session.video else None,
            str(session.analysis.id) if session.analysis else None,
            session.created_at,
            session.updated_at,
        ))
    
    def _upsert_analysis(
        self,
        cursor,
        analysis: AnalysisResult,
        session_id: UUID,
    ) -> None:
        """Insert or update analysis results."""
        # Convert observations and feedback to JSON
        observations_json = json.dumps([
            {
                "category": obs.category.value,
                "description": obs.description,
                "time_range": {
                    "start": obs.time_range.start.seconds,
                    "end": obs.time_range.end.seconds,
                } if obs.time_range else None,
            }
            for obs in analysis.observations
        ])
        
        feedback_json = json.dumps([
            {
                "id": str(fb.id),
                "priority": fb.priority.value,
                "observation": {
                    "category": fb.observation.category.value,
                    "description": fb.observation.description,
                },
                "recommendation": fb.recommendation,
                "drill_suggestions": fb.drill_suggestions,
            }
            for fb in analysis.feedback
        ])
        
        cursor.execute("""
            MERGE INTO analyses AS target
            USING (SELECT %s AS analysis_id) AS source
            ON target.analysis_id = source.analysis_id
            WHEN MATCHED THEN UPDATE SET
                summary = %s,
                observations = PARSE_JSON(%s),
                feedback = PARSE_JSON(%s),
                frame_count_analyzed = %s
            WHEN NOT MATCHED THEN INSERT (
                analysis_id, video_id, stroke_type, summary,
                observations, feedback, frame_count_analyzed, analyzed_at
            ) VALUES (%s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s), %s, %s)
        """, (
            str(analysis.id),
            analysis.summary, observations_json, feedback_json,
            analysis.frame_count_analyzed,
            str(analysis.id), str(analysis.video_id),
            analysis.stroke_type.value, analysis.summary,
            observations_json, feedback_json,
            analysis.frame_count_analyzed, analysis.analyzed_at,
        ))
    
    def _save_new_messages(self, cursor, session: CoachingSession) -> None:
        """Save any new messages that aren't already persisted."""
        if not session.conversation:
            return
        
        # Get existing message IDs
        cursor.execute("""
            SELECT message_id FROM messages WHERE session_id = %s
        """, (str(session.id),))
        
        existing_ids = {row[0] for row in cursor.fetchall()}
        
        # Insert new messages
        new_messages = [
            msg for msg in session.conversation
            if str(msg.id) not in existing_ids
        ]
        
        for i, msg in enumerate(new_messages):
            sequence = len(existing_ids) + i + 1
            cursor.execute("""
                INSERT INTO messages (message_id, session_id, role, content, 
                                      created_at, sequence_number)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                str(msg.id), str(session.id), msg.role, msg.content,
                msg.timestamp, sequence,
            ))
    
    def _build_session_from_rows(
        self,
        session_row,
        message_rows: list,
    ) -> CoachingSession:
        """Construct a CoachingSession from database rows."""
        # Build video if present
        video = None
        if session_row[4]:  # video_id
            video = VideoMetadata(
                id=UUID(session_row[4]),
                filename=session_row[5],
                storage_path=session_row[6],
                duration_seconds=session_row[7] or 0.0,
                resolution=(session_row[8] or 0, session_row[9] or 0),
                fps=session_row[10] or 0.0,
                file_size_bytes=session_row[11] or 0,
                uploaded_at=session_row[12],
            )
        
        # Build analysis if present
        analysis = None
        if session_row[14]:  # analysis_id
            analysis = AnalysisResult(
                id=UUID(session_row[14]),
                video_id=UUID(session_row[4]) if session_row[4] else UUID(int=0),
                stroke_type=StrokeType(session_row[15]) if session_row[15] else StrokeType.FREESTYLE,
                summary=session_row[16] or "",
                observations=self._parse_observations(session_row[17]),
                feedback=self._parse_feedback(session_row[18]),
                frame_count_analyzed=session_row[19] or 0,
                analyzed_at=session_row[20],
            )
        
        # Build messages
        messages = [
            ChatMessage(
                id=UUID(row[0]),
                role=row[1],
                content=row[2],
                timestamp=row[3],
            )
            for row in message_rows
        ]
        
        return CoachingSession(
            id=UUID(session_row[0]),
            video=video,
            analysis=analysis,
            conversation=messages,
            created_at=session_row[1],
            updated_at=session_row[2],
        )
    
    def _build_session_summary(self, row) -> CoachingSession:
        """Build a lightweight session from summary query."""
        video = None
        if row[4]:
            video = VideoMetadata(
                id=UUID(row[4]),
                filename=row[5],
                duration_seconds=row[6] or 0.0,
            )
        
        analysis = None
        if row[8]:
            analysis = AnalysisResult(
                id=UUID(row[8]),
                summary=row[9] or "",
            )
        
        return CoachingSession(
            id=UUID(row[0]),
            video=video,
            analysis=analysis,
            created_at=row[1],
            updated_at=row[2],
        )
    
    def _parse_variant_json(self, variant_data):
        """
        Parse Snowflake VARIANT data that might be a string or already parsed.
        
        Snowflake's VARIANT type behavior varies by driver and context:
        - snowflake-connector-python: Returns VARIANT as JSON string
        - snowflake-sqlalchemy: May return parsed dict/list
        - Mock implementation: Returns None or parsed data
        
        This helper ensures we handle all cases consistently.
        
        Args:
            variant_data: VARIANT column value (string, dict, list, or None)
        
        Returns:
            Parsed Python object (dict or list), or None if empty
        """
        if not variant_data:
            return None
        
        # If it's a string, parse it
        if isinstance(variant_data, str):
            try:
                return json.loads(variant_data)
            except json.JSONDecodeError as e:
                logger.error(
                    "Failed to parse VARIANT JSON string",
                    extra={"variant_data": variant_data[:100], "error": str(e)}
                )
                return None
        
        # Already parsed (dict or list)
        return variant_data
    
    def _parse_observations(self, obs_json) -> list[TechniqueObservation]:
        """Parse observations from Snowflake VARIANT column."""
        # Parse VARIANT data (handles both string and dict/list)
        obs_data = self._parse_variant_json(obs_json)
        
        if not obs_data:
            return []
        
        # Validate it's a list
        if not isinstance(obs_data, list):
            logger.warning(
                "Observations data is not a list after parsing",
                extra={"type": type(obs_data)}
            )
            return []
        
        observations = []
        for obs in obs_data:
            # Defensive: handle nested string (shouldn't happen but be safe)
            if isinstance(obs, str):
                obs = self._parse_variant_json(obs)
                if not obs:
                    continue
            
            observations.append(TechniqueObservation(
                category=TechniqueCategory(obs.get("category", "body_position")),
                description=obs.get("description", ""),
            ))
        return observations
    
    def _parse_feedback(self, fb_json) -> list[CoachingFeedback]:
        """Parse feedback from Snowflake VARIANT column."""
        # Parse VARIANT data (handles both string and dict/list)
        fb_data = self._parse_variant_json(fb_json)
        
        if not fb_data:
            return []
        
        # Validate it's a list
        if not isinstance(fb_data, list):
            logger.warning(
                "Feedback data is not a list after parsing",
                extra={"type": type(fb_data)}
            )
            return []
        
        feedback = []
        for fb in fb_data:
            # Defensive: handle nested string (shouldn't happen but be safe)
            if isinstance(fb, str):
                fb = self._parse_variant_json(fb)
                if not fb:
                    continue
            
            obs_data = fb.get("observation", {})
            feedback.append(CoachingFeedback(
                id=UUID(fb["id"]) if fb.get("id") else None,
                priority=FeedbackPriority(fb.get("priority", "secondary")),
                observation=TechniqueObservation(
                    category=TechniqueCategory(obs_data.get("category", "body_position")),
                    description=obs_data.get("description", ""),
                ),
                recommendation=fb.get("recommendation", ""),
                drill_suggestions=fb.get("drill_suggestions", []),
            ))
        return feedback
