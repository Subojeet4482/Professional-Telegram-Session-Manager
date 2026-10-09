"""
Scheduler API routes — get/toggle/run scheduler features.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from web.auth import PanelUser, require_auth, require_write
import database.database as db

logger = logging.getLogger("web.scheduler")
router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


_DEFAULTS = {
    "auto_check_enabled":   False,
    "auto_check_interval":  12,
    "daily_report_enabled": False,
    "daily_report_hour":    9,
    "auto_backup_enabled":  False,
    "auto_backup_interval": 7,
}


async def _get(key: str):
    return await db.sched_get(key, _DEFAULTS.get(key))


@router.get("")
async def get_scheduler(user: PanelUser = Depends(require_auth)):
    """Get all scheduler settings."""
    return {
        "auto_check_enabled":   await _get("auto_check_enabled"),
        "auto_check_interval":  await _get("auto_check_interval"),
        "daily_report_enabled": await _get("daily_report_enabled"),
        "daily_report_hour":    await _get("daily_report_hour"),
        "auto_backup_enabled":  await _get("auto_backup_enabled"),
        "auto_backup_interval": await _get("auto_backup_interval"),
        "last_check_ts":        await db.sched_get("last_check_ts", 0),
        "last_backup_ts":       await db.sched_get("last_backup_ts", 0),
        "last_report_ts":       await db.sched_get("last_report_ts", 0),
    }


@router.post("/toggle/{feature}")
async def toggle_feature(feature: str, user: PanelUser = Depends(require_write)):
    """Toggle a scheduler feature on/off."""
    key_map = {
        "auto_check":   "auto_check_enabled",
        "daily_report": "daily_report_enabled",
        "auto_backup":  "auto_backup_enabled",
    }
    key = key_map.get(feature)
    if not key:
        raise HTTPException(status_code=400, detail=f"Unknown feature: {feature}")

    current = await _get(key)
    new_val = not current
    await db.sched_set(key, new_val)
    await db.log_action(user.user_id, "web_scheduler_toggle", f"{feature}={'enabled' if new_val else 'disabled'}")

    return {"status": "ok", "feature": feature, "enabled": new_val}


@router.post("/interval/{feature}")
async def set_interval(feature: str, value: int, user: PanelUser = Depends(require_write)):
    """Set interval for a scheduler feature."""
    key_map = {
        "auto_check":   "auto_check_interval",
        "daily_report": "daily_report_hour",
        "auto_backup":  "auto_backup_interval",
    }
    key = key_map.get(feature)
    if not key:
        raise HTTPException(status_code=400, detail=f"Unknown feature: {feature}")

    await db.sched_set(key, value)
    await db.log_action(user.user_id, "web_scheduler_interval", f"{feature}={value}")

    return {"status": "ok", "feature": feature, "value": value}
