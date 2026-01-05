# Conversation Summary - SwimCoach AI Project

## Project Overview

**SwimCoach AI** is an AI-powered swim technique analysis platform that provides personalized coaching feedback from video uploads. The system uses:
- **Backend**: FastAPI (Python) deployed on Fly.io
- **Frontend**: React (Vite) deployed on Vercel/Cloudflare Pages
- **AI**: Claude API (Anthropic) for coaching analysis
- **Database**: Snowflake for session storage
- **Storage**: Cloudflare R2 for video frame storage
- **Auth**: Clerk for user authentication

## Current State

### ✅ Completed Features

**Backend (FastAPI)**
- Core API structure with clean architecture (api/core/infrastructure layers)
- Health check endpoints (`/health`, `/health/ready`)
- Analysis endpoints:
  - `POST /api/v1/analysis/upload` - Upload frames, create session
  - `POST /api/v1/analysis/{session_id}/analyze` - Trigger AI analysis
- Session management endpoints:
  - `POST /api/v1/sessions/{session_id}/chat` - Continue coaching conversation
  - `GET /api/v1/sessions/{session_id}` - Retrieve session details
  - `POST /api/v1/sessions/{session_id}/claim` - Claim anonymous session for authenticated user
  - `DELETE /api/v1/sessions/{session_id}` - Delete session (placeholder, not fully implemented)
- Mock modes for local development (Snowflake and R2)
- API key authentication via `X-API-Key` header
- User ID tracking via `X-User-Id` header (supports anonymous users)
- Rate limiting (usage limits per user)
- Dependency injection pattern
- Type safety throughout

**Frontend (React)**
- Video upload with client-side frame extraction (~15 frames on desktop, ~10 on mobile)
- Frame preview thumbnails
- Stroke type selection (freestyle, backstroke, breaststroke, butterfly)
- AI analysis display with summary and detailed feedback
- Interactive chat interface for follow-up questions
- Session history view (for authenticated users)
- Clerk authentication integration
- Anonymous user support with session claiming
- API key management (localStorage)
- Mobile-responsive design

**Infrastructure**
- Snowflake client with mock mode
- R2 storage client with mock mode
- Anthropic Claude API client
- Configuration management (Pydantic settings)
- Error handling and structured logging

**Documentation**
- README.md - Architecture and setup guide
- GETTING_STARTED.md - Quick start for developers
- IMPLEMENTATION_SUMMARY.md - What's been built
- DEPLOYMENT.md - Fly.io deployment guide
- frontend/README.md - Frontend setup guide

### 🚧 Incomplete/Placeholder Features

1. **Session Deletion** - DELETE endpoint exists but is a placeholder (needs implementation)
2. **Video Processing** - Architecture mentions ffmpeg but not implemented (currently using client-side frame extraction)
3. **Rate Limiting** - Basic usage limits exist, but advanced rate limiting mentioned in roadmap
4. **Monitoring** - Metrics, traces, alerts not yet implemented
5. **Tests** - Only basic unit tests exist (`tests/unit/test_models.py`)

## Architecture Decisions

### Design Principles
1. **Separation of Concerns** - Each module has single responsibility
2. **Configuration as Code** - No magic strings, environment-based config
3. **Explicit Error Handling** - Each layer defines failure modes
4. **Type Safety** - Python type hints throughout
5. **Testability by Design** - Pure functions, dependency injection, clear interfaces
6. **Observability** - Structured logging with correlation IDs

