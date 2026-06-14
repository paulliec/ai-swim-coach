# When “Analyze This Video” Stopped Being One API Call

*SwimCoach AI, continued — multi-pass vision, Snowflake RAG, partial results, and the rate-limit work nobody puts in the demo. ([Earlier deep dive](https://github.com/paulliec/ai-swim-coach).)*

---

## Quick recap

The first piece walked through SwimCoach AI end to end: client-side frame extraction (small uploads, instant preview), Claude vision for multi-image analysis, conversational follow-up with session history in Snowflake, and deployment on Cloudflare Pages + Fly.io. It also teased **RAG** over a `coaching_knowledge` table in Snowflake Cortex as a next step.

This article is the delta: the features that landed after that draft, and the reliability work that only shows up when real users (and real API limits) hit the system.

**Live demo:** [ai-swim-coach.pages.dev](https://ai-swim-coach.pages.dev)  
**Code:** [github.com/paulliec/ai-swim-coach](https://github.com/paulliec/ai-swim-coach)

---

## Two ways to analyze: frames vs full video

The original design optimized for **frames extracted in the browser** — great for bandwidth and preview, still the default path for many users.

We added a second mode: **upload the full video** and let the server run an **agentic** loop. FFmpeg pulls metadata and frames on demand; the model can ask for another pass at specific timestamps instead of burning tokens on a uniform grid across the whole clip.

Conceptually it mirrors how a coach watches video: wide pass first, then zoom in on the catch, the breath, or the turn.

The core loop lives in something like `AgenticSwimCoach`: sparse initial sampling (configurable FPS, capped frame counts), then structured JSON from the model describing whether it needs more frames and **where** in the timeline. The API returns **timestamp-linked feedback** (start/end seconds, category, observation, recommendation) so the UI can tie advice to moments in the footage, not just “frame 7.”

Tradeoff, stated plainly: full video means **larger uploads** and **server-side FFmpeg** (timeouts and codec edge cases become your problem). We tightened timeouts and added CI so regressions don’t slip in quietly.

---

## RAG is real now (not just a SQL sketch)

The “what’s next” section of the first article showed a Cortex embedding query. That path is implemented: a **Snowflake-backed knowledge repository** runs semantic search with `VECTOR_COSINE_SIMILARITY` and `SNOWFLAKE.CORTEX.EMBED_TEXT_768`, and retrieved chunks are injected into the system prompt for both the **classic frame analysis** and the **agentic** flow.

Design choice that mattered: **RAG augments the coach; it doesn’t replace vision.** The prompts tell the model to ground observations in what it actually sees, and use the retrieved text as reference material.

If retrieval fails (connector hiccup, empty table, whatever), analysis **continues without** the extra context — same pattern as any production RAG feature you don’t want to be a single point of failure.

---

## Agentic analysis + chat in one session

Early agentic runs produced a nice on-screen report but **didn’t always persist** in a shape that the existing chat flow could use. We fixed that by **saving agentic results to Snowflake** in the same session model the rest of the app expects: summary enriched with the structured feedback lines so follow-up questions still have coherent context.

That sounds like a small integration detail; in practice it’s the difference between “cool demo” and “I can ask a second question tomorrow.”

---

## Rate limits: where naive AI apps die

Multi-pass vision is multiple **paid** API calls in one user action. Throw in Anthropic rate limits and you get:

- Partial analysis mid-loop  
- Angry users who think the app is broken  
- Double retries if **both** the SDK and your code sleep and retry  

We addressed it in layers:

1. **Application-level throttling** between iterations where it helps avoid hammering the API.  
2. **Custom backoff** in our Anthropic wrapper: disable the SDK’s built-in retries (`max_retries=0`) so **one** retry strategy owns rate-limit behavior — avoids compounding sleeps and confusing failure modes.  
3. **Partial results + resume**: the agentic response can flag `partial` and `can_resume`; the client can call a **resume** endpoint to continue instead of starting from zero.  
4. **UX**: a **countdown auto-resume** on the frontend so users aren’t stuck staring at a dead screen after a 429-shaped interruption.

There is still a **daily usage cap** for anonymous and normal users; we added **bypass hooks** (e.g. keyed demo paths) so you’re not blocked when showing the product — without turning the whole thing into a free unlimited vision API for the internet.

---

## Everything else that added up

Smaller fixes that don’t each deserve a diagram but matter in production:

- **Sessions list** and **chat context** bugs after mode switches — the kind of state bugs SPA users actually feel.  
- Clearer flows when **rate limited** or when the user should **sign in**.  
- **“Video not found”** class errors tied to storage/session lifecycle.  
- **Anonymous user** polish so the happy path isn’t “works only if you’re logged in and lucky.”

---

## What I’d tell myself if I started again

- **Treat multi-step vision as a workflow**, not a single request: persistence, resume, and partial output are part of the feature, not stretch goals.  
- **Own your retry policy** when using vendor SDKs — defaults are built for generic clients, not for your budget and your UX.  
- **RAG** is worth it for vocabulary and methodology alignment, but the product still lives or dies on **what the model sees** in the pixels.

---

## Wrapping up

The first article was “how to ship a credible vision coaching MVP.” This chapter is **what it takes to keep it credible** when you add deeper analysis, retrieval-augmented prompts, and enough automation that API limits become a design constraint.

Same disclaimer as before: nothing here replaces a coach on deck. It’s for the gaps — late night, solo filming, second opinion — when you still want something grounded and specific.

**Try it:** [ai-swim-coach.pages.dev](https://ai-swim-coach.pages.dev)  
**Code:** [github.com/paulliec/ai-swim-coach](https://github.com/paulliec/ai-swim-coach)

---

*Ship the MVP, then ship the boring stuff that keeps the MVP from embarrassing you in production.*
