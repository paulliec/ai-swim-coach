# Building an AI-Powered Video Coaching App: A Technical Deep Dive

*How I built SwimCoach AI using Claude's vision capabilities, React, FastAPI, and Snowflake — with lessons learned along the way.*

---

## Why Swimming? Why This?

I didn't grow up swimming. Could get to one end of the pool without drowning, but I'd be gassed after a single lap. When COVID hit and gyms closed, I moved my workouts to my backyard pool. Rigged up a harness to the diving board, watched a ton of YouTube videos, and taught myself enough to swim 30 minutes for a decent cardio workout.

That turned into lessons at the Y, which turned into joining a masters swim team. Two years later I'm swimming 6 days a week. I'll never be world class (lol, or even really club class), but I love the exercise, the team camaraderie, and the mental challenge of getting better at something I started so late.

Here's the thing though - getting technique feedback is hard. You can film yourself, but then what? You're guessing. Coaches are great but expensive and not always available. I wanted something that could give me (and swimmers like me) on-demand coaching. Won't replace a real coach watching you in person, but when that's not available or you want a second opinion, this helps.

So I built it.

**What you'll learn from this walkthrough:**
- How to use Claude's vision API for multi-image analysis
- Client-side video frame extraction (and why it matters)
- Building a conversational coaching experience
- Production deployment patterns for AI applications
- Gotchas I hit and how I solved them

**Live Demo:** [ai-swim-coach.pages.dev](https://ai-swim-coach.pages.dev)  
**GitHub:** [github.com/paulliec/ai-swim-coach](https://github.com/paulliec/ai-swim-coach)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Frontend (React + Vite)                      │
│                     Cloudflare Pages                            │
└─────────────────────┬───────────────────────────────────────────┘
                      │ HTTPS + Clerk Auth
┌─────────────────────▼───────────────────────────────────────────┐
│                      API Layer (FastAPI)                        │
│                   Fly.io (Docker)                               │
│         • Rate Limiting • CORS • Request Validation             │
└──────┬──────────────────┬──────────────────────┬────────────────┘
       │                  │                      │
┌──────▼──────┐   ┌───────▼───────┐    ┌────────▼────────┐
│   Frame     │   │   Claude      │    │   Snowflake     │
│  Storage    │   │   Vision API  │    │   (Sessions,    │
│  (R2)       │   │   (Analysis)  │    │    History)     │
└─────────────┘   └───────────────┘    └─────────────────┘
```

### Key Design Decisions

**1. Client-Side Frame Extraction**

First big decision: where do we extract frames from video? Server-side seems safer but client-side turned out to be the right call:

- **No server upload bandwidth** — Users extract 15-60 frames (~2-5MB) instead of uploading a 100MB video
- **Instant preview** — Users see extracted frames before submitting
- **Works with any format** — Browser handles codec hell, not my server
- **Mobile-friendly** — Less data on cellular

```javascript
const extractFrames = async (file, fps) => {
  const video = document.createElement('video')
  video.src = URL.createObjectURL(file)
  
  await new Promise(resolve => {
    video.onloadedmetadata = resolve
  })
  
  const frameCount = Math.min(Math.round(video.duration * fps), 60)
  const frames = []
  
  for (let i = 0; i < frameCount; i++) {
    video.currentTime = (i / frameCount) * video.duration
    await new Promise(resolve => { video.onseeked = resolve })
    
    const canvas = document.createElement('canvas')
    canvas.getContext('2d').drawImage(video, 0, 0)
    frames.push(await canvasToBlob(canvas))
  }
  
  return frames
}
```

**Gotcha: iOS Safari Video Loading**

Mobile Safari is... special. Videos don't load metadata reliably without these attributes:

```javascript
video.muted = true
video.playsInline = true  // Critical for iOS
video.preload = 'auto'
```

Also needed small delays between seeks for iOS to actually render frames. Spent way too long debugging this one.

**2. Conversational Context**

A coaching session isn't one Q&A — it's a conversation. The coach needs to remember what they already said, what frames they looked at, what your specific issues were.

```python
def continue_conversation(session_id: UUID, user_message: str):
    session = repository.get_session(session_id)
    
    # Build conversation history for Claude
    messages = [
        {"role": "user", "content": build_initial_analysis_prompt(session)},
        {"role": "assistant", "content": session.initial_analysis},
    ]
    
    for msg in session.conversation_history:
        messages.append({"role": msg.role, "content": msg.content})
    
    messages.append({"role": "user", "content": user_message})
    
    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        messages=messages,
        system=SWIM_COACH_SYSTEM_PROMPT
    )
    
    return response.content
```

This way the coach can say things like "Looking back at frame 7 I mentioned earlier, notice how your elbow drops during the catch..."

**3. Rate Limiting**

AI API calls are expensive. Needed rate limiting, but also a bypass for demos:

```python
@router.post("/{session_id}/analyze")
async def analyze_session(
    session_id: UUID,
    x_user_id: str = Header(None),
    x_bypass_key: str = Header(None),
    usage_limits: UsageLimitRepository = Depends(get_usage_limit_repository),
):
    if x_bypass_key in settings.rate_limit_bypass_keys:
        pass  # Skip limit
    else:
        allowed, count, limit = usage_limits.check_and_increment(
            identifier=x_user_id or "anonymous",
            resource_type="video_analysis",
            limit_max=3,
            period_hours=24
        )
        
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Daily limit reached ({count}/{limit}). Try again tomorrow!"
            )
