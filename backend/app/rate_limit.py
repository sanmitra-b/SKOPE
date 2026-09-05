"""Process-local per-user chat limiter for the single API instance."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from .config import get_settings


_requests: defaultdict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def enforce_chat_rate_limit(user_id: str) -> None:
    now = time.monotonic()
    window_start = now - 60.0
    limit = get_settings().chat_rate_limit_per_minute
    with _lock:
        queue = _requests[user_id]
        while queue and queue[0] < window_start:
            queue.popleft()
        if len(queue) >= limit:
            retry_after = max(1, int(60 - (now - queue[0])))
            raise HTTPException(
                status_code=429,
                detail="Chat rate limit exceeded. Try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
        queue.append(now)
