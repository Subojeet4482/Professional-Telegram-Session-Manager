"""
Auto Log Out utilities — terminate every Telegram authorization except ours.

Telegram refuses to let a *freshly* created session reset other authorizations
(`FreshResetAuthorisationForbiddenError` — 24h after the session was created,
or `SessionTooFreshError` which carries an explicit countdown). When that
happens we park the phone in a small retry queue stored in the scheduler table
and the scheduler loop picks it up once the restriction expires.

Used by:
  - handlers/register.py        (after a new session is registered)
  - handlers/scheduler_handler.py (scheduled sweep + "Run logout now")
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from telethon.errors import (
    FloodWaitError,
    FreshResetAuthorisationForbiddenError,
    SessionTooFreshError,
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
)
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest

import database.database as db
from config.config_manager import get_api_credentials, get_proxy
from utils.country_utils import get_all_sessions, SESSIONS_DIR
from utils.session_lock import session_lock
from workers.session_worker import create_client

logger = logging.getLogger("auto_logout")

# Telegram's fresh-session restriction window for resetting other authorizations.
FRESH_WINDOW = 24 * 3600

# Key of the retry queue inside the `scheduler` table.
_PENDING_KEY = "pending_logouts"

# Serialises read-modify-write on the queue (scheduler loop + handlers touch it).
_queue_lock = asyncio.Lock()


# ══════════════════════════════════════════════════════════════════════════════
#  RESULT
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LogoutResult:
    """
    Outcome of one termination attempt.

    status:
      done          — every other authorization was terminated
      nothing       — the bot session was the only one
      restricted    — Telegram blocked it; retry_after holds the wait in seconds
      unauthorized  — our own session is dead (logged out elsewhere / banned)
      error         — anything else; `error` holds the message
    """
    status: str
    terminated: int = 0
    others: int = 0
    total: int = 0
    retry_after: int = 0
    error: str = ""
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("done", "nothing")


def format_wait(seconds: int) -> str:
    """Human-readable countdown, e.g. '23h 41m' / '4m 20s'."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# ══════════════════════════════════════════════════════════════════════════════
#  CORE TERMINATION
# ══════════════════════════════════════════════════════════════════════════════

def _fresh_wait_from(current_auth) -> int:
    """
    Seconds left before this session may reset others.

    `FreshResetAuthorisationForbiddenError` carries no countdown, so derive it
    from when the current authorization was created (Telegram allows it 24h
    after login). Falls back to a full window when the date is unavailable.
    """
    created = getattr(current_auth, "date_created", None)
    if created is None:
        return FRESH_WINDOW
    try:
        elapsed = time.time() - created.timestamp()
    except Exception:
        return FRESH_WINDOW
    return max(60, int(FRESH_WINDOW - elapsed))


async def terminate_other_sessions(client) -> LogoutResult:
    """
    Terminate every authorization except the one this client is using.

    The client must already be connected. Never raises — every failure mode is
    reported through the returned LogoutResult.
    """
    try:
        if not await client.is_user_authorized():
            return LogoutResult(status="unauthorized")

        result = await client(GetAuthorizationsRequest())
        auths = result.authorizations
    except (AuthKeyUnregisteredError, UserDeactivatedBanError):
        return LogoutResult(status="unauthorized")
    except Exception as e:
        logger.warning("GetAuthorizations failed: %s", e)
        return LogoutResult(status="error", error=f"{type(e).__name__}: {e}")

    total = len(auths)
    current = next((a for a in auths if a.current), None)
    others = [a for a in auths if not a.current]

    if not others:
        return LogoutResult(status="nothing", total=total, others=0)

    terminated = 0
    failed: list[str] = []

    for auth in others:
        try:
            await client(ResetAuthorizationRequest(hash=auth.hash))
            terminated += 1
        except FreshResetAuthorisationForbiddenError:
            # Account-wide restriction — no point trying the remaining ones.
            return LogoutResult(
                status="restricted",
                terminated=terminated,
                others=len(others),
                total=total,
                retry_after=_fresh_wait_from(current),
            )
        except SessionTooFreshError as e:
            return LogoutResult(
                status="restricted",
                terminated=terminated,
                others=len(others),
                total=total,
                retry_after=max(60, int(e.seconds)),
            )
        except FloodWaitError as e:
            return LogoutResult(
                status="restricted",
                terminated=terminated,
                others=len(others),
                total=total,
                retry_after=max(60, int(e.seconds)),
            )
        except (AuthKeyUnregisteredError, UserDeactivatedBanError):
            return LogoutResult(status="unauthorized", terminated=terminated, others=len(others), total=total)
        except Exception as e:
            logger.warning("Reset authorization hash=%s failed: %s", auth.hash, e)
            failed.append(f"{type(e).__name__}: {e}")

    if terminated == 0 and failed:
        return LogoutResult(
            status="error",
            others=len(others),
            total=total,
            error=failed[0],
            failed=failed,
        )

    return LogoutResult(
        status="done",
        terminated=terminated,
        others=len(others),
        total=total,
        failed=failed,
    )


