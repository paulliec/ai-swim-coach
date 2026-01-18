# Rate Limiting in Agentic AI Flows - Article Notes

## The Problem

Agentic AI workflows make multiple API calls in rapid succession. A typical multi-pass analysis might make 3-5 calls to Claude within 30 seconds. This quickly hits rate limits, especially on free/lower tier accounts.

## What We Experienced

Our SwimCoach agentic flow:
1. Initial frame analysis → API call #1
2. AI requests more frames at specific timestamps
3. Second analysis with additional frames → API call #2
4. Third pass for final details → API call #3
5. Final coaching feedback generation → API call #4

Result: `HTTP 429 Too Many Requests` on call #3

## Solutions Implemented

### 1. Simple Throttling (What We Did)
```python
API_CALL_DELAY_SECONDS = 2.0

# Before each API call (except first)
if iterations > 1:
    await asyncio.sleep(API_CALL_DELAY_SECONDS)
```

**Pros**: Simple, effective, predictable
**Cons**: Adds fixed delay even when not needed

### 2. Partial Results (Graceful Degradation)
```python
if "rate limit" in error_msg.lower() and analysis_progress:
    return AgenticAnalysisResponse(
        summary=f"⚠️ Partial analysis: {last_observations}",
        partial=True,
        analysis_progress=analysis_progress,
    )
```

**Pros**: User gets something instead of error
**Cons**: Incomplete analysis

## Future Improvements to Explore

### 3. Adaptive Throttling (Check Rate Limit Headers)
Anthropic returns headers:
- `x-ratelimit-limit-requests`: Your limit
- `x-ratelimit-remaining-requests`: Remaining in window
- `x-ratelimit-reset-requests`: When limit resets

```python
# Pseudocode
remaining = response.headers.get('x-ratelimit-remaining-requests')
if int(remaining) < 3:
    await asyncio.sleep(10)  # Back off aggressively
elif int(remaining) < 10:
    await asyncio.sleep(3)   # Moderate backoff
```

### 4. Token Bucket Algorithm
Pre-emptively track your own usage:
```python
class RateLimiter:
    def __init__(self, tokens_per_minute=50):
        self.tokens = tokens_per_minute
        self.last_refill = time.time()
        
    async def acquire(self):
        self._refill()
        if self.tokens < 1:
            wait_time = 60 - (time.time() - self.last_refill)
            await asyncio.sleep(wait_time)
        self.tokens -= 1
```

### 5. Queue-Based Processing (Production)
For multi-user systems:
- Redis Queue or Celery
- Global rate limiter across all users
- Priority queues for paid users

### 6. Caching Similar Requests
If two users upload similar videos:
- Hash the input characteristics
- Return cached analysis if similar enough

## Key Insights

1. **Simple delays work**: 2-second delay between calls prevented 95% of rate limit issues

2. **Graceful degradation matters**: Returning partial results is better than failing completely

3. **User feedback is critical**: Show progress so users know it's working, not stuck

4. **Design for failure**: Assume rate limits will happen, build recovery into the flow

## Article Outline

1. **Hook**: "Your AI agent just failed on step 3 of 5. Now what?"

2. **The Reality**: Agentic flows = multiple API calls = rate limit risk

3. **Quick Wins**:
   - Add delays between calls
   - Return partial results on failure
   - Show progress to users

4. **Advanced Strategies**:
   - Header-based adaptive throttling
   - Token bucket algorithms
   - Queue-based architectures

5. **Code Examples**: Real implementation from SwimCoach

6. **Tradeoffs**: Latency vs reliability vs complexity

---

*This would make a good Medium article or dev.to post targeting AI/ML engineers building agentic systems.*
