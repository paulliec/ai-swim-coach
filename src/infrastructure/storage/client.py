"""
Object storage client for video frames.

Supports Cloudflare R2 (S3-compatible) with mock mode for local development.
Using R2 instead of S3 because:
- No egress fees (important for video delivery)
- Tighter integration with Cloudflare Workers
- Same S3 API means we could swap to actual S3 if needed

Mock mode stores frames in memory, enabling API testing without
provisioning actual object storage.
"""

import io
import logging
from dataclasses import dataclass
from typing import Optional, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when storage operations fail."""
    pass


@dataclass
class StorageConfig:
    """
    Configuration for R2/S3-compatible storage.
    
    Using a dataclass instead of raw parameters means:
    - Configuration is explicit and documented
    - Easy to validate at construction time
    - Simple to create test configurations
    """
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    endpoint_url: str
    region: str = "auto"  # R2 uses 'auto' for region


class StorageClient(Protocol):
    """
    Protocol for object storage operations.
    
    Using a protocol means tests can provide mocks and we can
    swap storage backends without changing dependent code.
    """
    
    async def upload_frame(
        self,
        frame_data: bytes,
        session_id: UUID,
        frame_number: int,
    ) -> str:
        """Upload frame and return storage path."""
        ...
    
    async def download_frame(
        self,
        storage_path: str,
    ) -> bytes:
        """Download frame data by storage path."""
        ...
    
    async def get_presigned_url(
        self,
        storage_path: str,
        expiry_seconds: int = 3600,
    ) -> str:
        """Generate temporary download URL."""
        ...
    
    async def delete_frames(
        self,
        session_id: UUID,
    ) -> int:
        """Delete all frames for a session. Returns count deleted."""
        ...
    
    async def upload_video(
        self,
        video_data: bytes,
        session_id: UUID,
        filename: str,
    ) -> str:
        """Upload video file and return storage path."""
        ...
    
    async def download_video(
        self,
        storage_path: str,
    ) -> bytes:
        """Download video data by storage path."""
        ...


class R2StorageClient:
    """
    Cloudflare R2 object storage client.
    
    Uses boto3 because R2 is S3-compatible. This abstraction means
    we could swap to actual S3, MinIO, or other S3-compatible storage
    with minimal changes.
    
    All methods are async to match the Protocol even though boto3 is
    synchronous. This keeps the interface consistent with truly async
    storage clients and prevents blocking the event loop in the future.
    """
    
    def __init__(self, config: StorageConfig) -> None:
        """
        Initialize R2 client with boto3.
        
        We import boto3 here (not at module level) because:
        - Mock mode doesn't need it
        - Explicit about when the dependency is required
        - Makes testing easier
        """
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise ImportError(
                "boto3 is required for R2 storage. Install with: pip install boto3"
            )
        
        self._config = config
        
        # Configure boto3 for R2
        # R2 requires v4 signatures and has specific endpoint patterns
        boto_config = Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
        )
        
        self._s3_client = boto3.client(
            's3',
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            region_name=config.region,
            config=boto_config,
        )
        
        logger.info(
            "Initialized R2 storage client",
            extra={
                "bucket": config.bucket_name,
                "endpoint": config.endpoint_url,
            }
        )
    
    async def upload_frame(
        self,
        frame_data: bytes,
        session_id: UUID,
        frame_number: int,
    ) -> str:
        """
        Upload a frame to R2 storage.
        
        Path structure: frames/{session_id}/{frame_number:04d}.jpg
        Using session_id in path enables:
        - Easy cleanup of all frames for a session
        - Natural grouping in storage
        - Simple URL patterns for retrieval
        """
        storage_path = self._build_frame_path(session_id, frame_number)
        
        try:
            self._s3_client.put_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
                Body=frame_data,
                ContentType='image/jpeg',
                # Metadata for debugging and analytics
                Metadata={
                    'session-id': str(session_id),
                    'frame-number': str(frame_number),
                }
            )
            
            logger.debug(
                "Uploaded frame",
                extra={
                    "session_id": str(session_id),
                    "frame_number": frame_number,
                    "size_bytes": len(frame_data),
                }
            )
            
            return storage_path
            
        except Exception as e:
            logger.error(
                "Failed to upload frame",
                extra={
                    "session_id": str(session_id),
                    "frame_number": frame_number,
                    "error": str(e),
                }
            )
            raise StorageError(f"Upload failed: {e}")
    
    async def download_frame(self, storage_path: str) -> bytes:
        """Download frame data from R2."""
        try:
            response = self._s3_client.get_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
            )
            
            return response['Body'].read()
            
        except Exception as e:
            logger.error(
                "Failed to download frame",
                extra={"storage_path": storage_path, "error": str(e)}
            )
            raise StorageError(f"Download failed: {e}")
    
    async def get_presigned_url(
        self,
        storage_path: str,
        expiry_seconds: int = 3600,
    ) -> str:
        """
        Generate a temporary download URL.
        
        Presigned URLs enable:
        - Direct client downloads without routing through API
        - Time-limited access (security)
        - Reduced API server load
        
        Default 1-hour expiry is reasonable for viewing sessions.
        """
        try:
            url = self._s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self._config.bucket_name,
                    'Key': storage_path,
                },
                ExpiresIn=expiry_seconds,
            )
            
            return url
            
        except Exception as e:
            logger.error(
                "Failed to generate presigned URL",
                extra={"storage_path": storage_path, "error": str(e)}
            )
            raise StorageError(f"Presigned URL generation failed: {e}")
    
    async def delete_frames(self, session_id: UUID) -> int:
        """
        Delete all frames for a session.
        
        Used for cleanup after analysis or when user deletes a session.
        Returns count of deleted objects.
        """
        prefix = f"frames/{session_id}/"
        
        try:
            # List all objects with the session prefix
            response = self._s3_client.list_objects_v2(
                Bucket=self._config.bucket_name,
                Prefix=prefix,
            )
            
            if 'Contents' not in response:
                return 0
            
            # Delete in batch
            objects_to_delete = [
                {'Key': obj['Key']}
                for obj in response['Contents']
            ]
            
            if not objects_to_delete:
                return 0
            
            self._s3_client.delete_objects(
                Bucket=self._config.bucket_name,
                Delete={'Objects': objects_to_delete}
            )
            
            count = len(objects_to_delete)
            logger.info(
                "Deleted frames",
                extra={"session_id": str(session_id), "count": count}
            )
            
            return count
            
        except Exception as e:
            logger.error(
                "Failed to delete frames",
                extra={"session_id": str(session_id), "error": str(e)}
            )
            raise StorageError(f"Delete failed: {e}")
    
    def _build_frame_path(self, session_id: UUID, frame_number: int) -> str:
        """Build storage path for a frame."""
        return f"frames/{session_id}/{frame_number:04d}.jpg"
    
    async def upload_video(
        self,
        video_data: bytes,
        session_id: UUID,
        filename: str,
    ) -> str:
        """
        Upload a video file to R2 storage.
        
        Path structure: videos/{session_id}/{filename}
        Videos are stored separately from frames for easier management.
        """
        # get extension from filename, default to mp4
        ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'mp4'
        storage_path = f"videos/{session_id}/original.{ext}"
        
        # guess content type
        content_types = {
            'mp4': 'video/mp4',
            'mov': 'video/quicktime',
            'avi': 'video/x-msvideo',
            'webm': 'video/webm',
        }
        content_type = content_types.get(ext.lower(), 'video/mp4')
        
        try:
            self._s3_client.put_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
                Body=video_data,
                ContentType=content_type,
                Metadata={
                    'session-id': str(session_id),
                    'original-filename': filename,
                }
            )
            
            logger.info(
                "Uploaded video",
                extra={
                    "session_id": str(session_id),
                    "size_bytes": len(video_data),
                    "storage_path": storage_path,
                }
            )
            
            return storage_path
            
        except Exception as e:
            logger.error(
                "Failed to upload video",
                extra={"session_id": str(session_id), "error": str(e)}
            )
            raise StorageError(f"Video upload failed: {e}")
    
    async def download_video(self, storage_path: str) -> bytes:
        """Download video data from R2."""
        try:
            response = self._s3_client.get_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
            )
            
            return response['Body'].read()
            
        except Exception as e:
            logger.error(
                "Failed to download video",
                extra={"storage_path": storage_path, "error": str(e)}
            )
            raise StorageError(f"Video download failed: {e}")


# ---------------------------------------------------------------------------
# Mock Storage for Local Development
# ---------------------------------------------------------------------------

class MockStorageClient:
    """
    In-memory storage for local development.
    
    This mock enables testing the full API flow without provisioning
    real object storage. Frames and videos are stored in dictionaries
    and "URLs" are mock URIs.
    
    Not suitable for production, but perfect for development and testing.
    """
    
    def __init__(self) -> None:
        # store frames and videos in memory: {storage_path: bytes}
        self._frames: dict[str, bytes] = {}
        self._videos: dict[str, bytes] = {}
        logger.info("Initialized mock storage client (in-memory)")
    
    async def upload_frame(
        self,
        frame_data: bytes,
        session_id: UUID,
        frame_number: int,
    ) -> str:
        """Store frame in memory."""
        storage_path = f"frames/{session_id}/{frame_number:04d}.jpg"
        self._frames[storage_path] = frame_data
        
        logger.debug(
            "Stored frame in mock storage",
            extra={
                "session_id": str(session_id),
                "frame_number": frame_number,
                "size_bytes": len(frame_data),
            }
        )
        
        return storage_path
    
    async def download_frame(self, storage_path: str) -> bytes:
        """Retrieve frame from memory."""
        if storage_path not in self._frames:
            raise StorageError(f"Frame not found: {storage_path}")
        
        return self._frames[storage_path]
    
    async def get_presigned_url(
        self,
        storage_path: str,
        expiry_seconds: int = 3600,
    ) -> str:
        """
        Return a mock URL for the frame.
        
        In real usage, this would be a presigned URL. For mock mode,
        we return a placeholder. In a more sophisticated mock, we could
        return a data URI with the actual image data.
        """
        if storage_path not in self._frames:
            raise StorageError(f"Frame not found: {storage_path}")
        
        return f"mock://storage/{storage_path}"
    
    async def delete_frames(self, session_id: UUID) -> int:
        """Delete frames from memory."""
        prefix = f"frames/{session_id}/"
        
        keys_to_delete = [
            key for key in self._frames.keys()
            if key.startswith(prefix)
        ]
        
        for key in keys_to_delete:
            del self._frames[key]
        
        logger.debug(
            "Deleted frames from mock storage",
            extra={"session_id": str(session_id), "count": len(keys_to_delete)}
        )
        
        return len(keys_to_delete)
    
    async def upload_video(
        self,
        video_data: bytes,
        session_id: UUID,
        filename: str,
    ) -> str:
        """Store video in memory."""
        ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'mp4'
        storage_path = f"videos/{session_id}/original.{ext}"
        self._videos[storage_path] = video_data
        
        logger.debug(
            "Stored video in mock storage",
            extra={
                "session_id": str(session_id),
                "size_bytes": len(video_data),
                "storage_path": storage_path,
            }
        )
        
        return storage_path
    
    async def download_video(self, storage_path: str) -> bytes:
        """Retrieve video from memory."""
        if storage_path not in self._videos:
            raise StorageError(f"Video not found: {storage_path}")
        
        return self._videos[storage_path]


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------

def create_storage_client(
    config: Optional[StorageConfig] = None,
    mock_mode: bool = False,
) -> StorageClient:
    """
    Create storage client based on configuration.
    
    Factory function pattern because:
    - Centralizes client creation logic
    - Makes mock vs real decision explicit
    - Simplifies dependency injection in FastAPI
    
    Args:
        config: Storage configuration (required if not mock_mode)
        mock_mode: If True, return mock client for testing
    
    Returns:
        StorageClient implementation (R2 or Mock)
    """
    if mock_mode:
        return MockStorageClient()
    
    if config is None:
        raise ValueError("config is required when not in mock mode")
    
    return R2StorageClient(config)

