"""
Settings handler — Proxy management, API credentials & Profile auto-fill.
"""

import logging
from urllib.parse import urlparse

from aiogram import Router, F
from utils.utils import smart_edit
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ui.states import ProxyState, ApiState, Auto2FAState
from ui.keyboards import (
    settings_menu_kb, proxy_kb, api_kb, profile_kb, auto_2fa_kb,
    auto_logout_kb, cancel_kb, back_menu_kb,
)
from config.config_manager import (
    get_proxy, set_proxy,
    get_api_credentials, set_api_id, set_api_hash,
    get_api_platform, set_api_platform,
    get_profile_settings, set_profile_settings,
    get_auto_2fa, set_auto_2fa,
    get_auto_logout, set_auto_logout,
)
from utils.device_profiles import DESKTOP, PLATFORM_LABELS, build_profile, describe
from workers.session_worker import test_proxy_connection
from handlers.common import is_admin_async, is_write_admin_async

router = Router()
logger = logging.getLogger(__name__)


# ─── Settings Menu ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "set")
async def cb_settings(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    text = (
        "⚙️ <b>Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose a setting to manage:"
    )
    await smart_edit(callback.message, text, reply_markup=settings_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  PROXY SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "prx")
async def cb_proxy_view(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    proxy = await get_proxy()
    is_enabled = proxy.get("enabled", True)
    status_text = "🟢 Enabled" if is_enabled else "🔴 Disabled"

    text = (
        "🌐 <b>Proxy Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 Status: <b>{status_text}</b>\n"
        f"🔹 Type: <code>{proxy.get('type', 'socks5')}</code>\n"
        f"🔹 Host: <code>{proxy.get('host', '—')}</code>\n"
        f"🔹 Port: <code>{proxy.get('port', '—')}</code>\n"
        f"🔹 User: <code>{proxy.get('username', '—')}</code>\n"
        f"🔹 Pass: <code>{proxy.get('password', '—')}</code>\n"
        f"🔹 RDNS: <code>{proxy.get('rdns', True)}</code>"
    )
    await smart_edit(callback.message, text, reply_markup=proxy_kb(is_enabled), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "ptgl")
async def cb_proxy_toggle(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    proxy = await get_proxy()
    proxy["enabled"] = not proxy.get("enabled", True)
    await set_proxy(proxy)
    
    # Re-fetch for display
    proxy = await get_proxy()
    is_enabled = proxy.get("enabled", True)
    status_text = "🟢 Enabled" if is_enabled else "🔴 Disabled"

    text = (
        "🌐 <b>Proxy Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 Status: <b>{status_text}</b>\n"
        f"🔹 Type: <code>{proxy.get('type', 'socks5')}</code>\n"
        f"🔹 Host: <code>{proxy.get('host', '—')}</code>\n"
        f"🔹 Port: <code>{proxy.get('port', '—')}</code>\n"
        f"🔹 User: <code>{proxy.get('username', '—')}</code>\n"
        f"🔹 Pass: <code>{proxy.get('password', '—')}</code>\n"
        f"🔹 RDNS: <code>{proxy.get('rdns', True)}</code>"
    )
    await smart_edit(callback.message, text, reply_markup=proxy_kb(is_enabled), parse_mode="HTML")
    await callback.answer("Proxy state updated")


@router.callback_query(F.data == "pe")
async def cb_proxy_edit(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(ProxyState.waiting)
    text = (
        "✏️ <b>Change Proxy</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the new proxy in this format:\n\n"
        "<code>socks5://user:pass@host:port</code>\n\n"
        "📝 Examples:\n"
        "<code>socks5://user:pass@1.2.3.4:1080</code>\n"
        "<code>socks5://host:port</code> (no auth)\n"
        "<code>http://user:pass@host:port</code>"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(ProxyState.waiting)
async def on_proxy_input(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    raw = message.text.strip()

    try:
        parsed = urlparse(raw)
        proxy_type = parsed.scheme or "socks5"
        host = parsed.hostname
        port = parsed.port

        if not host or not port:
            raise ValueError("Missing host or port")

        current_proxy = await get_proxy()
        new_proxy = {
            "enabled": current_proxy.get("enabled", True),
            "type": proxy_type,
            "host": host,
            "port": int(port),
            "username": parsed.username or "",
            "password": parsed.password or "",
            "rdns": True,
        }
        await set_proxy(new_proxy)
        await state.clear()

        status_text = "🟢 Enabled" if new_proxy["enabled"] else "🔴 Disabled"
        await message.answer(
            f"✅ <b>Proxy updated!</b>\n\n"
            f"🔹 Status: <b>{status_text}</b>\n"
            f"🔹 Type: <code>{proxy_type}</code>\n"
            f"🔹 Host: <code>{host}</code>\n"
            f"🔹 Port: <code>{port}</code>\n"
            f"🔹 User: <code>{new_proxy['username'] or '—'}</code>",
            reply_markup=proxy_kb(new_proxy["enabled"]),
            parse_mode="HTML",
        )

    except Exception as e:
        await message.answer(
            f"❌ <b>Invalid format!</b>\n\n"
            f"Error: <code>{e}</code>\n\n"
            f"Use: <code>socks5://user:pass@host:port</code>",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "pt")
async def cb_proxy_test(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await callback.answer("🧪 Testing proxy...")

    proxy = await get_proxy()
    api_id, api_hash = await get_api_credentials()

    await smart_edit(callback.message, "🧪 <b>Testing proxy connection...</b>",
        parse_mode="HTML",
    )

    success, msg = await test_proxy_connection(api_id, api_hash, proxy)

    text = (
        "🌐 <b>Proxy Test Result</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{msg}"
    )
    is_enabled = proxy.get("enabled", True)
    await smart_edit(callback.message, text, reply_markup=proxy_kb(is_enabled), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════════
#  API SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

async def _api_status_text() -> tuple[str, str]:
    """Render the API settings screen. Returns (text, platform)."""
    platform = await get_api_platform()
    api_id, api_hash = await get_api_credentials()
    masked_hash = api_hash[:6] + "..." + api_hash[-4:] if len(api_hash) > 10 else api_hash

    # Sample fingerprint so the effect of the platform switch is visible before
    # any account is registered with it.
    sample = describe(build_profile(platform, seed="sample"))

    text = (
        "🔑 <b>API Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 Platform: <b>{PLATFORM_LABELS.get(platform, platform)}</b>\n"
        f"🔹 API ID: <code>{api_id}</code>\n"
        f"🔹 API Hash: <code>{masked_hash}</code>\n\n"
        f"🧬 <b>Device fingerprint (example):</b>\n<code>{sample}</code>\n\n"
        "<i>Each account gets its own fingerprint, derived from its phone number "
        "so it stays identical on every reconnect. Setting a custom API ID or "
        "Hash drops the official Telegram Desktop identity — use the button "
        "above to restore it.</i>"
    )
    return text, platform


@router.callback_query(F.data == "api")
async def cb_api_view(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    text, platform = await _api_status_text()
    await smart_edit(callback.message, text, reply_markup=api_kb(platform), parse_mode="HTML")
    await callback.answer()


# ── Restore the official Telegram Desktop identity ────────────────────────────

@router.callback_query(F.data == "apl_desktop")
async def cb_api_platform(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    if await get_api_platform() == DESKTOP:
        await callback.answer("Already active")
        return

    await set_api_platform(DESKTOP)
    text, platform = await _api_status_text()
    await smart_edit(callback.message, text, reply_markup=api_kb(platform), parse_mode="HTML")
    await callback.answer(f"Switched to {PLATFORM_LABELS[DESKTOP]}")


# ── Change API ID ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "ai")
async def cb_api_id_edit(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(ApiState.api_id)
    text = (
        "✏️ <b>Change API ID</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the new API ID (numbers only):\n\n"
        "💡 Get it from <a href='https://my.telegram.org'>my.telegram.org</a>"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(ApiState.api_id)
async def on_api_id_input(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("❌ API ID must be a number!", reply_markup=cancel_kb())
        return

    await set_api_id(int(raw))
    await state.clear()
    await message.answer(
        f"✅ <b>API ID updated to:</b> <code>{raw}</code>\n\n"
        "<i>Platform switched to Custom API. Make sure the API Hash matches, "
        "or use “Restore Desktop (2040)” to go back to the official identity.</i>",
        reply_markup=api_kb(await get_api_platform()),
        parse_mode="HTML",
    )


# ── Change API Hash ───────────────────────────────────────────────────────

@router.callback_query(F.data == "ah")
async def cb_api_hash_edit(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(ApiState.api_hash)
    text = (
        "✏️ <b>Change API Hash</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the new API Hash:\n\n"
        "💡 Get it from <a href='https://my.telegram.org'>my.telegram.org</a>"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(ApiState.api_hash)
async def on_api_hash_input(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    raw = message.text.strip()
    if len(raw) < 10:
        await message.answer("❌ API Hash seems too short!", reply_markup=cancel_kb())
        return

    await set_api_hash(raw)
    await state.clear()
    masked = raw[:6] + "..." + raw[-4:]
    await message.answer(
        f"✅ <b>API Hash updated to:</b> <code>{masked}</code>",
        reply_markup=api_kb(await get_api_platform()),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  PROFILE SETTINGS
#  Each option is a pure toggle — one tap enables, another disables.
#  No user input required. Data is fetched from randomuser.me on registration.
# ═══════════════════════════════════════════════════════════════════════════════

def _profile_status_text(p: dict) -> str:
    u_icon  = "✅" if p.get("auto_username") else "❌"
    n_icon  = "✅" if p.get("auto_name")     else "❌"
    ph_icon = "✅" if p.get("auto_photo")    else "❌"
    b_icon  = "✅" if p.get("auto_bio")      else "❌"
    return (
        "👤 <b>Profile Auto-Fill Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{u_icon} <b>Username</b> — random from API\n"
        f"{n_icon} <b>Name</b> — first + last from API\n"
        f"{ph_icon} <b>Profile Photo</b> — large photo from API\n"
        f"{b_icon} <b>Bio</b> — generated from API data\n\n"
        "Tap any button to enable or disable it:"
    )


async def _toggle(p: dict, key: str) -> bool:
    """Toggle a boolean key and save. Returns new value."""
    p[key] = not p.get(key, False)
    await set_profile_settings(p)
    return p[key]


@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    p = await get_profile_settings()
    await smart_edit(callback.message, _profile_status_text(p),
        reply_markup=profile_kb(p),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Username toggle ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "prf_username")
async def cb_profile_username(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    p = await get_profile_settings()
    enabled = await _toggle(p, "auto_username")
    await callback.answer(
        "✅ Auto username enabled" if enabled else "❌ Auto username disabled",
        show_alert=False,
    )
    await smart_edit(callback.message, _profile_status_text(p),
        reply_markup=profile_kb(p),
        parse_mode="HTML",
    )


# ── Name toggle ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "prf_name")
async def cb_profile_name(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    p = await get_profile_settings()
    enabled = await _toggle(p, "auto_name")
    await callback.answer(
        "✅ Auto name enabled" if enabled else "❌ Auto name disabled",
        show_alert=False,
    )
    await smart_edit(callback.message, _profile_status_text(p),
        reply_markup=profile_kb(p),
        parse_mode="HTML",
    )


# ── Profile Photo toggle ──────────────────────────────────────────────────────

@router.callback_query(F.data == "prf_photo")
async def cb_profile_photo(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    p = await get_profile_settings()
    enabled = await _toggle(p, "auto_photo")
    await callback.answer(
        "✅ Auto photo enabled" if enabled else "❌ Auto photo disabled",
        show_alert=False,
    )
    await smart_edit(callback.message, _profile_status_text(p),
        reply_markup=profile_kb(p),
        parse_mode="HTML",
    )


# ── Bio toggle ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "prf_bio")
async def cb_profile_bio(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    p = await get_profile_settings()
    enabled = await _toggle(p, "auto_bio")
    await callback.answer(
        "✅ Auto bio enabled" if enabled else "❌ Auto bio disabled",
        show_alert=False,
    )
    await smart_edit(callback.message, _profile_status_text(p),
        reply_markup=profile_kb(p),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTO 2FA SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "auto2fa")
async def cb_auto2fa_view(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    a2fa = await get_auto_2fa()
    is_enabled = a2fa.get("enabled", False)
    status_text = "🟢 Enabled" if is_enabled else "🔴 Disabled"
    pwd = a2fa.get("password", "")
    pwd_display = f"<code>{pwd}</code>" if pwd else "<i>None</i>"

    text = (
        "🔐 <b>Auto 2FA Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 Status: <b>{status_text}</b>\n"
        f"🔹 Password: {pwd_display}\n\n"
        "<i>When enabled, the bot will automatically set this 2FA password on new sessions upon successful registration.</i>"
    )
    await smart_edit(callback.message, text, reply_markup=auto_2fa_kb(a2fa), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "a2fa_toggle")
async def cb_auto2fa_toggle(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    a2fa = await get_auto_2fa()
    if not a2fa.get("enabled", False) and not a2fa.get("password"):
        await callback.answer("⚠️ Please set a password first!", show_alert=True)
        return

    a2fa["enabled"] = not a2fa.get("enabled", False)
    await set_auto_2fa(a2fa)

    is_enabled = a2fa.get("enabled", False)
    status_text = "🟢 Enabled" if is_enabled else "🔴 Disabled"
    pwd = a2fa.get("password", "")
    pwd_display = f"<code>{pwd}</code>" if pwd else "<i>None</i>"

    text = (
        "🔐 <b>Auto 2FA Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 Status: <b>{status_text}</b>\n"
        f"🔹 Password: {pwd_display}\n\n"
        "<i>When enabled, the bot will automatically set this 2FA password on new sessions upon successful registration.</i>"
    )
    await smart_edit(callback.message, text, reply_markup=auto_2fa_kb(a2fa), parse_mode="HTML")
    await callback.answer("Auto 2FA state updated")

@router.callback_query(F.data == "a2fa_edit")
async def cb_auto2fa_edit(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(Auto2FAState.waiting_password)
    text = (
        "✏️ <b>Set Auto 2FA Password</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the new password that will be used for Auto 2FA:"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(Auto2FAState.waiting_password)
async def on_auto2fa_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    pwd = message.text.strip()
    a2fa = await get_auto_2fa()
    a2fa["password"] = pwd
    # Auto-enable if it was disabled and a password is provided
    a2fa["enabled"] = True
    await set_auto_2fa(a2fa)
    
    await state.clear()
    
    is_enabled = a2fa.get("enabled", False)
    status_text = "🟢 Enabled" if is_enabled else "🔴 Disabled"
    pwd_display = f"<code>{pwd}</code>"

    text = (
        "✅ <b>Auto 2FA password updated!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 Status: <b>{status_text}</b>\n"
        f"🔹 Password: {pwd_display}\n\n"
        "<i>When enabled, the bot will automatically set this 2FA password on new sessions upon successful registration.</i>"
    )
    await message.answer(text, reply_markup=auto_2fa_kb(a2fa), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTO LOG OUT SETTINGS
#  Global default: every new registration terminates all other authorizations.
# ═══════════════════════════════════════════════════════════════════════════════

def _auto_logout_text(alo: dict) -> str:
    is_enabled = alo.get("enabled", False)
    status_text = "🟢 Enabled" if is_enabled else "🔴 Disabled"
    return (
        "🚪 <b>Auto Log Out Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 Status: <b>{status_text}</b>\n\n"
        "<i>When enabled, every newly registered session automatically terminates "
        "all other active sessions on the account — no confirmation asked.\n\n"
        "Telegram blocks a fresh session from doing this for the first 24 hours; "
        "in that case the bot retries by itself once the restriction expires and "
        "notifies you when it succeeds.</i>"
    )


@router.callback_query(F.data == "autologout")
async def cb_auto_logout_view(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    alo = await get_auto_logout()
    await smart_edit(callback.message, _auto_logout_text(alo),
        reply_markup=auto_logout_kb(alo), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "alo_toggle")
async def cb_auto_logout_toggle(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    alo = await get_auto_logout()
    alo["enabled"] = not alo.get("enabled", False)
    await set_auto_logout(alo)

    await smart_edit(callback.message, _auto_logout_text(alo),
        reply_markup=auto_logout_kb(alo), parse_mode="HTML",
    )
    await callback.answer(
        "✅ Auto Log Out enabled" if alo["enabled"] else "❌ Auto Log Out disabled"
    )
