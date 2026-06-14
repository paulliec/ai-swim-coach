"""
Snowflake connection management.

Mock mode with in-memory storage for local dev.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Optional
from uuid import UUID

from fastapi import HTTPException

from .repositories.sessions import SnowflakeConfig, SnowflakeConnection

logger = logging.getLogger(__name__)


class SnowflakeConnectionError(Exception):
    """Raised when Snowflake connection fails."""
    pass


def _load_private_key(key_path: Optional[str] = None, key_base64: Optional[str] = None):
    """Load PEM key from file or base64 env var, return DER/PKCS8 bytes for Snowflake."""
    import base64
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    
    if key_base64:
        logger.info("Loading private key from base64-encoded environment variable")
        key_bytes = base64.b64decode(key_base64)
    elif key_path:
        logger.info(f"Loading private key from file: {key_path}")
        with open(key_path, 'rb') as key_file:
            key_bytes = key_file.read()
    else:
        raise ValueError("Either key_path or key_base64 must be provided for key-pair authentication")
    
    private_key = serialization.load_pem_private_key(
        key_bytes,
        password=None,
        backend=default_backend()
    )

    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    return private_key_bytes


@contextmanager
def get_snowflake_connection(config: SnowflakeConfig) -> Generator[SnowflakeConnection, None, None]:
    """Context-managed Snowflake connection. Auth priority: key_base64 > key_path > password."""
    try:
        import snowflake.connector
    except ImportError:
        raise ImportError(
            "snowflake-connector-python is required. "
            "Install with: pip install snowflake-connector-python"
        )
    
    conn = None
    try:
        connect_params = {
            'account': config.account,
            'user': config.user,
            'database': config.database,
            'schema': config.schema,
            'warehouse': config.warehouse,
            'role': config.role,
            'client_session_keep_alive': True,
        }
        
        if config.private_key_base64:
            connect_params['private_key'] = _load_private_key(key_base64=config.private_key_base64)
        elif config.private_key_path:
            connect_params['private_key'] = _load_private_key(key_path=config.private_key_path)
        elif config.password:
            connect_params['password'] = config.password
        else:
            raise SnowflakeConnectionError(
                "Either password, private_key_path, or private_key_base64 must be provided"
            )
        
        conn = snowflake.connector.connect(**connect_params)
        
        logger.debug(
            "Established Snowflake connection",
            extra={
                "account": config.account,
                "database": config.database,
                "schema": config.schema,
            }
        )
        
        yield conn
        
    except HTTPException:
        raise
    
    except snowflake.connector.errors.DatabaseError as e:
        logger.error(
            "Snowflake connection failed",
            extra={"error": str(e), "account": config.account}
        )
        raise SnowflakeConnectionError(f"Database connection failed: {e}")
    
    except Exception as e:
        logger.error(
            "Unexpected error connecting to Snowflake",
            extra={"error": str(e)}
        )
        raise SnowflakeConnectionError(f"Connection error: {e}")
    
    finally:
        if conn:
            try:
                conn.close()
                logger.debug("Closed Snowflake connection")
            except Exception as e:
                logger.warning(
                    "Error closing Snowflake connection",
                    extra={"error": str(e)}
                )


# ---------------------------------------------------------------------------
# Connection Pool (for production use)
# ---------------------------------------------------------------------------

class SnowflakeConnectionPool:
    """# TODO: fix later - naive pool, creates new connection each time."""
    
    def __init__(self, config: SnowflakeConfig, pool_size: int = 5):
        self._config = config
        self._pool_size = pool_size
        self._connections: list[SnowflakeConnection] = []
        
        logger.info(
            "Initialized Snowflake connection pool",
            extra={"pool_size": pool_size}
        )
    
    @contextmanager
    def get_connection(self) -> Generator[SnowflakeConnection, None, None]:
        with get_snowflake_connection(self._config) as conn:
            yield conn


# ---------------------------------------------------------------------------
# Mock Connection for Local Development
# ---------------------------------------------------------------------------

