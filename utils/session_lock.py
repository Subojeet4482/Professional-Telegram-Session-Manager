"""
Per-phone Telethon session locking.

Telethon session files are SQLite databases. Nothing previously serialized
access to a given phone's file, so the hourly scheduler sweep, the auto-logout
sweep, a bot admin clicking "OTP", and a web-panel admin clicking "2FA" could
all open the SAME .session file at the SAME time. Two Telethon clients writing
to one SQLite file concurrently raises "database is locked" at best and
corrupts the auth-key row at worst — corruption that is silently swallowed by
callers' broad `except Exception` blocks and that cannot be repaired without
re-authenticating the account by SMS.

`session_lock(phone)` is an async context manager backed by a per-phone
asyncio.Lock, held for the client's entire connect()...disconnect() lifetime.
It only serializes operations on the SAME phone — unrelated phones still run
fully concurrently, so the check_handler / broadcast / bulk_2fa Semaphore-gated
batches are not slowed down.

Usage:
    from utils.session_lock import session_lock

    async with session_lock(phone):
        client = create_client(session_path, api_id, api_hash, proxy)
        await client.connect()
        try:
            ...
        finally:
            await client.disconnect()

Locks are process-local (asyncio, not cross-process/cross-host). That matches
this deployment: bot + scheduler + web panel all run in one process (see
main.py / web/app.py's `_run_bot`), sharing one event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

logger = logging.getLogger("session_lock")

# phone -> Lock. Never explicitly deleted while in use — _prune_idle_locks()
# below reclaims entries once nothing is waiting on them, so this cannot grow
# without bound across a long-running process.
_locks: dict[str, asyncio.Lock] = {}
_last_used: dict[str, float] = {}

# Above this many tracked phones, an idle sweep runs opportunistically on the
# next acquire. Generous relative to realistic inventory sizes, existing only
# as a backstop against unbounded growth.
_PRUNE_THRESHOLD = 5000


def _normalize(phone: str) -> str:
    return (phone or "").lstrip("+").strip()


def _prune_idle_locks() -> None:
    """Drop locks for phones that are not currently held or waited on."""
    now = time.monotonic()
    stale = [
        p for p, lock in _locks.items()
        if not lock.locked() and now - _last_used.get(p, 0) > 300
    ]
    for p in stale:
        _locks.pop(p, None)
        _last_used.pop(p, None)
    if stale:
        logger.debug("Pruned %d idle session locks", len(stale))


def _get_lock(phone: str) -> asyncio.Lock:
    key = _normalize(phone)
    lock = _locks.get(key)
    if lock is None:
        if len(_locks) >= _PRUNE_THRESHOLD:
            _prune_idle_locks()
        lock = asyncio.Lock()
        _locks[key] = lock
    _last_used[key] = time.monotonic()
    return lock


@contextlib.asynccontextmanager
async def session_lock(phone: str):
    """
    Hold the per-phone lock for the duration of the `async with` block.

    Acquiring is fair (asyncio.Lock is FIFO), so a long-running scheduler sweep
    cannot starve a human admin's on-demand click indefinitely — it queues.
    """
    lock = _get_lock(phone)
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


def is_locked(phone: str) -> bool:
    """True if another operation currently holds this phone's lock."""
    lock = _locks.get(_normalize(phone))
    return bool(lock and lock.locked())
