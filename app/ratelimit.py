import time

from fastapi import HTTPException, Request

# simple in-memory rate limiter (per client IP + action) to slow brute-force attempts
_rate_hits: dict[str, list[float]] = {}
_last_sweep = 0.0


def client_ip(request: Request) -> str:
    """Real visitor IP. Requests reach us through the Vercel rewrite + Render's
    proxy, so request.client.host is a proxy address shared by every visitor;
    the original IP is the first entry of X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _sweep(now: float):
    """Drop stale keys now and then so the dict doesn't grow forever."""
    global _last_sweep
    if now - _last_sweep < 600:
        return
    _last_sweep = now
    for k in [k for k, v in _rate_hits.items() if not v or now - v[-1] > 3600]:
        _rate_hits.pop(k, None)


def rate_limit(request: Request, action: str, max_calls: int, window_sec: int):
    now = time.time()
    _sweep(now)
    key = f"{action}:{client_ip(request)}"
    hits = [t for t in _rate_hits.get(key, []) if now - t < window_sec]
    if len(hits) >= max_calls:
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")
    hits.append(now)
    _rate_hits[key] = hits


def reset():
    """Clear all counters (used by tests)."""
    _rate_hits.clear()