```

---

## The AI Part: Prompt Engineering

Getting Claude to give *useful* feedback wasn't trivial. Early prompts got generic advice like "keep your body streamlined" — true but useless.

The key was structured output and technical vocabulary:

```python
SWIM_COACH_SYSTEM_PROMPT = """You are an expert swim coach analyzing technique from video frames.

For each observation, provide:
1. WHAT you see (specific body position, timing, angle)
2. WHY it matters (impact on speed, efficiency, injury risk)  
3. HOW to fix it (concrete drill or focus point)

Prioritize by impact:
- PRIMARY: Major inefficiencies costing significant speed/energy
- SECONDARY: Moderate issues worth addressing
- REFINEMENT: Fine-tuning for competitive swimmers

Use technical terminology: catch, pull, recovery, hip rotation, 
bilateral breathing, early vertical forearm, etc.

Reference specific frames: "In frame 3, notice how..."
"""
```

### Vision API

Claude's vision API takes multiple images in one request — perfect for analyzing a sequence:

```python
async def analyze_video(self, frames: list[bytes], stroke_type: str):
    content = []
    
    for i, frame_data in enumerate(frames):
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(frame_data).decode()
            }
        })
        content.append({
            "type": "text",
            "text": f"Frame {i+1}"
        })
    
    content.append({
        "type": "text", 
        "text": f"Analyze this {stroke_type} technique across all frames."
    })
    
    response = await self.client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SWIM_COACH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}]
    )
    
    return self._parse_analysis(response.content)
```

---

## Gotchas

### CORS

The classic "works locally, breaks in production" issue. Frontend on Cloudflare Pages couldn't talk to backend on Fly.io. This was a pain in the ass to debug because CORS errors in the browser console often mask the *real* error.

**Wrong:**
```python
allow_origins=["*"]  # Insecure, breaks credentials
```

**Right:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ai-swim-coach.pages.dev",
        "http://localhost:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Always check backend logs when debugging CORS. The browser error is usually misleading.

### Snowflake VARIANT Columns

Snowflake stores JSON in VARIANT columns, but the Python connector sometimes returns strings, sometimes dicts. Fun!

```python
def _parse_variant_json(self, value):
    """Handle Snowflake VARIANT that may be str or dict."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None
```

### Mobile Video Extraction

Frame extraction worked great on desktop. Mobile Safari produced blank frames. Fix: add delays between seeks to let iOS actually render:

```javascript
await new Promise(resolve => { video.onseeked = resolve })

if (isMobileDevice()) {
  await new Promise(resolve => setTimeout(resolve, 100))
}

// NOW the frame is ready
ctx.drawImage(video, 0, 0)
```

Mobile testing accounted for probably 40% of my debugging time on this project.

### Vite Environment Variables

Vite requires `VITE_` prefix for client-side env vars. Security feature (prevents leaking server secrets), easy to forget:

```javascript
// Wrong - undefined in browser
const apiKey = process.env.API_KEY

// Right
const apiKey = import.meta.env.VITE_API_KEY
```

---

## FPS Selector

Users analyzing fast movements (catch, entry, flip turn) need more frames. Users wanting quick feedback prefer fewer. Added a slider:

```jsx
<input
  type="range"
  min="0.5"
  max="3"
  step="0.5"
  value={framesPerSecond}
  onChange={(e) => {
    setFramesPerSecond(parseFloat(e.target.value))
    extractFrames(videoFile, parseFloat(e.target.value))
  }}
/>
```

Higher FPS = more detail but longer extraction, more upload data, more expensive/slower AI analysis. The slider lets users make that tradeoff.

---

## Deployment

### Backend: Fly.io

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY src/ ./src/
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```bash
fly launch
fly secrets set ANTHROPIC_API_KEY="sk-ant-..."
fly deploy
```

Key settings - scale to zero when idle (saves money on free tier):
```toml
[http_service]
  auto_stop_machines = "suspend"
  auto_start_machines = true
  min_machines_running = 0
```

### Frontend: Cloudflare Pages

Push to GitHub, connect to Cloudflare Pages, set env vars in dashboard. Done.

---

## What's Next

The system currently relies on Claude's training data for swimming knowledge. Next step is RAG with authoritative sources - US Masters Swimming articles, Total Immersion methodology, SwimSmooth content. 

Using Snowflake Cortex for embeddings:

```sql
CREATE TABLE coaching_knowledge (
    content_id VARCHAR PRIMARY KEY,
    source VARCHAR,
    topic VARCHAR,
    content TEXT,
    embedding VECTOR(768)
);

SELECT content
FROM coaching_knowledge
WHERE VECTOR_COSINE_SIMILARITY(
    embedding, 
    EMBED_TEXT_768('freestyle catch early vertical forearm')
) > 0.7
LIMIT 5;
```

Then inject retrieved passages into Claude's context for grounded, citation-backed feedback.

---

## Wrapping Up

I built this for myself and swimmers like me. People who came to the sport late, who don't have unlimited access to coaches, who want to get better and are willing to put in the work. The AI won't replace a coach on deck watching you swim. But when that's not available, or you want feedback on a workout you filmed, or you just want to nerd out about technique at 10pm - it's there.

**Try it:** [ai-swim-coach.pages.dev](https://ai-swim-coach.pages.dev)  
**Code:** [github.com/paulliec/ai-swim-coach](https://github.com/paulliec/ai-swim-coach)

Questions or want to chat about building AI applications? Find me on LinkedIn.

---

*Vision AI that actually does something useful. That's the goal anyway.*
