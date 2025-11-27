# SwimCoach AI

An AI-powered swim technique analysis platform that provides personalized coaching feedback from video uploads.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (React)                         │
│                     Vercel / Cloudflare Pages                   │
└─────────────────────┬───────────────────────────────────────────┘
                      │ HTTPS
┌─────────────────────▼───────────────────────────────────────────┐
│                      API Layer (FastAPI)                        │
│                   - Authentication                              │
│                   - Request validation                          │
│                   - Rate limiting                               │
└──────┬──────────────────┬──────────────────────┬────────────────┘
       │                  │                      │
┌──────▼──────┐   ┌───────▼───────┐    ┌────────▼────────┐
│   Video     │   │   Analysis    │    │   Data Layer    │
│  Processing │   │   Service     │    │   (Snowflake)   │
│  (ffmpeg)   │   │  (Claude API) │    │                 │
└─────────────┘   └───────────────┘    └─────────────────┘
```

## Design Principles

### 1. Separation of Concerns
Each module has a single responsibility. The API layer doesn't know about frame extraction algorithms. The analysis service doesn't know about storage. This isn't just cleanliness — it enables testing, replacement, and reasoning about failure modes.

### 2. Configuration as Code
No magic strings buried in functions. Environment-specific values come from config. Secrets come from environment variables. Defaults are explicit and documented.

### 3. Explicit Error Handling
We don't catch generic exceptions and hope for the best. Each layer defines its failure modes and communicates them clearly to the layer above.

### 4. Type Safety
Python type hints throughout, enforced by mypy. Not because Python requires it, but because it catches bugs before runtime and serves as documentation.

### 5. Testability by Design
- Pure functions where possible
- Dependency injection for external services
- Clear interfaces between components

### 6. Observability
Structured logging with correlation IDs. You should be able to trace a request from upload to coaching response.

## Project Structure

```
swimcoach/
├── src/
│   ├── api/                 # FastAPI routes and request/response models
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── analysis.py  # Video analysis endpoints
│   │   │   ├── sessions.py  # Coaching session management
│   │   │   └── health.py    # Health checks
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   └── logging.py
│   │   └── dependencies.py  # FastAPI dependency injection
│   │
│   ├── core/                # Business logic, no framework dependencies
│   │   ├── __init__.py
│   │   ├── analysis/
│   │   │   ├── __init__.py
│   │   │   ├── coach.py     # Coaching logic and prompt management
│   │   │   ├── frames.py    # Frame extraction strategies
│   │   │   └── models.py    # Domain models (Stroke, Technique, Feedback)
│   │   └── video/
│   │       ├── __init__.py
│   │       ├── processor.py # Video processing orchestration
│   │       └── validators.py
│   │
│   ├── infrastructure/      # External service integrations
│   │   ├── __init__.py
│   │   ├── anthropic/       # Claude API client
│   │   │   ├── __init__.py
│   │   │   └── client.py
│   │   ├── snowflake/       # Data persistence
│   │   │   ├── __init__.py
│   │   │   ├── client.py
│   │   │   └── repositories/
│   │   │       ├── __init__.py
│   │   │       ├── sessions.py
│   │   │       └── analyses.py
│   │   └── storage/         # Video file storage (S3/R2)
│   │       ├── __init__.py
│   │       └── client.py
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py      # Pydantic settings management
│   │
│   └── main.py              # Application entry point
│
├── tests/
│   ├── unit/                # Fast, isolated tests
│   ├── integration/         # Tests with real dependencies
│   └── conftest.py          # Shared fixtures
│
├── scripts/
│   └── setup_snowflake.sql  # Database schema
│
├── pyproject.toml           # Dependencies and tool config
├── Dockerfile
├── docker-compose.yml       # Local development
└── .env.example
```

## Why This Structure?

**src/ layout**: Prevents accidental imports from the project root. Forces explicit package structure.

**core/ has no external dependencies**: The business logic doesn't import FastAPI, doesn't import boto3, doesn't import snowflake-connector. This means:
- You can test it without mocking half the universe
- You can swap frameworks without rewriting logic
- You can reason about what the application *does* without wading through infrastructure

**infrastructure/ wraps external services**: Every external dependency gets a thin wrapper that exposes only what we need and translates between external formats and our domain models.

**Repositories pattern for data access**: The application asks for data by intent ("get session by ID") not by implementation ("execute this SQL"). The repository handles the translation.

## Local Development

```bash
# Clone and setup
cp .env.example .env
# Add your API keys to .env

# Run with Docker
docker-compose up

# Or run directly
pip install -e ".[dev]"
uvicorn src.main:app --reload
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| ANTHROPIC_API_KEY | Claude API access | Yes |
| SNOWFLAKE_ACCOUNT | Snowflake account identifier | Yes |
| SNOWFLAKE_USER | Service account username | Yes |
| SNOWFLAKE_PASSWORD | Service account password | Yes |
| SNOWFLAKE_DATABASE | Database name | Yes |
| STORAGE_BUCKET | S3/R2 bucket for videos | Yes |
| LOG_LEVEL | Logging verbosity | No (default: INFO) |

## API Endpoints

### POST /api/v1/analysis/upload
Upload video for analysis. Returns a session ID.

### POST /api/v1/analysis/{session_id}/analyze
Trigger analysis on uploaded video. Returns initial coaching feedback.

### POST /api/v1/sessions/{session_id}/chat
Continue coaching conversation. Send follow-up questions, get targeted advice.

### GET /api/v1/sessions/{session_id}
Retrieve session history and all feedback.

---

## Contributing

This is a portfolio project but structured for collaboration. PRs welcome.
