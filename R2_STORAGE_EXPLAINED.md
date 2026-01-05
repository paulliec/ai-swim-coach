# Cloudflare R2 Storage - Deep Dive

## What is R2?

Cloudflare R2 is **object storage** - their competitor to Amazon S3. "Object storage" means you store files (blobs) with keys (paths) rather than in a traditional filesystem or database.

```
Key:   frames/abc123-session-id/0001.jpg
Value: [binary image data]
```

---

## Why R2 vs S3 vs Others?

| Feature | R2 | S3 | GCS |
|---------|----|----|-----|
| Egress fees | **$0 (free!)** | $0.09/GB | $0.12/GB |
| S3-compatible API | Yes | Yes | No |
| Free tier | 10GB storage | 5GB | 5GB |
| CDN integration | Built-in | Extra cost | Extra cost |

**The big deal:** R2 has **zero egress fees**. If users download frames or you serve images publicly, you don't pay per GB transferred. S3's egress fees add up fast at scale.

---

## How R2 Works in SwimCoach

### The Flow

```
User uploads frames
        ↓
FastAPI receives multipart/form-data
        ↓
For each frame:
    storage.upload_frame(data, session_id, frame_number)
        ↓
    R2 stores at: frames/{session_id}/0001.jpg
        ↓
Later, for analysis:
    storage.download_frame(path) → bytes
        ↓
    Send bytes to Claude Vision API
```

### Storage Path Pattern

```
frames/{session_id}/{frame_number:04d}.jpg

Examples:
frames/a1b2c3d4-uuid/0001.jpg
frames/a1b2c3d4-uuid/0002.jpg
frames/a1b2c3d4-uuid/0015.jpg
```

This structure enables:
- Easy cleanup: delete everything with prefix `frames/{session_id}/`
- Natural grouping by session
- Simple URL patterns

---

## The Code

### R2StorageClient (production)

Uses boto3 (AWS SDK) because R2 is S3-compatible:

```python
# Initialize
self._s3_client = boto3.client(
    's3',
    endpoint_url="https://{account_id}.r2.cloudflarestorage.com",
    aws_access_key_id=config.access_key_id,
    aws_secret_access_key=config.secret_access_key,
    region_name="auto",  # R2 uses 'auto'
)

# Upload
self._s3_client.put_object(
    Bucket=bucket_name,
    Key=f"frames/{session_id}/0001.jpg",
    Body=frame_bytes,
    ContentType='image/jpeg',
)

# Download
response = self._s3_client.get_object(Bucket=bucket_name, Key=path)
data = response['Body'].read()

# Delete (batch)
self._s3_client.delete_objects(
    Bucket=bucket_name,
    Delete={'Objects': [{'Key': path1}, {'Key': path2}]}
)
```

### MockStorageClient (local dev)

Stores frames in a Python dictionary - no R2 credentials needed:

```python
class MockStorageClient:
    def __init__(self):
        self._frames: dict[str, bytes] = {}
    
    async def upload_frame(self, frame_data, session_id, frame_number):
        path = f"frames/{session_id}/{frame_number:04d}.jpg"
        self._frames[path] = frame_data
        return path
    
    async def download_frame(self, path):
        return self._frames[path]
```

Enable with `R2_MOCK_MODE=true` in `.env`.

---

## Key Features

### 1. Presigned URLs

Generate temporary download links without exposing credentials:

```python
url = self._s3_client.generate_presigned_url(
    'get_object',
    Params={'Bucket': bucket, 'Key': path},
    ExpiresIn=3600,  # 1 hour
)
# Returns: https://bucket.r2.cloudflarestorage.com/frames/...?signature=...
```

**Use case:** Let users download frames directly without routing through your API.

**Not using yet:** Currently frames go API → Claude, not exposed to users.

### 2. Metadata

Attach custom metadata to objects:

```python
self._s3_client.put_object(
    ...,
    Metadata={
        'session-id': str(session_id),
        'frame-number': str(frame_number),
    }
)
```

Useful for debugging and analytics.

### 3. Batch Delete

Clean up all frames for a session efficiently:

```python
async def delete_frames(self, session_id):
    # List all objects with prefix
    response = self._s3_client.list_objects_v2(
        Bucket=bucket,
        Prefix=f"frames/{session_id}/"
    )
    
    # Batch delete
    objects = [{'Key': obj['Key']} for obj in response['Contents']]
    self._s3_client.delete_objects(Bucket=bucket, Delete={'Objects': objects})
```

---

## Configuration

### Environment Variables

```bash
# .env
R2_ACCOUNT_ID=your-cloudflare-account-id
R2_ACCESS_KEY_ID=your-r2-access-key
R2_SECRET_ACCESS_KEY=your-r2-secret-key
R2_BUCKET_NAME=swimcoach-frames
R2_ENDPOINT=https://{account_id}.r2.cloudflarestorage.com

# For local dev without R2:
R2_MOCK_MODE=true
```

### Getting R2 Credentials

1. Cloudflare Dashboard → R2
2. Create bucket (e.g., `swimcoach-frames`)
3. Manage R2 API Tokens → Create API Token
4. Copy Access Key ID and Secret Access Key

---

## Interview Q&A

**Q: "How does R2 compare to S3?"**
> "API is nearly identical - boto3 works with both. Main difference is R2 has zero egress fees and tighter Cloudflare integration. I chose R2 for the free tier and because my frontend is on Cloudflare Pages."

**Q: "What's stored in R2 vs Snowflake?"**
> "R2 has the actual image bytes - raw frame data. Snowflake has session metadata, analysis results, conversation history. They reference each other by session_id."

**Q: "What happens if R2 is down?"**
> "Upload fails, user gets an error. For more resilience I could add retry logic or queue failed uploads. Currently it's synchronous and fails fast."

**Q: "How do you clean up old frames?"**
> "I have `delete_frames(session_id)` that does a prefix scan and batch delete. Not hooked up to automatic cleanup yet - that would be a background job or lifecycle policy."

**Q: "Why not store frames in the database?"**
> "Databases aren't optimized for blob storage. R2 is designed for this - cheap, fast, scalable. Plus Snowflake charges for storage, R2's free tier is generous."

**Q: "How would you serve frames to the frontend?"**
> "Currently I don't - frames go to Claude for analysis. If I wanted to display them, I'd use presigned URLs so the browser fetches directly from R2 without going through my API."

---

## Cost Breakdown

### R2 Free Tier
- 10 GB storage/month
- 1 million Class A operations (writes)
- 10 million Class B operations (reads)
- **Unlimited egress**

### Your Usage (estimate)
- 15 frames × 100KB = 1.5MB per session
- 100 sessions/month = 150MB storage
- Well within free tier

### At Scale
- 10,000 sessions/month = 15GB storage = ~$0.23/month
- Still very cheap because egress is free

---

## Protocol Pattern

The code uses Python's Protocol for abstraction:

```python
class StorageClient(Protocol):
    async def upload_frame(self, ...) -> str: ...
    async def download_frame(self, ...) -> bytes: ...
    async def delete_frames(self, ...) -> int: ...
```

Both `R2StorageClient` and `MockStorageClient` implement this protocol. The rest of the code depends on `StorageClient`, not the concrete implementations.

**Why this matters:**
- Swap R2 for S3 without changing business logic
- Test without real storage
- Clear contract for what storage must do

---

## Files to Review

- `src/infrastructure/storage/client.py` - Full implementation
- `src/api/dependencies.py` - How storage client is injected
- `src/api/routes/analysis.py` - Where upload/download happens
- `.env` / `env.template` - Configuration

