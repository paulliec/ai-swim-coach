"""
Object storage client for video frames.

R2 over S3 — cheaper egress for video. Mock mode for local dev.
"""

import io
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when storage operations fail."""
    pass


@dataclass
class StorageConfig:
    """R2/S3-compatible storage configuration."""
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    endpoint_url: str
    region: str = "auto"  # R2 uses 'auto' for region


class StorageClient(Protocol):
    """Protocol for object storage operations."""
    
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
    
    async def save_analysis_state(
        self,
        session_id: UUID,
        state: dict[str, Any],
    ) -> str:
        """Save agentic analysis state for resume capability."""
        ...
    
    async def load_analysis_state(
        self,
        session_id: UUID,
    ) -> Optional[dict[str, Any]]:
        """Load saved analysis state. Returns None if no state exists."""
        ...
    
    async def delete_analysis_state(
        self,
        session_id: UUID,
    ) -> bool:
        """Delete analysis state after completion. Returns True if deleted."""
        ...


class R2StorageClient:
    """Cloudflare R2 client via boto3. Async interface wrapping sync S3 calls."""

    def __init__(self, config: StorageConfig) -> None:
        # Import here — mock mode doesn't need boto3
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise ImportError(
                "boto3 is required for R2 storage. Install with: pip install boto3"
            )
        
        self._config = config

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
        """Upload frame to R2. Path: frames/{session_id}/{frame:04d}.jpg"""
        storage_path = self._build_frame_path(session_id, frame_number)
        
        try:
            self._s3_client.put_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
                Body=frame_data,
                ContentType='image/jpeg',
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
        """Generate temporary download URL (default 1hr expiry)."""
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
        """Delete all frames for a session. Returns count deleted."""
        prefix = f"frames/{session_id}/"

        try:
            response = self._s3_client.list_objects_v2(
                Bucket=self._config.bucket_name,
                Prefix=prefix,
            )
            
            if 'Contents' not in response:
                return 0
            
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
        """Upload video to R2. Path: videos/{session_id}/original.{ext}"""
        ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'mp4'
        storage_path = f"videos/{session_id}/original.{ext}"

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
    
    async def save_analysis_state(
        self,
        session_id: UUID,
        state: dict[str, Any],
    ) -> str:
        """Save analysis state as JSON — enables resume on interruption."""
        storage_path = f"videos/{session_id}/analysis_state.json"
        
        try:
            state_json = json.dumps(state, default=str)
            
            self._s3_client.put_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
                Body=state_json.encode('utf-8'),
                ContentType='application/json',
                Metadata={'session-id': str(session_id)}
            )
            
            logger.info(
                "Saved analysis state",
                extra={"session_id": str(session_id), "storage_path": storage_path}
            )
            
            return storage_path
            
        except Exception as e:
            logger.error(
                "Failed to save analysis state",
                extra={"session_id": str(session_id), "error": str(e)}
            )
            raise StorageError(f"State save failed: {e}")
    
    async def load_analysis_state(
        self,
        session_id: UUID,
    ) -> Optional[dict[str, Any]]:
        """Load saved analysis state. Returns None if no state exists."""
        storage_path = f"videos/{session_id}/analysis_state.json"
        
        try:
            response = self._s3_client.get_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
            )
            
            state_json = response['Body'].read().decode('utf-8')
            state = json.loads(state_json)
            
            logger.info(
                "Loaded analysis state",
                extra={"session_id": str(session_id), "iteration": state.get('iteration', 0)}
            )
            
            return state
            
        except self._s3_client.exceptions.NoSuchKey:
            return None
        except Exception as e:
            if 'NoSuchKey' in str(e) or '404' in str(e):
                return None
            logger.error(
                "Failed to load analysis state",
                extra={"session_id": str(session_id), "error": str(e)}
            )
            return None
    
    async def delete_analysis_state(
        self,
        session_id: UUID,
    ) -> bool:
        """Delete analysis state after completion."""
        storage_path = f"videos/{session_id}/analysis_state.json"
        
        try:
            self._s3_client.delete_object(
                Bucket=self._config.bucket_name,
                Key=storage_path,
            )
            
            logger.info("Deleted analysis state", extra={"session_id": str(session_id)})
            return True
            
        except Exception as e:
            logger.warning(
                "Failed to delete analysis state (may not exist)",
                extra={"session_id": str(session_id), "error": str(e)}
            )
            return False


# ---------------------------------------------------------------------------
# Mock Storage for Local Development
# ---------------------------------------------------------------------------

class MockStorageClient:
    """In-memory storage for local dev and testing."""

    def __init__(self) -> None:
        self._frames: dict[str, bytes] = {}
        self._videos: dict[str, bytes] = {}
        self._states: dict[str, dict[str, Any]] = {}  # {session_id: state}
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
        """Return mock:// URL placeholder."""
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
    
    async def save_analysis_state(
        self,
        session_id: UUID,
        state: dict[str, Any],
    ) -> str:
        """Save analysis state in memory."""
        self._states[str(session_id)] = state
        logger.debug(f"Saved analysis state for session {session_id}")
        return f"videos/{session_id}/analysis_state.json"
    
    async def load_analysis_state(
        self,
        session_id: UUID,
    ) -> Optional[dict[str, Any]]:
        """Load analysis state from memory."""
        return self._states.get(str(session_id))
    
    async def delete_analysis_state(
        self,
        session_id: UUID,
    ) -> bool:
        """Delete analysis state from memory."""
        if str(session_id) in self._states:
            del self._states[str(session_id)]
            return True
        return False


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------

def create_storage_client(
    config: Optional[StorageConfig] = None,
    mock_mode: bool = False,
) -> StorageClient:
    """Factory: returns R2 or mock client."""
    if mock_mode:
        return MockStorageClient()
    
    if config is None:
        raise ValueError("config is required when not in mock mode")
    
    return R2StorageClient(config)

