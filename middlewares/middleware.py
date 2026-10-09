"""
Rate-Limit Middleware — prevents a single user from spamming callbacks/messages.
Config: max N requests per T seconds per user.
"""

import time
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

logger = logging.getLogger("ratelimit")

# ── Settings ──────────────────────────────────────────────────────────────────
WINDOW_SECS = 5      # rolling window
MAX_REQUESTS = 10    # max requests per window per user
THROTTLE_MSG = "⚠️ You're sending requests too fast. Please slow down."


class RateLimitMiddleware(BaseMiddleware):
    """
    Token-bucket style rate limiter per user.
    Drops requests that exceed MAX_REQUESTS within WINDOW_SECS.
    """

    def __init__(self, max_requests: int = MAX_REQUESTS, window: float = WINDOW_SECS):
        self.max_requests = max_requests
        self.window = window
        # {user_id: [timestamp, timestamp, ...]}
        self._buckets: dict[int, list[float]] = defaultdict(list)
        self._request_count = 0
        super().__init__()

    def _is_throttled(self, user_id: int) -> bool:
        now = time.monotonic()
        bucket = self._buckets[user_id]
        # Remove timestamps outside the window
        active = [t for t in bucket if now - t < self.window]
        if len(active) >= self.max_requests:
            self._buckets[user_id] = active
            return True
        active.append(now)
        self._buckets[user_id] = active
        return False

    def _cleanup_stale_buckets(self) -> None:
        """Remove buckets for users who have had no requests within the window."""
        now = time.monotonic()
        stale = [uid for uid, ts in self._buckets.items() if all(now - t >= self.window for t in ts)]
        for uid in stale:
            del self._buckets[uid]

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if user and self._is_throttled(user.id):
            logger.warning(f"Rate-limited user {user.id}")
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(THROTTLE_MSG, show_alert=True)
                except Exception:
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer(THROTTLE_MSG)
                except Exception:
                    pass
            return  # Drop the request

        # Periodically sweep stale buckets to prevent unbounded dict growth
        self._request_count += 1
        if self._request_count % 500 == 0:
            self._cleanup_stale_buckets()

        return await handler(event, data)
