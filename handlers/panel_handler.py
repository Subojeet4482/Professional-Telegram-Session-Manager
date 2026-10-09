"""
Panel Handler — Generates one-time login tokens for the web panel.
"""

import os
import secrets
import time
import logging

import aiohttp
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from utils.utils import smart_edit
import database.database as db
from handlers.common import is_admin_async

router = Router()
logger = logging.getLogger(__name__)

TOKEN_EXPIRY = 600  # 10 minutes
PANEL_PORT = int(os.getenv("PANEL_PORT", "8080"))


async def _get_panel_url() -> str:
    """Determine the correct base URL for the panel."""
    # Railway public domain support
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if railway_domain:
        return f"https://{railway_domain}"

    # Fetch server's public IP from ipify API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.ipify.org?format=json",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json()
                ip = data.get("ip")
                if ip:
                    return f"http://{ip}:{PANEL_PORT}"
    except Exception:
        pass

    # Fallback to localhost if ipify fails
    return f"http://localhost:{PANEL_PORT}"


def _panel_kb(has_token: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔑 Generate New Token", callback_data="panel_gen_token")],
    ]
    if has_token:
        rows.insert(0, [InlineKeyboardButton(text="🔄 Refresh Info", callback_data="panel")])
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "panel")
async def cb_panel(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer()

    panel_url = await _get_panel_url()

    text = (
        "🌐 <b>Web Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 Panel URL: <code>{panel_url}</code>\n\n"
        "🔑 Generate a one-time login token below.\n"
        "⏱ Tokens expire after <b>10 minutes</b> and can only be used <b>once</b>.\n\n"
        "🔒 Do not share the token with anyone!"
    )
    await smart_edit(callback.message, text, reply_markup=_panel_kb(), parse_mode="HTML")


@router.callback_query(F.data == "panel_gen_token")
async def cb_gen_token(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer()

    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expires_at = now + TOKEN_EXPIRY

    # Bind the token to the issuing admin so the resulting web session inherits
    # their identity and role. Panel privileges now mirror bot privileges.
    await db.create_web_token(token, expires_at, callback.from_user.id)

    panel_url = await _get_panel_url()
    login_url = f"{panel_url}/login"

    # The token is deliberately NOT embedded in the URL. A credential in a query
    # string leaks into browser history, Referer headers sent to third-party
    # origins, reverse-proxy logs and uvicorn access logs. It is delivered as a
    # separate copyable block and submitted in the POST body instead.
    text = (
        "✅ <b>Token Generated!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 Panel: <code>{login_url}</code>\n\n"
        "🔑 Login token (tap to copy):\n"
        f"<code>{token}</code>\n\n"
        "⏱ Expires in: <b>10 minutes</b>\n"
        "🔑 One-time use only\n\n"
        "🔒 <b>Paste the token into the login page. Never share it.</b>"
    )

    inline_kb = []

    # Telegram rejects localhost/127.0.0.1 in inline buttons, so only add it if it's a public URL
    if "localhost" not in login_url and "127.0.0.1" not in login_url and "Unknown" not in login_url:
        inline_kb.append([InlineKeyboardButton(text="🌐 Open Panel", url=login_url)])

    inline_kb.extend([
        [InlineKeyboardButton(text="🔑 Generate New Token", callback_data="panel_gen_token")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="panel")],
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=inline_kb)
    await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
