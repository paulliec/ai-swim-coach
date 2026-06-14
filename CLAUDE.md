# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SwimCoach AI is a swim technique analysis platform. Users upload swimming videos, the backend extracts frames and sends them to Claude's vision API for analysis, and an AI coaching conversation follows. The frontend is a React SPA with Clerk authentication; the backend is a FastAPI service backed by Snowflake (data), Cloudflare R2 (video/frame storage), and the Anthropic API (analysis).

## Common Commands

### Backend
```bash
# Activate venv (from swimcoach/ root)
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux

# Install all dependencies including dev tools
pip install -e ".[dev]"

# Run backend (dev mode with auto-reload)
uvicorn src.main:app --reload

# Run all tests
pytest

# Run a single test file
pytest tests/unit/test_models.py

# Run tests with specific marker/keyword
pytest -k "test_function_name"

# Type checking
mypy src/

# Lint and format
ruff check src/ tests/
ruff format src/ tests/
```

### Frontend (from `frontend/` directory)
```bash
npm install
npm run dev      # Dev server at localhost:3000
npm run build    # Production build to dist/
```

### Deployment
```bash
fly deploy                    # Deploy backend to Fly.io
fly logs                      # View production logs
fly secrets set KEY="value"   # Set environment variables
```

## Architecture

The codebase follows a layered architecture with strict dependency boundaries:

- **`src/api/`** — FastAPI routes, middleware, dependency injection. Entry points for HTTP requests.
- **`src/core/`** — Business logic with zero framework/infrastructure imports. Contains coaching logic (`coach.py`, `agentic_coach.py`), frame extraction (`frames.py`), and domain models (`models.py`).
- **`src/infrastructure/`** — Thin wrappers around external services (Anthropic, Snowflake, R2 storage). Translates between external formats and domain models.
- **`src/config/settings.py`** — Pydantic `BaseSettings` loaded from env vars/`.env`. Cached via `get_settings()` (call `.cache_clear()` in tests to reset).

Key principle: `core/` never imports from `api/` or `infrastructure/`. Dependencies flow inward.

## Mock Modes

Three mock modes allow local development without external services:
- `SNOWFLAKE_MOCK_MODE=true` — in-memory database
- `R2_MOCK_MODE=true` — in-memory object storage
- `VIDEO_PROCESSOR_MOCK_MODE=true` — skip FFmpeg requirement

The Anthropic API key is always required (no mock for LLM).

## Tooling Configuration

- **Python**: 3.11+, managed via `pyproject.toml`
- **Linting**: Ruff (line-length 100, `B008` ignored for FastAPI's `Depends()` pattern)
- **Type checking**: mypy with strict settings (`disallow_untyped_defs`, `strict_equality`, etc.)
- **Testing**: pytest with `--strict-markers`, `--cov=src`, `asyncio_mode = "auto"`
- **Frontend**: React 18 + Vite + Tailwind CSS + Clerk auth

## API Route Prefixes

- `/health` — Health checks
- `/api/v1/analysis` — Video upload and analysis
- `/api/v1/sessions` — Coaching session management and chat
- `/api/v1/video` — Video analysis endpoints

## Code Style & Voice

Senior engineer, pragmatic. Code should read like someone who's been
around and made choices deliberately.

### Comments
- Terse. Explain why and enough what to jog future-me's memory
- Skip obvious ones. Add when the reason isn't clear from context
- Good: `# R2 over S3 - cheaper egress for video at this scale`
- Good: `# two passes: cheap wide scan first, targeted detail second`
- Bad: `# initialize the session variable`

### TODOs
- `# TODO: fix later - [brief reason it's deferred]`
- Example: `# TODO: fix later - retry logic is naive, fine for low volume`

### Naming
- Use domain terms: stroke_type, split_time, frames, passes, session
- Terse over descriptive when context is clear
- `get_frames` not `retrieve_video_frames_from_storage`

### Abstraction
- DRY yes, but not at the cost of readability
- Abstract real repetition, not hypothetical reuse
- If the abstraction makes you ask "wtf is this again" — it's too much
- No BaseProcessorAbstractFactoryInterface type nonsense
- Concrete and clear beats architecturally "correct"

### Dead Code
- Delete it. That's what git is for.

### Commits
- Never add Co-Authored-By tags to commit messages
- Commit messages like a person: "fix retry logic" not
  "Implement robust exponential backoff for API rate limiting"

### General
- Simple and correct beats clever
- If it works and isn't a liability, leave it alone
- No over-engineering for hypothetical scale

## Key Files

- `src/main.py` — App factory (`create_app()`), CORS, router registration
- `src/core/analysis/coach.py` — Core coaching prompt logic
- `src/core/analysis/agentic_coach.py` — Agentic coaching flow
- `src/infrastructure/anthropic/client.py` — Claude API integration
- `src/infrastructure/snowflake/repositories/` — Data access layer (repository pattern)
- `scripts/setup_snowflake.sql` — Database schema
- `fly.toml` — Fly.io deployment config (region: ord, port: 8080)
