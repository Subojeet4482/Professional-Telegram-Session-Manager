"""
Shared utilities for handlers — admin check, action logging.
Supports:
  - Primary admin from ADMIN_ID env (always trusted)
  - Additional admins stored in database (multi-admin)

IMPORTANT: Always use is_admin_async() in handlers.
           is_admin() is kept only as a fast-path for the primary admin
           and will NOT catch DB-only admins.

SECURITY: every check in this module is fail-CLOSED. An unset, malformed or
non-positive ADMIN_ID grants nobody access — it must never grant everybody
access. ADMIN_ID is mandatory and is validated at startup by
require_admin_id_configured(); the checks below stay defensive anyway so a
future code path that skips startup validation cannot reopen the hole.
"""

import os
import database.database as db

# Roles permitted to perform state-changing actions. Allow-list, not a
# deny-list: an unrecognised role in the DB is treated as untrusted.
WRITE_ROLES = ("superadmin", "admin")
ALL_ROLES = ("superadmin", "admin", "readonly")


class AdminConfigError(RuntimeError):
    """Raised at startup when ADMIN_ID is missing or invalid."""


def get_primary_admin() -> int:
    """
    Get the primary admin ID from env.

    Returns 0 when unset, non-numeric, or non-positive. Callers MUST treat 0
    as "no primary admin configured" and deny — never as a wildcard.
    """
    raw = (os.getenv("ADMIN_ID") or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return 0
    return value if value > 0 else 0


# Backwards-compatible private alias (kept so older imports keep working).
_get_primary_admin = get_primary_admin


def require_admin_id_configured() -> int:
    """
    Validate that ADMIN_ID is usable. Call once at startup.

    Raises AdminConfigError so the process refuses to boot rather than running
    with an authorization gate that denies everyone (or, historically, one
    that allowed everyone).
    """
    primary = get_primary_admin()
    if not primary:
        raise AdminConfigError(
            "ADMIN_ID is not set to a valid positive Telegram user ID. "
            "Set ADMIN_ID in the environment (or .env) before starting. "
            "Refusing to start: without it no admin check can succeed."
        )
    return primary


# Sync fast-path — ONLY checks primary admin from env
def is_admin(user_id: int) -> bool:
    """
    Sync check: allows ONLY the primary admin (ADMIN_ID).
    ⚠️  Does NOT check DB admins — use is_admin_async() for full check.
    """
    primary = get_primary_admin()
    return bool(primary) and user_id == primary


async def is_admin_async(user_id: int) -> bool:
    """
    Full async check: primary admin OR any admin registered in DB.
    Always use this in handlers for read access.
    """
    # Fast-path: primary admin from env
    primary = get_primary_admin()
    if primary and user_id == primary:
        return True

    # Check DB admins (multi-admin support)
    try:
        return await db.is_admin_db(user_id)
    except Exception:
        # DB unavailable → deny. Never fall back to "allow".
        return False


async def is_write_admin_async(user_id: int) -> bool:
    """
    Write check: primary admin, or a DB admin whose role is in WRITE_ROLES.
    'readonly' and any unrecognised role are denied.
    """
    primary = get_primary_admin()
    if primary and user_id == primary:
        return True

    try:
        role = await db.get_admin_role(user_id)
        return role in WRITE_ROLES
    except Exception:
        return False


async def is_superadmin_async(user_id: int) -> bool:
    """Superadmin check: primary admin, or a DB admin with role 'superadmin'."""
    primary = get_primary_admin()
    if primary and user_id == primary:
        return True

    try:
        return await db.get_admin_role(user_id) == "superadmin"
    except Exception:
        return False


async def resolve_role(user_id: int) -> str | None:
    """
    Effective role for a user, or None if they are not an admin at all.

    The primary admin always resolves to 'superadmin' regardless of what the
    DB says, so ADMIN_ID cannot be locked out by a bad DB row.
    """
    primary = get_primary_admin()
    if primary and user_id == primary:
        return "superadmin"

    try:
        role = await db.get_admin_role(user_id)
    except Exception:
        return None

    return role if role in ALL_ROLES else None


async def log_action(user_id: int, action: str, detail: str = "") -> None:
    """Log an admin action to the database."""
    try:
        await db.log_action(user_id, action, detail)
    except Exception:
        pass  # Never crash a handler because of logging
