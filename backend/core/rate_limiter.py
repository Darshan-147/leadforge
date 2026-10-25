from collections import deque
from math import ceil
from threading import Lock
from time import monotonic

from fastapi import Depends, HTTPException

from core.security import AuthenticatedUser, require_user

# In-memory rate limiter for a single backend process.
# Keys are (operation name, authenticated user ID).
_RATE_LIMIT_BUCKETS: dict[
    tuple[str, str], tuple[int, deque[float]]
] = {}

_RATE_LIMIT_LOCK = Lock()
_RATE_LIMIT_REQUEST_COUNT = 0


def user_rate_limit(
    scope: str,
    limit: int,
    window_seconds: int,
):
    async def enforce_limit(
        user: AuthenticatedUser = Depends(require_user),
    ) -> None:
        global _RATE_LIMIT_REQUEST_COUNT

        now = monotonic()
        key = (scope, user.id)

        with _RATE_LIMIT_LOCK:
            bucket = _RATE_LIMIT_BUCKETS.get(key)

            if bucket is None or bucket[0] != window_seconds:
                timestamps: deque[float] = deque()
                _RATE_LIMIT_BUCKETS[key] = (
                    window_seconds,
                    timestamps,
                )
            else:
                timestamps = bucket[1]

            # Remove requests outside the current time window.
            while (
                timestamps
                and now - timestamps[0] >= window_seconds
            ):
                timestamps.popleft()

            # Reject the request if the user has reached the limit.
            if len(timestamps) >= limit:
                retry_after = max(
                    1,
                    ceil(
                        window_seconds
                        - (now - timestamps[0])
                    ),
                )

                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Rate limit exceeded for {scope}. "
                        f"Try again in {retry_after} seconds."
                    ),
                    headers={
                        "Retry-After": str(retry_after)
                    },
                )

            timestamps.append(now)
            _RATE_LIMIT_REQUEST_COUNT += 1

            # Periodically remove expired buckets for inactive users.
            if _RATE_LIMIT_REQUEST_COUNT % 100 == 0:
                for stale_key, (
                    stale_window,
                    stale_timestamps,
                ) in list(_RATE_LIMIT_BUCKETS.items()):
                    while (
                        stale_timestamps
                        and now - stale_timestamps[0]
                        >= stale_window
                    ):
                        stale_timestamps.popleft()

                    if not stale_timestamps:
                        _RATE_LIMIT_BUCKETS.pop(
                            stale_key, None
                        )

    return enforce_limit