class MockSnowflakeCursor:
    """In-memory cursor — handles MERGE/SELECT/INSERT/UPDATE/DELETE by pattern matching."""
    
    def __init__(self, storage: dict) -> None:
        self._storage = storage
        self._results: list = []
        self._rowcount: int = 0
    
    def execute(self, query: str, params: Optional[tuple] = None) -> 'MockSnowflakeCursor':
        logger.debug(
            "Mock cursor execute",
            extra={"query": query[:100], "params": params}
        )
        
        query_upper = query.upper().strip()
        
        if 'MERGE INTO' in query_upper:
            self._handle_merge(query_upper, params)
        
        elif query_upper.startswith('SELECT'):
            self._handle_select(query_upper, params)
        
        elif 'INSERT INTO' in query_upper:
            self._handle_insert(query_upper, params)
        
        elif query_upper.startswith('UPDATE'):
            self._handle_update(query_upper, params)
        
        elif query_upper.startswith('DELETE'):
            self._handle_delete(query_upper, params)
        
        return self
    
    def _handle_merge(self, query: str, params: Optional[tuple]) -> None:
        if not params:
            return

        if 'COACHING_SESSIONS' in query:
            table = 'coaching_sessions'
            session_id = str(params[0])  # First param is session_id
            self._storage[table][session_id] = {
                'session_id': session_id,
                'params': params,
            }
            self._rowcount = 1
        
        elif 'VIDEOS' in query:
            table = 'videos'
            video_id = str(params[0])
            self._storage[table][video_id] = {
                'video_id': video_id,
                'params': params,
            }
            self._rowcount = 1
        
        elif 'ANALYSES' in query:
            table = 'analyses'
            analysis_id = str(params[0])
            self._storage[table][analysis_id] = {
                'analysis_id': analysis_id,
                'params': params,
            }
            self._rowcount = 1
        
        elif 'MESSAGES' in query:
            table = 'messages'
            message_id = str(params[0])
            self._storage[table][message_id] = {
                'message_id': message_id,
                'params': params,
            }
            self._rowcount = 1
    
    def _handle_select(self, query: str, params: Optional[tuple]) -> None:
        if not params:
            self._results = []
            return
        
        if 'FROM COACHING_SESSIONS' in query and 'WHERE' in query:
            session_id = str(params[0])
            session = self._storage['coaching_sessions'].get(session_id)

            if session:
                # Mirror _upsert_session param order: 1=video_id, 2=analysis_id,
                # 3=status, 4=error_message, 11=created_at, 12=updated_at.
                p = session.get('params', ())
                video_id = p[1] if len(p) > 1 else None
                analysis_id = p[2] if len(p) > 2 else None
                status = p[3] if len(p) > 3 else 'active'
                error_message = p[4] if len(p) > 4 else None
                created_at = p[11] if len(p) > 11 else None
                updated_at = p[12] if len(p) > 12 else None

                # Join the analysis row (if any) — _upsert_analysis param order:
                # 1=summary, 2=observations, 3=feedback, 4=frame_count,
                # 7=stroke_type, 12=analyzed_at.
                a_stroke = a_summary = a_obs = a_fb = a_analyzed = None
                a_frames = 0
                if analysis_id:
                    analysis = self._storage['analyses'].get(str(analysis_id))
                    if analysis:
                        ap = analysis.get('params', ())
                        a_summary = ap[1] if len(ap) > 1 else None
                        a_obs = ap[2] if len(ap) > 2 else None
                        a_fb = ap[3] if len(ap) > 3 else None
                        a_frames = ap[4] if len(ap) > 4 else 0
                        a_stroke = ap[7] if len(ap) > 7 else None
                        a_analyzed = ap[12] if len(ap) > 12 else None

                self._results = [(
                    session_id, created_at, updated_at, status, video_id,
                    None, None, None, None, None, None, None, None, None,
                    analysis_id, a_stroke, a_summary, a_obs, a_fb, a_frames,
                    a_analyzed, error_message,
                )]
            else:
                self._results = []
        
        elif 'FROM MESSAGES' in query:
            session_id = str(params[0]) if params else None
            self._results = []  # No messages by default
        
        elif 'FROM USAGE_LIMITS' in query:
            if len(params) >= 3:
                identifier = str(params[0])
                identifier_type = str(params[1])
                resource_type = str(params[2])
                
                for record in self._storage['usage_limits'].values():
                    record_params = record['params']
                    if (str(record_params[1]) == identifier and
                        str(record_params[2]) == identifier_type and
                        str(record_params[3]) == resource_type):
                        if len(params) == 5:
                            if (record_params[6] == params[3] and
                                record_params[7] == params[4]):
                                self._results = [(
                                    record_params[0],
                                    record_params[4],
                                    record_params[5],
                                )]
                                return
                        else:
                            if len(params) == 4:
                                self._results = [(
                                    record_params[4],
                                    record_params[5],
                                    record_params[7],
                                )]
                                return
                
                self._results = []
            else:
                self._results = []
    
    def _handle_insert(self, query: str, params: Optional[tuple]) -> None:
        if not params:
            return
        
        if 'MESSAGES' in query:
            table = 'messages'
            message_id = str(params[0])
            self._storage[table][message_id] = {
                'message_id': message_id,
                'params': params,
            }
            self._rowcount = 1
        
        elif 'USAGE_LIMITS' in query:
            table = 'usage_limits'
            limit_id = str(params[0])
            self._storage[table][limit_id] = {
                'limit_id': limit_id,
                'params': params,
            }
            self._rowcount = 1
    
    def _handle_update(self, query: str, params: Optional[tuple]) -> None:
        if not params:
            return

        if 'USAGE_LIMITS' in query:
            new_count = params[0]
            limit_id = str(params[1])
            
            if limit_id in self._storage['usage_limits']:
                record = self._storage['usage_limits'][limit_id]
                old_params = record['params']
                new_params = (
                    old_params[0], old_params[1], old_params[2], old_params[3],
                    new_count, old_params[5], old_params[6], old_params[7],
                )
                record['params'] = new_params
                self._rowcount = 1
            else:
                self._rowcount = 0
    
    def _handle_delete(self, query: str, params: Optional[tuple]) -> None:
        if not params:
            return

        if 'USAGE_LIMITS' in query:
            identifier = str(params[0])
            identifier_type = str(params[1])
            resource_type = str(params[2])
            
            deleted_count = 0
            to_delete = []
            
            for limit_id, record in self._storage['usage_limits'].items():
                record_params = record['params']
                if (str(record_params[1]) == identifier and
                    str(record_params[2]) == identifier_type and
                    str(record_params[3]) == resource_type):
                    to_delete.append(limit_id)
            
            for limit_id in to_delete:
                del self._storage['usage_limits'][limit_id]
                deleted_count += 1
            
            self._rowcount = deleted_count
    
    def fetchone(self):
        if not self._results:
            return None
        return self._results[0]
    
    def fetchall(self) -> list:
        return self._results
    
    def close(self) -> None:
        pass

    @property
    def rowcount(self) -> int:
        return self._rowcount