async def count_authorizations(client) -> int:
    """Number of active authorizations, or -1 if it could not be read."""
    try:
        result = await client(GetAuthorizationsRequest())
        return len(result.authorizations)
    except Exception as e:
        logger.warning("count_authorizations failed: %s", e)
        return -1


# ══════════════════════════════════════════════════════════════════════════════
#  BY PHONE (opens its own client)
# ══════════════════════════════════════════════════════════════════════════════

def find_session_path(phone: str) -> str | None:
    """Locate the stored .session file for a phone (without the extension)."""
    phone = phone.lstrip("+")
    for folder, phones in get_all_sessions().items():
        if phone in phones:
            return os.path.join(SESSIONS_DIR, folder, phone)
    return None


async def logout_others_for_phone(phone: str) -> LogoutResult:
    """Open the stored session for `phone` and terminate its other authorizations."""
    session_path = find_session_path(phone)
    if not session_path:
        return LogoutResult(status="error", error="Session file not found")

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    # This one function is called both by the scheduler's background
    # auto-logout sweep AND by every on-demand "log out others" trigger in the
    # bot, so a single lock here serializes the two most likely colliding
    # writers to this phone's .session file.
    client = None
    async with session_lock(phone):
        try:
            client = create_client(session_path, api_id, api_hash, proxy)
            await client.connect()
            return await terminate_other_sessions(client)
        except Exception as e:
            logger.warning("logout_others_for_phone +%s: %s", phone, e)
            return LogoutResult(status="error", error=f"{type(e).__name__}: {e}")
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass


# ══════════════════════════════════════════════════════════════════════════════
#  RETRY QUEUE
# ══════════════════════════════════════════════════════════════════════════════
#
# Entry: {"phone", "retry_at", "chat_id", "notify", "attempts"}
#   retry_at — unix ts when the restriction expires
#   chat_id  — who to notify (0 = silent)
#   notify   — False for Advanced Gen / global-settings runs that stay quiet
#              until they finally succeed

async def get_pending() -> list[dict]:
    return await db.sched_get(_PENDING_KEY, []) or []


async def schedule_retry(phone: str, retry_after: int, chat_id: int = 0, notify: bool = True) -> int:
    """
    Queue (or re-queue) a phone for a retry once its restriction expires.
    Returns the unix timestamp of the scheduled attempt.
    """
    phone = phone.lstrip("+")
    retry_at = int(time.time()) + max(60, int(retry_after))

    async with _queue_lock:
        pending = await get_pending()
        for entry in pending:
            if entry.get("phone") == phone:
                entry["retry_at"] = retry_at
                entry["chat_id"] = chat_id or entry.get("chat_id", 0)
                entry["notify"] = bool(notify or entry.get("notify"))
                entry["attempts"] = int(entry.get("attempts", 0))
                break
        else:
            pending.append({
                "phone": phone,
                "retry_at": retry_at,
                "chat_id": chat_id,
                "notify": notify,
                "attempts": 0,
            })
        await db.sched_set(_PENDING_KEY, pending)

    logger.info("Auto-logout retry queued for +%s in %s", phone, format_wait(retry_after))
    return retry_at


async def drop_pending(phone: str) -> None:
    """Remove a phone from the retry queue."""
    phone = phone.lstrip("+")
    async with _queue_lock:
        pending = await get_pending()
        remaining = [e for e in pending if e.get("phone") != phone]
        if len(remaining) != len(pending):
            await db.sched_set(_PENDING_KEY, remaining)


