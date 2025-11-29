"""
Snowflake database connection management.

Provides connection factory and context manager for Snowflake operations.
Includes mock mode with in-memory storage for local development.

Using the repository pattern means most code never touches this module
directly - it goes through SessionRepository which handles the translation
between domain models and database rows.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Optional
from uuid import UUID

from .repositories.sessions import SnowflakeConfig, SnowflakeConnection

logger = logging.getLogger(__name__)


class SnowflakeConnectionError(Exception):
    """Raised when Snowflake connection fails."""
    pass


def _load_private_key(key_path: str):
    """
    Load private key from file for key-pair authentication.
    
    Snowflake requires the private key as a bytes object, not a file path.
    This function reads the key file and returns it in the format
    snowflake-connector expects.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    
    with open(key_path, 'rb') as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,  # No password on the key
            backend=default_backend()
        )
    
    # Convert to the format Snowflake expects
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    return private_key_bytes


@contextmanager
def get_snowflake_connection(config: SnowflakeConfig) -> Generator[SnowflakeConnection, None, None]:
    """
    Provide Snowflake connection with automatic cleanup.
    
    Supports both password and key-pair authentication:
    - If private_key_path is set, uses key-pair auth
    - Otherwise, uses password auth
    
    Using a context manager ensures connections are always closed,
    even if an exception occurs. This prevents connection leaks which
    can exhaust Snowflake's connection pool.
    
    Usage:
        with get_snowflake_connection(config) as conn:
            cursor = conn.cursor()
            # do work
            conn.commit()
    """
    try:
        import snowflake.connector
    except ImportError:
        raise ImportError(
            "snowflake-connector-python is required. "
            "Install with: pip install snowflake-connector-python"
        )
    
    conn = None
    try:
        # Build connection parameters
        connect_params = {
            'account': config.account,
            'user': config.user,
            'database': config.database,
            'schema': config.schema,
            'warehouse': config.warehouse,
            'role': config.role,
            'client_session_keep_alive': True,
        }
        
        # Use key-pair auth if private key path is provided
        if config.private_key_path:
            logger.info("Using key-pair authentication for Snowflake")
            connect_params['private_key'] = _load_private_key(config.private_key_path)
        elif config.password:
            logger.info("Using password authentication for Snowflake")
            connect_params['password'] = config.password
        else:
            raise SnowflakeConnectionError(
                "Either password or private_key_path must be provided"
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
    """
    Simple connection pool for Snowflake.
    
    In production, you'd want a proper connection pool to avoid
    the overhead of creating new connections for each request.
    
    This is a simplified implementation. For heavy production use,
    consider using a library like SQLAlchemy with Snowflake dialect.
    """
    
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
        """
        Get a connection from the pool.
        
        For now, this just creates a new connection each time.
        A real implementation would reuse connections.
        """
        with get_snowflake_connection(self._config) as conn:
            yield conn


# ---------------------------------------------------------------------------
# Mock Connection for Local Development
# ---------------------------------------------------------------------------

class MockSnowflakeCursor:
    """
    Mock Snowflake cursor for testing.
    
    Implements just enough of the cursor interface to support
    SessionRepository operations without a real database.
    
    This is a simple in-memory implementation that handles the
    basic MERGE and SELECT queries used by SessionRepository.
    """
    
    def __init__(self, storage: dict) -> None:
        self._storage = storage
        self._results: list = []
        self._rowcount: int = 0
    
    def execute(self, query: str, params: Optional[tuple] = None) -> 'MockSnowflakeCursor':
        """
        Execute a query against mock storage.
        
        Handles basic MERGE (upsert) and SELECT queries by pattern matching.
        This is simplified but sufficient for testing the API flow.
        """
        logger.debug(
            "Mock cursor execute",
            extra={"query": query[:100], "params": params}
        )
        
        query_upper = query.upper().strip()
        
        # Handle MERGE INTO (upsert) operations
        if 'MERGE INTO' in query_upper:
            self._handle_merge(query_upper, params)
        
        # Handle SELECT queries
        elif query_upper.startswith('SELECT'):
            self._handle_select(query_upper, params)
        
        # Handle INSERT queries
        elif 'INSERT INTO' in query_upper:
            self._handle_insert(query_upper, params)
        
        return self
    
    def _handle_merge(self, query: str, params: Optional[tuple]) -> None:
        """Handle MERGE INTO (upsert) queries."""
        if not params:
            return
        
        # Extract table name
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
        """Handle SELECT queries."""
        if not params:
            self._results = []
            return
        
        # Get session by ID (main query used by SessionRepository.get_session)
        if 'FROM COACHING_SESSIONS' in query and 'WHERE' in query:
            session_id = str(params[0])
            session = self._storage['coaching_sessions'].get(session_id)
            
            if session:
                # Build a row with all the fields the repository expects
                # This matches the structure in SessionRepository.get_session
                params_tuple = session.get('params', ())
                
                # Return row matching the SELECT structure
                self._results = [(
                    session_id,  # session_id
                    None,  # created_at
                    None,  # updated_at
                    'active',  # status
                    params_tuple[1] if len(params_tuple) > 1 else None,  # video_id
                    None,  # filename
                    None,  # storage_path
                    None,  # duration_seconds
                    None,  # resolution_width
                    None,  # resolution_height
                    None,  # fps
                    None,  # file_size_bytes
                    None,  # uploaded_at
                    None,  # video_stroke_type
                    params_tuple[2] if len(params_tuple) > 2 else None,  # analysis_id
                    None,  # analysis_stroke_type
                    None,  # summary
                    None,  # observations
                    None,  # feedback
                    None,  # frame_count_analyzed
                    None,  # analyzed_at
                )]
            else:
                self._results = []
        
        # Get messages for session
        elif 'FROM MESSAGES' in query:
            session_id = str(params[0]) if params else None
            self._results = []  # No messages by default
    
    def _handle_insert(self, query: str, params: Optional[tuple]) -> None:
        """Handle INSERT queries."""
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
    
    def fetchone(self):
        """Fetch one row from results."""
        if not self._results:
            return None
        return self._results[0]
    
    def fetchall(self) -> list:
        """Fetch all rows from results."""
        return self._results
    
    def close(self) -> None:
        """Close cursor (no-op for mock)."""
        pass
    
    @property
    def rowcount(self) -> int:
        """Return number of rows affected."""
        return self._rowcount


class MockSnowflakeConnection:
    """
    Mock Snowflake connection for local development.
    
    Stores data in memory using a simple dictionary structure.
    This enables testing the full API without a real database.
    
    Not suitable for production, but perfect for:
    - Local development
    - Unit tests
    - CI/CD environments
    """
    
    def __init__(self) -> None:
        # In-memory storage: {table_name: {id: row_dict}}
        self._storage: dict[str, dict[str, dict]] = {
            'coaching_sessions': {},
            'videos': {},
            'analyses': {},
            'messages': {},
        }
        self._committed = True
        
        logger.info("Initialized mock Snowflake connection (in-memory)")
    
    def cursor(self) -> MockSnowflakeCursor:
        """Create a mock cursor."""
        return MockSnowflakeCursor(self._storage)
    
    def commit(self) -> None:
        """Commit transaction (no-op for mock, always auto-commits)."""
        self._committed = True
        logger.debug("Mock connection commit")
    
    def rollback(self) -> None:
        """Rollback transaction (no-op for mock)."""
        logger.debug("Mock connection rollback")
    
    def close(self) -> None:
        """Close connection (no-op for mock)."""
        logger.debug("Mock connection close")
    
    # Helper methods for testing
    def _add_session(self, session_id: UUID, session_data: dict) -> None:
        """Add session to mock storage (for test setup)."""
        self._storage['coaching_sessions'][str(session_id)] = session_data
    
    def _get_session(self, session_id: UUID) -> Optional[dict]:
        """Get session from mock storage (for test assertions)."""
        return self._storage['coaching_sessions'].get(str(session_id))
    
    def _clear(self) -> None:
        """Clear all mock storage (for test cleanup)."""
        for table in self._storage.values():
            table.clear()


@contextmanager
def get_mock_snowflake_connection() -> Generator[MockSnowflakeConnection, None, None]:
    """
    Provide mock Snowflake connection for local development.
    
    Returns a connection that stores data in memory. Perfect for
    testing and local development without provisioning Snowflake.
    """
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
    """
    Create Snowflake connection based on configuration.
    
    Factory function that returns either a real or mock connection
    depending on mock_mode flag.
    
    Args:
        config: Snowflake configuration (required if not mock_mode)
        mock_mode: If True, return mock connection for testing
    
    Yields:
        SnowflakeConnection implementation (real or mock)
    """
    if mock_mode:
        with get_mock_snowflake_connection() as conn:
            yield conn
    else:
        if config is None:
            raise ValueError("config is required when not in mock mode")
        
        with get_snowflake_connection(config) as conn:
            yield conn