class MockSnowflakeConnection:
    """In-memory Snowflake mock for local dev and testing."""

    def __init__(self) -> None:
        self._storage: dict[str, dict[str, dict]] = {
            'coaching_sessions': {},
            'videos': {},
            'analyses': {},
            'messages': {},
            'usage_limits': {},
        }
        self._committed = True
        
        logger.info("Initialized mock Snowflake connection (in-memory)")
    
    def cursor(self) -> MockSnowflakeCursor:
        return MockSnowflakeCursor(self._storage)
    
    def commit(self) -> None:
        self._committed = True
        logger.debug("Mock connection commit")
    
    def rollback(self) -> None:
        logger.debug("Mock connection rollback")
    
    def close(self) -> None:
        logger.debug("Mock connection close")

    def _add_session(self, session_id: UUID, session_data: dict) -> None:
        self._storage['coaching_sessions'][str(session_id)] = session_data
    
    def _get_session(self, session_id: UUID) -> Optional[dict]:
        return self._storage['coaching_sessions'].get(str(session_id))
    
    def _clear(self) -> None:
        for table in self._storage.values():
            table.clear()


@contextmanager
def get_mock_snowflake_connection() -> Generator[MockSnowflakeConnection, None, None]:
    conn = MockSnowflakeConnection()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------

@contextmanager
def create_snowflake_connection(
    config: Optional[SnowflakeConfig] = None,
    mock_mode: bool = False,
) -> Generator[SnowflakeConnection, None, None]:
    """Factory: returns real or mock Snowflake connection."""
    if mock_mode:
        with get_mock_snowflake_connection() as conn:
            yield conn
    else:
        if config is None:
            raise ValueError("config is required when not in mock mode")
        
        with get_snowflake_connection(config) as conn:
            yield conn