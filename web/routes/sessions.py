"""
Sessions API routes — list, detail, logout, delete, export.
"""

import os
import asyncio
import shutil
import zipfile
import tempfile
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

from web.auth import PanelUser, require_auth, require_write, require_superadmin
import database.database as db
from utils.country_utils import (
    get_all_sessions, get_country_display, SESSIONS_DIR,
)
from utils.session_lock import session_lock
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client, get_last_otp

logger = logging.getLogger("web.sessions")
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(user: PanelUser = Depends(require_auth)):
    """List all sessions grouped by country."""
    all_sessions = get_all_sessions()
    all_account = await db.get_all_account_statuses()
    all_contact = await db.get_all_contact_statuses()
    all_spam = await db.get_all_spam_statuses()
    all_2fa = await db.get_all_2fa()

    result = {}
    for folder, phones in all_sessions.items():
        flag, name = get_country_display(folder)
        result[folder] = {
            "flag": flag,
            "name": name,
            "phones": [
                {
                    "phone": p,
                    "account_status": all_account.get(p, "Unknown"),
                    "contact_status": all_contact.get(p, "Unknown"),
                    "spam_status": all_spam.get(p, "Unknown"),
                    "has_2fa": p in all_2fa,
                }
                for p in phones
            ],
        }
    return result


@router.get("/{phone}")
async def session_detail(phone: str, user: PanelUser = Depends(require_auth)):
    """Get details for a specific session."""
    all_sessions = get_all_sessions()
    folder = None
    for f, phones in all_sessions.items():
        if phone in phones:
            folder = f
            break
    if not folder:
        raise HTTPException(status_code=404, detail="Session not found")

    flag, name = get_country_display(folder)
    account = await db.get_account_status(phone)
    contact = await db.get_contact_status(phone)
    spam = await db.get_spam_status(phone)

    return {
        "phone": phone,
        "country": name,
        "flag": flag,
        "folder": folder,
        "account_status": account,
        "contact_status": contact,
        "spam_status": spam,
    }


class PhoneAction(BaseModel):
    phone: str


@router.post("/{phone}/logout")
async def logout_session(phone: str, user: PanelUser = Depends(require_write)):
    """Log out a Telegram session."""
    session_file = _find_session_file(phone)
    if not session_file:
        raise HTTPException(status_code=404, detail="Session not found")

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    client = None

    try:
        async with session_lock(phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()
            await client.disconnect()
            client = None
            await asyncio.sleep(0.5)

            # Delete local files
            for ext in [".session", ".session-journal"]:
                path = session_file + ext
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except PermissionError:
                        await asyncio.sleep(1)
                        try:
                            os.remove(path)
                        except Exception:
                            pass

        await db.delete_phone_status(phone)
        _cleanup_empty_folder(session_file)
        await db.log_action(user.user_id, "web_logout", f"+{phone}")

        return {"status": "ok", "message": f"+{phone} logged out successfully"}

    except Exception as e:
        logger.exception("Logout error for +%s", phone)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="Logout failed")


@router.delete("/{phone}")
async def delete_session(phone: str, user: PanelUser = Depends(require_write)):
    """Delete a session file and DB status."""
    if not os.path.exists(SESSIONS_DIR):
        raise HTTPException(status_code=404, detail="Session not found")

    deleted = False
    async with session_lock(phone):
        for folder in os.listdir(SESSIONS_DIR):
            folder_path = os.path.join(SESSIONS_DIR, folder)
            if not os.path.isdir(folder_path):
                continue
            session_file = os.path.join(folder_path, f"{phone}.session")
            if os.path.exists(session_file):
                for path in [session_file, session_file + "-journal"]:
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except PermissionError:
                            await asyncio.sleep(1)
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                deleted = True
                await db.delete_phone_status(phone)
                # Remove folder if empty
                remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
                if not remaining:
                    try:
                        os.rmdir(folder_path)
                    except OSError:
                        pass
                break

    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.log_action(user.user_id, "web_delete", f"+{phone}")
    return {"status": "ok", "message": f"+{phone} deleted"}


