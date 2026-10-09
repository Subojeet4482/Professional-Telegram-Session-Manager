"""
Web Panel Authentication — one-time token + session cookie system.

Identity model
--------------
A login token is minted in the bot by a specific Telegram admin and is stored
WITH that admin's user_id. Logging in copies the user_id onto the browser
session, so every request can be resolved back to a real Telegram user and to
their role.

This is what makes role enforcement possible. Previously tokens and sessions
were anonymous, so `require_auth` could only answer "is this a valid session?"
and every route was therefore effectively superadmin — a 'readonly' admin who
generated a token could add themselves as a superadmin via the panel API.
"""

import secrets
import time
import logging
from dataclasses import dataclass

from fastapi import Request, HTTPException, Depends

import database.database as db
from handlers.common import resolve_role, WRITE_ROLES

logger = logging.getLogger("web.auth")

SESSION_COOKIE = "panel_session"
SESSION_DURATION = 86400  # 24 hours


@dataclass(frozen=True)
class PanelUser:
    """The authenticated identity behind a panel request."""

    user_id: int
    role: str

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES


async def validate_token_and_login(token: str, request: Request) -> str:
    """Validate one-time token, create session, return session_id."""
    record = await db.get_web_token(token)
    now = int(time.time())

    if not record:
        raise HTTPException(status_code=403, detail="Invalid token")
    if record["used"]:
        raise HTTPException(status_code=403, detail="Token already used")
    if record["expires_at"] < now:
        raise HTTPException(status_code=403, detail="Token expired")

    # Legacy tokens minted before tokens carried an identity cannot be mapped
    # to a user, so they cannot be role-checked. Reject rather than guess.
    user_id = int(record.get("user_id") or 0)
    if not user_id:
        raise HTTPException(status_code=403, detail="Token is no longer valid — please generate a new one")

    # The issuer must still be an admin at redemption time, not just at
    # issuance time: a token minted before a demotion must not outlive it.
    role = await resolve_role(user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="Access denied")

    # Mark as used immediately
    await db.mark_token_used(token)

    # Create browser session
    session_id = secrets.token_urlsafe(32)
    user_agent = request.headers.get("user-agent", "")
    await db.create_web_session(session_id, now + SESSION_DURATION, user_agent, user_id)
    logger.info(
        "New web session for user %s (role=%s) from %s",
        user_id, role, request.client.host if request.client else "unknown",
    )
    return session_id


async def require_auth(request: Request) -> PanelUser:
    """
    Resolve the caller's identity from the session cookie.

    Raises 401 when unauthenticated/expired, 403 when the underlying Telegram
    user is no longer an admin. Returns a PanelUser — routes should annotate
    the dependency so they can use `.user_id` for audit logging.
    """
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    record = await db.get_web_session(session_id)
    now = int(time.time())
    if not record or record["expires_at"] < now:
        raise HTTPException(status_code=401, detail="Session expired")

    user_id = int(record.get("user_id") or 0)
    if not user_id:
        # Legacy anonymous session from before identity binding existed.
        await db.delete_web_session(session_id)
        raise HTTPException(status_code=401, detail="Session expired")

    # The cookie is bearer-token-equivalent: anyone who obtains it (XSS, a
    # shared/leaked log line, a proxy) can replay it. Pinning it to the
    # User-Agent seen at login is not proof of identity — a stolen cookie can
    # be replayed with a forged header too — but it stops the common case of
    # a cookie copied verbatim into a different browser/tool, at zero cost to
    # legitimate users whose browser doesn't change mid-session.
    stored_ua = record.get("user_agent") or ""
    current_ua = request.headers.get("user-agent", "")
    if stored_ua and stored_ua != current_ua:
        await db.delete_web_session(session_id)
        logger.warning(
            "Session %s… rejected: user-agent mismatch (user=%s)",
            session_id[:8], user_id,
        )
        raise HTTPException(status_code=401, detail="Session expired")

    # Re-resolve the role on every request so a demotion or removal takes
    # effect immediately instead of at session expiry.
    role = await resolve_role(user_id)
    if role is None:
        await db.delete_web_session(session_id)
        logger.warning("Session %s… rejected: user %s is no longer an admin", session_id[:8], user_id)
        raise HTTPException(status_code=403, detail="Access denied")

    return PanelUser(user_id=user_id, role=role)


def require_role(*roles: str):
    """
    Dependency factory enforcing that the caller holds one of `roles`.

    Usage:
        @router.post("")
        async def add_admin(user: PanelUser = Depends(require_superadmin)):
    """
    allowed = frozenset(roles)

    async def _dependency(user: PanelUser = Depends(require_auth)) -> PanelUser:
        if user.role not in allowed:
            logger.warning(
                "Denied %s (role=%s): requires one of %s",
                user.user_id, user.role, sorted(allowed),
            )
            raise HTTPException(status_code=403, detail="Insufficient privileges")
        return user

    return _dependency


# Ready-made dependencies for the two privilege tiers.
require_write = require_role(*WRITE_ROLES)          # superadmin + admin
require_superadmin = require_role("superadmin")     # superadmin only
