# Getting Started with SwimCoach AI API

This guide will help you get the FastAPI application running locally.

## Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

## Quick Start

### 1. Install Dependencies

```bash
# Install the package in development mode
pip install -e .

# Or install just the core dependencies
pip install -r requirements.txt  # if you create one
# Or directly:
pip install fastapi uvicorn anthropic pydantic-settings python-multipart
```

### 2. Configure Environment

```bash
# Copy the environment template
cp env.template .env

# Edit .env and set your Anthropic API key
# Minimum required:
# ANTHROPIC_API_KEY=sk-ant-api03-...
# SNOWFLAKE_MOCK_MODE=true
# R2_MOCK_MODE=true
```

### 3. Run the Application

```bash
# Start the development server with auto-reload
uvicorn src.main:app --reload

# Or run with the python module directly
python -m src.main
```

### 4. Test the API

Open your browser to:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## Testing the API Flow

### 1. Upload Frames

```bash
curl -X POST "http://localhost:8000/api/v1/analysis/upload" \
  -H "X-API-Key: dev-key-1" \
  -F "frames=@frame1.jpg" \
  -F "frames=@frame2.jpg" \
  -F "frames=@frame3.jpg" \
  -F "stroke_type=freestyle"
```

Response will include a `session_id`.

### 2. Analyze Frames

```bash
curl -X POST "http://localhost:8000/api/v1/analysis/{session_id}/analyze" \
  -H "X-API-Key: dev-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "stroke_type": "freestyle",
    "user_notes": "Focusing on improving my catch"
  }'
```

### 3. Chat with Coach

```bash
curl -X POST "http://localhost:8000/api/v1/sessions/{session_id}/chat" \
  -H "X-API-Key: dev-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Can you explain the catch drill in more detail?"
  }'
```

### 4. Get Session Details

```bash
curl -X GET "http://localhost:8000/api/v1/sessions/{session_id}" \
  -H "X-API-Key: dev-key-1"
```

## Mock Mode (Default)

By default, the application runs in mock mode for both Snowflake and R2:

- **Snowflake Mock Mode**: Sessions stored in memory (lost on restart)
- **R2 Mock Mode**: Frames stored in memory

This allows you to test the complete API flow without:
- Setting up a Snowflake database
- Creating an R2 bucket
- Paying for cloud services

## Production Mode

To use real services, edit `.env`:

```bash
# Disable mock modes
SNOWFLAKE_MOCK_MODE=false
R2_MOCK_MODE=false

# Provide real credentials
SNOWFLAKE_ACCOUNT=your-account.us-east-1
SNOWFLAKE_USER=swimcoach_api
SNOWFLAKE_PASSWORD=your-password
# ... etc

R2_ACCOUNT_ID=your-cloudflare-account-id
R2_ACCESS_KEY_ID=your-access-key
R2_SECRET_ACCESS_KEY=your-secret-key
```

## Project Structure

```
swimcoach/
├── src/
│   ├── api/                  # FastAPI routes and dependencies
│   │   ├── routes/
│   │   │   ├── analysis.py   # Frame upload & analysis endpoints
│   │   │   ├── sessions.py   # Chat & session management
│   │   │   └── health.py     # Health checks
│   │   └── dependencies.py   # Dependency injection
│   │
│   ├── core/                 # Business logic (framework-agnostic)
│   │   └── analysis/
│   │       ├── coach.py      # Coaching service
│   │       ├── frames.py     # Frame extraction strategies
│   │       └── models.py     # Domain models
│   │
│   ├── infrastructure/       # External service integrations
│   │   ├── anthropic/        # Claude API client
│   │   ├── snowflake/        # Database & repositories
│   │   └── storage/          # R2/S3 storage client
│   │
│   ├── config/
│   │   └── settings.py       # Application configuration
│   │
│   └── main.py               # FastAPI application
│
├── tests/                    # Test files
├── pyproject.toml           # Dependencies & project metadata
└── env.template             # Environment configuration template
```

## Common Issues

### Import Errors

If you see import errors, make sure you installed the package:

```bash
pip install -e .
```

This makes the `src` package importable.

### Missing Dependencies

Install all required packages:

```bash
pip install fastapi uvicorn anthropic pydantic-settings \
    snowflake-connector-python boto3 python-multipart python-dotenv
```

### API Key Authentication

All endpoints require an API key in the `X-API-Key` header.

Default keys for development: `dev-key-1`, `dev-key-2`, `dev-key-3`

## Next Steps

1. **Try the Swagger UI** at http://localhost:8000/docs
   - Interactive API documentation
   - Test endpoints directly in the browser
   - See request/response schemas

2. **Review the architecture** in README.md
   - Understand the design principles
   - See the component structure
   - Learn the patterns used

3. **Add tests** for your use cases
   - Unit tests for business logic
   - Integration tests for API endpoints
   - See `tests/unit/test_models.py` for examples

4. **Deploy to production**
   - Choose deployment platform (Fly.io, Railway, etc.)
   - Set up real Snowflake database
   - Configure R2 bucket
   - Use secure API keys

## Development Tips

- **Auto-reload**: Use `--reload` flag for development
- **Log level**: Set `LOG_LEVEL=DEBUG` for verbose logging
- **Mock modes**: Keep enabled during development to avoid costs
- **API docs**: Swagger UI at `/docs` is your friend

## Need Help?

- Check the logs for error messages
- Review the health check endpoint: `/health/ready`
- Read docstrings in the code (they explain the "why")
- Test with Swagger UI before writing client code

Happy coaching! 🏊‍♂️