@router.get("/export/all")
async def export_all_sessions(user: PanelUser = Depends(require_superadmin)):
    """
    Export all sessions as a ZIP file. Superadmin only.

    This returns every session file in the deployment — each one a complete,
    password-less account credential — so it is the single most damaging route
    in the panel and is restricted to the highest tier.
    """
    all_sessions = get_all_sessions()
    if not all_sessions:
        raise HTTPException(status_code=404, detail="No sessions to export")

    # Private, unguessable directory (mkdtemp -> mode 0700) rather than a fixed
    # /tmp/web_export_all.zip that any local user could read, pre-create as a
    # symlink, or find lingering after the download.
    tmp_dir = tempfile.mkdtemp(prefix="web_export_")
    tmp_zip = os.path.join(tmp_dir, "sessions_export.zip")
    total = 0
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, phones in all_sessions.items():
                for phone in phones:
                    sf = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                    if os.path.exists(sf):
                        zf.write(sf, f"{folder}/{phone}.session")
                        total += 1

        if total == 0:
            raise HTTPException(status_code=404, detail="No session files found")

        await db.log_action(user.user_id, "web_export", f"{total} sessions")
        # BackgroundTask runs after the response body has been fully sent, so
        # the archive exists exactly as long as the download needs it.
        return FileResponse(
            tmp_zip,
            media_type="application/zip",
            filename="sessions_export.zip",
            background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
        )
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("Export failed")
        raise HTTPException(status_code=500, detail="Export failed")

from telethon.tl.functions.account import GetPasswordRequest

@router.get("/{phone}/2fa")
async def get_2fa_password(phone: str, user: PanelUser = Depends(require_write)):
    """Fetch 2FA password for a session and verify its status."""
    session_file = _find_session_file(phone)
    if not session_file:
        raise HTTPException(status_code=404, detail="Session not found")

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    client = None

    try:
        async with session_lock(phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                client = None
                raise HTTPException(status_code=400, detail="Session not authorized")

            pwd_info = await client(GetPasswordRequest())
            await client.disconnect()
            client = None

        if not pwd_info.has_password:
            return {"found": True, "password": "Not Enabled (No 2FA on this account)"}

        # It has a password, let's check our database
        password = await db.get_2fa(phone)
        if password:
            await db.log_action(user.user_id, "web_view_2fa", f"+{phone}")
            return {"found": True, "password": password}

        return {"found": False, "message": "2FA is Enabled, but password was not captured/saved."}

    except HTTPException:
        raise
    except Exception:
        logger.exception("2FA check error for +%s", phone)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="Failed to check 2FA status")


@router.get("/{phone}/otp")
async def get_otp(phone: str, user: PanelUser = Depends(require_write)):
    """Fetch last OTP code for a session."""
    session_file = _find_session_file(phone)
    if not session_file:
        raise HTTPException(status_code=404, detail="Session not found")

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    client = None

    try:
        async with session_lock(phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                client = None
                raise HTTPException(status_code=400, detail="Session not authorized")

            found, message = await get_last_otp(client)
            await client.disconnect()
            client = None

        if found:
            await db.log_action(user.user_id, "web_view_otp", f"+{phone}")
        return {"found": found, "message": message}

    except HTTPException:
        raise
    except Exception:
        logger.exception("OTP error for +%s", phone)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="OTP fetch failed")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_session_file(phone: str) -> str | None:
    """Find session file path (without .session extension) for a phone."""
    if not os.path.exists(SESSIONS_DIR):
        return None
    for folder in os.listdir(SESSIONS_DIR):
        folder_path = os.path.join(SESSIONS_DIR, folder)
        if not os.path.isdir(folder_path):
            continue
        candidate = os.path.join(folder_path, f"{phone}.session")
        if os.path.exists(candidate):
            return os.path.join(folder_path, phone)
    return None


def _cleanup_empty_folder(session_file: str) -> None:
    """Remove the parent folder if it's empty after a session is removed."""
    folder_path = os.path.dirname(session_file)
    if os.path.exists(folder_path):
        remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
        if not remaining:
            try:
                os.rmdir(folder_path)
            except OSError:
                pass