### Code Patterns
- Type hints everywhere
- Docstrings explain "why" not just "what"
- Thin infrastructure layer (API routes orchestrate, don't implement logic)
- Protocol-based dependencies (core logic depends on protocols, not concrete implementations)
- Dependency injection (no global state)
- Factory functions for creating clients/services
- Context managers for resource cleanup

### Project Structure
```
swimcoach/
├── src/
│   ├── api/              # FastAPI routes and dependencies
│   ├── core/             # Business logic (framework-agnostic)
│   ├── infrastructure/   # External service integrations
│   ├── config/           # Configuration management
│   └── main.py           # Application entry point
├── frontend/             # React application
├── tests/                # Test files
├── scripts/              # Setup scripts (Snowflake schema)
└── docs/                 # Documentation files
```

## Key Technical Details

### Authentication Flow
- **API Keys**: Simple `X-API-Key` header authentication (dev keys: `dev-key-1`, `dev-key-2`, `dev-key-3`)
- **User Tracking**: `X-User-Id` header supports both authenticated (Clerk user ID) and anonymous users
- **Session Claiming**: Anonymous users can create sessions, then claim them after signing in

### Rate Limiting
- Usage limits per user (daily limits)
- Configurable via environment variables
- Bypass keys for admin/trusted partners

### Mock Modes
- `SNOWFLAKE_MOCK_MODE=true` - In-memory database (lost on restart)
- `R2_MOCK_MODE=true` - In-memory storage
- Enables full local development without cloud services

### Environment Variables

**Backend:**
- `ANTHROPIC_API_KEY` - Required
- `SNOWFLAKE_*` - Required unless mock mode
- `R2_*` - Required unless mock mode
- `API_KEYS` - Comma-separated list of valid API keys
- `SNOWFLAKE_MOCK_MODE` / `R2_MOCK_MODE` - Enable mock modes

**Frontend:**
- `VITE_API_BASE` - Backend API URL
- `VITE_CLERK_PUBLISHABLE_KEY` - Clerk authentication key
- `VITE_API_KEY` - Optional default API key

## Roadmap / Next Steps

### Immediate Tasks
1. ✅ Verify imports - Check all imports resolve correctly
2. ✅ Fix linting errors - Run linter and fix issues
3. ✅ Test API flow - Upload → Analyze → Chat → Retrieve
4. ⏳ Add real credentials - When ready, disable mock modes

### Development Tasks
5. ⏳ **Implement session deletion** - Complete the DELETE endpoint
6. ⏳ **Add rate limiting** - Enhanced protection against abuse
7. ⏳ **Add more tests** - Unit and integration tests
8. ⏳ **Add monitoring** - Metrics, traces, alerts

### Deployment Tasks
9. ⏳ **Choose deployment target** - Fly.io (docs exist), Railway, or Cloudflare Workers
10. ⏳ **Deploy frontend** - Vercel/Netlify/Cloudflare Pages
11. ⏳ **Configure production** - Set up real Snowflake and R2

## Current Focus

The user was reviewing the roadmap and has `src/api/routes/analysis.py` open. This file contains the upload and analyze endpoints.

## Important Notes

- **Code Style**: User prefers human-like code style (not overly commented, natural variable names, avoid excessive lambda expressions)
- **Architecture**: Strong emphasis on clean architecture with separation of concerns
- **Type Safety**: Python type hints are important throughout
- **Testing**: Mock modes enable testing without external dependencies

## Files to Review

**Key Backend Files:**
- `src/api/routes/analysis.py` - Upload and analysis endpoints (currently open)
- `src/api/routes/sessions.py` - Session management and chat
- `src/core/analysis/coach.py` - Coaching logic
- `src/infrastructure/anthropic/client.py` - Claude API client
- `src/infrastructure/snowflake/repositories/sessions.py` - Session repository

**Key Frontend Files:**
- `frontend/src/App.jsx` - Main application component
- `frontend/src/components/SessionHistory.jsx` - Session history view

**Configuration:**
- `src/config/settings.py` - Application settings
- `pyproject.toml` - Python dependencies
- `fly.toml` - Fly.io deployment config
- `env.template` - Environment variable template

## Getting Started

1. **Backend**: `pip install -e .` then `uvicorn src.main:app --reload`
2. **Frontend**: `cd frontend && npm install && npm run dev`
3. **Mock Mode**: Set `SNOWFLAKE_MOCK_MODE=true` and `R2_MOCK_MODE=true` in `.env`
4. **API Key**: Use `dev-key-1` for local testing

## Questions for New Agent

- What should we prioritize next?
- Are there any specific features the user wants to add?
- Any bugs or issues to address?
- Ready to deploy to production or still in development?