async def _bump_attempts(phone: str, retry_after: int) -> None:
    """Reschedule a still-restricted entry, preserving its attempt counter."""
    phone = phone.lstrip("+")
    async with _queue_lock:
        pending = await get_pending()
        for entry in pending:
            if entry.get("phone") == phone:
                entry["retry_at"] = int(time.time()) + max(60, int(retry_after))
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
                break
        await db.sched_set(_PENDING_KEY, pending)


async def process_pending_retries(bot) -> None:
    """
    Run every due retry. Called once per scheduler tick.

    On success the entry is dropped and the requester notified; if Telegram
    still restricts it, the entry is rescheduled with the new countdown.
    """
    now = int(time.time())
    due = [e for e in await get_pending() if int(e.get("retry_at", 0)) <= now]

    for entry in due:
        phone = entry.get("phone", "")
        chat_id = int(entry.get("chat_id", 0) or 0)
        notify = bool(entry.get("notify", True))

        result = await logout_others_for_phone(phone)

        if result.status == "restricted":
            await _bump_attempts(phone, result.retry_after)
            logger.info(
                "Auto-logout still restricted for +%s — retrying in %s",
                phone, format_wait(result.retry_after),
            )
            continue

        await drop_pending(phone)

        if not chat_id:
            continue

        if result.status == "done":
            await _notify(bot, chat_id,
                f"✅ <b>Auto Log Out completed</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📱 <code>+{phone}</code>\n"
                f"🚪 Terminated: <b>{result.terminated}</b> session(s)\n\n"
                f"⭐ Only the bot's session remains active."
            )
        elif result.status == "nothing":
            if notify:
                await _notify(bot, chat_id,
                    f"ℹ️ <b>Auto Log Out</b>\n\n"
                    f"📱 <code>+{phone}</code>\n"
                    f"No other sessions were left to terminate."
                )
        elif result.status == "unauthorized":
            await _notify(bot, chat_id,
                f"⚠️ <b>Auto Log Out failed</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📱 <code>+{phone}</code>\n"
                f"The bot's own session was terminated, so the other sessions "
                f"could not be logged out."
            )
        else:
            await _notify(bot, chat_id,
                f"❌ <b>Auto Log Out failed</b>\n\n"
                f"📱 <code>+{phone}</code>\n"
                f"Error: <code>{result.error}</code>"
            )


async def _notify(bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        logger.warning("Auto-logout notify failed for %s: %s", chat_id, e)


# ══════════════════════════════════════════════════════════════════════════════
#  SWEEP ALL SESSIONS
# ══════════════════════════════════════════════════════════════════════════════

async def sweep_all_sessions(bot=None, chat_id: int = 0, concurrency: int = 5) -> dict:
    """
    Run Auto Log Out across every stored session.

    Restricted accounts are queued for an automatic retry. Returns a summary
    dict for display: cleaned / clean / restricted / dead / errors / total.
    """
    all_sessions = get_all_sessions()
    phones = [p for phones in all_sessions.values() for p in phones]
    if not phones:
        return {"total": 0, "cleaned": 0, "terminated": 0, "clean": 0,
                "restricted": 0, "dead": 0, "errors": 0}

    summary = {"total": len(phones), "cleaned": 0, "terminated": 0, "clean": 0,
               "restricted": 0, "dead": 0, "errors": 0}
    sem = asyncio.Semaphore(concurrency)

    async def _one(phone: str):
        async with sem:
            result = await logout_others_for_phone(phone)

        if result.status == "done":
            summary["cleaned"] += 1
            summary["terminated"] += result.terminated
        elif result.status == "nothing":
            summary["clean"] += 1
        elif result.status == "restricted":
            summary["restricted"] += 1
            await schedule_retry(phone, result.retry_after, chat_id=chat_id, notify=False)
        elif result.status == "unauthorized":
            summary["dead"] += 1
            try:
                await db.set_account_status(phone, "died")
            except Exception:
                pass
        else:
            summary["errors"] += 1

    await asyncio.gather(*(_one(p) for p in phones))
    await db.sched_set("last_logout_ts", int(time.time()))
    return summary
