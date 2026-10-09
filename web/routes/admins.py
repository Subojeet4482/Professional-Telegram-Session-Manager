"""
Admins API routes — list, add, remove admins.

Privilege model: reading the admin list is available to any authenticated
admin; MUTATING the admin list is superadmin-only. Previously every route here
required nothing beyond a valid session, so any admin — including 'readonly' —
could POST themselves a 'superadmin' role and take over the deployment.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from web.auth import PanelUser, require_auth, require_superadmin
from handlers.common import ALL_ROLES, get_primary_admin
import database.database as db

logger = logging.getLogger("web.admins")
router = APIRouter(prefix="/api/admins", tags=["admins"])


class AdminCreate(BaseModel):
    user_id: int
    role: str = "admin"

    @field_validator("user_id")
    @classmethod
    def _positive_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("user_id must be a positive Telegram user ID")
        return v

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ALL_ROLES:
            raise ValueError(f"Invalid role. Must be one of: {', '.join(ALL_ROLES)}")
        return v


@router.get("")
async def list_admins(user: PanelUser = Depends(require_auth)):
    """List all admins. Any authenticated admin may read this."""
    admins = await db.get_all_admins()
    return {"admins": admins}


@router.post("")
async def add_admin(data: AdminCreate, user: PanelUser = Depends(require_superadmin)):
    """Add a new admin. Superadmin only."""
    await db.add_admin(data.user_id, data.role)
    await db.log_action(user.user_id, "web_add_admin", f"user_id={data.user_id} role={data.role}")
    logger.info("Admin %s added %s (%s) via web", user.user_id, data.user_id, data.role)
    return {"status": "ok", "message": f"Admin {data.user_id} added with role {data.role}"}


@router.delete("/{user_id}")
async def remove_admin(user_id: int, user: PanelUser = Depends(require_superadmin)):
    """Remove an admin. Superadmin only."""
    # The ADMIN_ID owner is the root of trust — removing them from the DB would
    # not actually revoke them (env always wins) but would corrupt the UI state.
    if user_id == get_primary_admin():
        raise HTTPException(status_code=400, detail="The primary admin cannot be removed")

    # Refuse self-removal: it is almost always a mistake and can strand the
    # deployment with zero reachable superadmins.
    if user_id == user.user_id:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access")

    role = await db.get_admin_role(user_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Admin not found")

    await db.remove_admin(user_id)
    # A removed admin's existing browser session/tokens must not keep working
    # for the rest of their 24h lifetime — otherwise "remove admin" is
    # cosmetic until the session naturally expires.
    revoked_sessions = await db.delete_web_sessions_for_user(user_id)
    revoked_tokens = await db.delete_web_tokens_for_user(user_id)
    await db.log_action(user.user_id, "web_remove_admin", f"user_id={user_id}")
    logger.info(
        "Admin %s removed %s via web (revoked %d session(s), %d token(s))",
        user.user_id, user_id, revoked_sessions, revoked_tokens,
    )
    return {"status": "ok", "message": f"Admin {user_id} removed"}
