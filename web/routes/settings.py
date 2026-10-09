"""
Settings API routes — proxy, API credentials, profile settings.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.auth import PanelUser, require_auth, require_write, require_superadmin
import database.database as db
from config.config_manager import (
    get_proxy, set_proxy,
    get_api_credentials, set_api_id, set_api_hash,
    get_api_platform, set_api_platform,
    get_profile_settings, set_profile_settings,
)
from utils.device_profiles import (
    CUSTOM, DESKTOP, PLATFORM_LABELS, build_profile, describe,
)

logger = logging.getLogger("web.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── Models ────────────────────────────────────────────────────────────────────

class ProxyUpdate(BaseModel):
    enabled: bool = False
    type: str = "socks5"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    rdns: bool = True


class ApiUpdate(BaseModel):
    api_id: int | None = None
    api_hash: str | None = None
    # "desktop" (official api_id 2040) or "custom".
    # Applied before api_id/api_hash so an explicit custom pair still wins.
    platform: str | None = None


class ProfileUpdate(BaseModel):
    auto_username: bool = False
    auto_name: bool = False
    auto_photo: bool = False
    auto_bio: bool = False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def get_all_settings(user: PanelUser = Depends(require_auth)):
    """Get all settings: proxy, API, profile."""
    proxy = await get_proxy()
    api_id, api_hash = await get_api_credentials()
    platform = await get_api_platform()
    profile = await get_profile_settings()

    # Mask sensitive data
    masked_hash = api_hash[:4] + "..." + api_hash[-4:] if len(api_hash) > 8 else "****"
    proxy_display = dict(proxy)
    if proxy_display.get("password"):
        proxy_display["password"] = "****"

    return {
        "proxy": proxy_display,
        "api": {
            "api_id": api_id,
            "api_hash": masked_hash,
            "platform": platform,
            "platform_label": PLATFORM_LABELS.get(platform, platform),
            # Example only — every account derives its own from its number.
            "device_sample": describe(build_profile(platform, seed="sample")),
        },
        "profile": profile,
    }


@router.post("/proxy")
async def update_proxy(data: ProxyUpdate, user: PanelUser = Depends(require_superadmin)):
    """Update proxy settings. Superadmin only — controls all outbound traffic."""
    proxy = {
        "enabled": data.enabled,
        "type": data.type,
        "host": data.host,
        "port": data.port,
        "username": data.username,
        "password": data.password,
        "rdns": data.rdns,
    }
    await set_proxy(proxy)
    await db.log_action(user.user_id, "web_update_proxy", f"enabled={data.enabled} host={data.host}")
    return {"status": "ok", "message": "Proxy settings updated"}


@router.post("/api")
async def update_api(data: ApiUpdate, user: PanelUser = Depends(require_superadmin)):
    """Update the API platform and/or ID and Hash. Superadmin only — these are credentials."""
    if data.platform is not None:
        if data.platform not in (DESKTOP, CUSTOM):
            raise HTTPException(status_code=400, detail=f"Unknown platform: {data.platform}")
        await set_api_platform(data.platform)
    if data.api_id is not None:
        await set_api_id(data.api_id)
    if data.api_hash is not None:
        await set_api_hash(data.api_hash)
    await db.log_action(
        user.user_id, "web_update_api",
        f"platform={await get_api_platform()} credentials updated",
    )
    return {"status": "ok", "message": "API settings updated"}


@router.post("/profile")
async def update_profile(data: ProfileUpdate, user: PanelUser = Depends(require_write)):
    """Update profile auto-fill settings."""
    profile = {
        "auto_username": data.auto_username,
        "auto_name": data.auto_name,
        "auto_photo": data.auto_photo,
        "auto_bio": data.auto_bio,
    }
    await set_profile_settings(profile)
    await db.log_action(user.user_id, "web_update_profile", str(profile))
    return {"status": "ok", "message": "Profile settings updated"}
