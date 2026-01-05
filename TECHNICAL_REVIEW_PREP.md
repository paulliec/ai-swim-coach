# Technical Review Prep Guide

## Structure Your Walkthrough (5-10 min)

### 1. Problem → Solution → Results (30 seconds)
> "I wanted on-demand swim coaching. Built an app that analyzes video with AI. It's live, users have tried it."

### 2. Architecture Overview (2 min)
Draw the diagram:
```
Frontend (React) → API (FastAPI) → Claude Vision API
                       ↓
                   Snowflake (sessions, history)
                   R2 Storage (frames)
```

### 3. Walk Through One Request (3 min)
> "User uploads video → browser extracts frames → uploads to API → stores in R2 → calls Claude with images → saves analysis to Snowflake → returns coaching feedback → user can ask follow-up questions in context"

### 4. Interesting Decisions (3 min)
Pick 2-3 to highlight (see below)

---

## Questions to Anticipate

### Architecture & Design

**Q: Why client-side frame extraction instead of server-side?**
> "Bandwidth and UX. User uploads 3MB of frames vs 100MB video. They see frames before submitting. Browser handles codec complexity. Tradeoff: less control over extraction quality."

**Q: Why FastAPI over Django/Flask/Node?**
> "Type hints, async support, automatic OpenAPI docs. For an AI app with async Claude calls, FastAPI's async-first design made sense. Also I wanted to learn it."

**Q: Why Snowflake for a small app?**
> "Honestly, I wanted to learn it. For this scale, Postgres would be fine. But Snowflake gave me experience with: VARIANT columns for JSON, Cortex for embeddings (RAG next step), and enterprise data warehouse patterns. Overkill? Yes. Learning opportunity? Also yes."

**Q: Why not just use a database for frames? Why R2?**
> "Separation of concerns. Frames are blobs, sessions are structured data. R2 is cheap object storage, Snowflake isn't designed for blob storage. Also, R2 could serve frames directly to users if needed."

**Q: Why R2 instead of S3?**
> "S3-compatible API so I'm not locked in, zero egress fees which matters for image delivery, and Cloudflare's free tier is generous. My frontend is on Cloudflare Pages too, so it's a natural fit."

---

### AI/ML Specific

**Q: How does the conversation context work?**
> "I store the full conversation history in Snowflake. When user asks a follow-up, I load the original analysis + all previous messages and send the full context to Claude. Claude doesn't have memory - I'm managing the memory."

**Q: How did you handle prompt engineering?**
> "Iteration. Early prompts gave generic advice. I added: structured output format (primary/secondary/refinement priorities), required technical terminology, explicit instruction to reference specific frames. The prompt is in `src/core/analysis/coach.py` - it's version controlled because it's core business logic."

**Q: What's the token cost per analysis?**
> "Roughly 15 frames × ~1000 tokens each + prompt + response = ~20-25K tokens per analysis. At Claude's pricing, that's roughly $0.05-0.10 per analysis. Hence the rate limiting."

**Q: Why not use GPT-4V instead of Claude?**
> "Tried both. Claude gave more structured, actionable feedback. GPT-4V was wordier. Could swap them - that's why I have a `VisionModelClient` protocol."

---

### Production & Operations

**Q: How do you handle rate limiting?**
> "Track daily usage per user ID in Snowflake. 3 analyses/day limit. Check before the expensive Claude call, not after. Bypass keys for demos. 429 response with user-friendly message."

**Q: What was the hardest bug?**
> "CORS was a pain in the ass. Also iOS Safari video loading - had to add specific attributes and delays between frame seeks. Mobile testing was 40% of debugging time."

**Q: How would you scale this?**
> "Current bottleneck is Claude API latency (10-30s). For scale: (1) Queue analysis jobs instead of blocking, (2) WebSocket for progress updates, (3) Cache common feedback patterns, (4) Batch similar requests."

**Q: What would you do differently?**
> "Would've added better observability earlier - request tracing, latency metrics. Also would've tested on mobile sooner. And maybe used Postgres instead of Snowflake for simpler local dev."

---

### Code Quality

**Q: How did you structure the codebase?**
> "Clean architecture pattern. `core/` has business logic with no framework deps. `infrastructure/` wraps external services. `api/` is thin - just HTTP handling. This means I can test the coaching logic without mocking Snowflake."

**Q: How did you use AI tools in development?**
> "Extensively. Claude helped with boilerplate, debugging, prompt iteration. But I reviewed everything, understood the architecture decisions, and debugged the tricky parts myself (looking at you, iOS Safari). AI accelerated development but didn't replace understanding."

**Q: What's the test coverage?**
> "Honestly, lower than I'd like for a portfolio project. I have mock modes for Snowflake and R2 that let me test the flow locally. Priority was shipping a working product. I'd add more integration tests before adding major features."

---

### RAG / Next Steps

**Q: What's the RAG plan?**
> "Store swimming technique content with vector embeddings in Snowflake using Cortex. At analysis time, query for relevant content based on what the AI observes. Augment Claude's prompt with authoritative sources. Output can cite 'According to USMS...' instead of just Claude's training data."

**Q: What else would you add?**
> "Video timeline markers - 'issue at 0:15, see this specific moment.' Server-side video processing for users who don't want client extraction. Comparative analysis - 'you're improving since last session.' Social features - share with your coach."

---

## Red Flags to Avoid

❌ "I just followed a tutorial"
✅ "I started with X, then modified it because Y wasn't working for my use case"

❌ "Claude wrote most of it"
✅ "I used Claude to accelerate boilerplate. The architecture decisions and debugging were mine."

❌ Can't explain a piece of code in the repo
✅ Walk through any file and explain why it exists

❌ "It just works"
✅ Know one thing that's hacky and why you'd fix it

---

## Practice Questions

Have someone ask you these rapid-fire:

1. Walk me through what happens when I upload a video
2. Why did you choose [any technology]?
3. What was the hardest technical problem?
4. How would this break at 10x scale?
5. Show me the code for [rate limiting / conversation context / frame extraction]
6. What would you do differently?
7. How did you use AI tools?

---

## Key Files to Know

| File | Purpose | Be ready to explain |
|------|---------|---------------------|
| `frontend/src/App.jsx` | Main UI + frame extraction | How extractFrames() works, FPS selector |
| `src/core/analysis/coach.py` | Coaching logic + prompts | SYSTEM_PROMPT, why it's structured this way |
| `src/api/routes/analysis.py` | Upload + analyze endpoints | Rate limiting, request flow |
| `src/infrastructure/storage/client.py` | R2/mock storage | Why R2, mock mode pattern |
| `src/infrastructure/snowflake/repositories/sessions.py` | Session persistence | VARIANT column handling |
| `src/api/dependencies.py` | FastAPI DI | How services are injected |

---

## The "I Don't Know" Answers

It's okay to say:
- "I haven't load tested it yet"
- "That's an area I'd improve"
- "I chose X for learning purposes, Y would be more appropriate at scale"
- "I used AI to help write that, but I understand what it does"

Honesty about limitations shows maturity.

