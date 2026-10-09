"""
Register handler — Phone registration flow with FSM.
"""
from utils.utils import smart_edit

import os
import io
import asyncio
import random
import string
import logging
import aiohttp
from PIL import Image
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from ui.states import RegState
from ui.keyboards import cancel_kb, back_menu_kb
from config.config_manager import (
    get_proxy, get_api_credentials, get_profile_settings, get_auto_2fa, get_auto_logout,
)
from utils.logout_utils import (
    terminate_other_sessions,
    count_authorizations,
    logout_others_for_phone,
    schedule_retry,
    format_wait,
)
from utils.country_utils import get_session_dir, set_account_status, set_contact_status, set_spam_status
from utils.phone_utils import parse_phone, PhoneValidationError
from workers.session_worker import (
    create_client,
    check_spam,
    check_contact_limit,
    PhoneNumberInvalidError,
    PhoneNumberBannedError,
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
)
from telethon.errors import RPCError
from handlers.common import is_admin_async, is_write_admin_async
import database.database as db

router = Router()
logger = logging.getLogger(__name__)

# Active Telethon clients: {user_id: {"client": ..., "phone": ..., "hash": ..., "timeout_task": ...}}
_active: dict[int, dict] = {}


def describe_code_delivery(sent) -> tuple[str, int]:
    """Explain where Telegram actually put the code. Returns (text, code length).

    Telegram picks the delivery channel itself, and when it picks the in-app
    channel the code lands in another Telegram client already logged into that
    number — never as an SMS. Reporting the channel turns "no code arrived"
    into something the operator can act on instead of guess at.
    """
    from telethon.tl.types import auth as auth_types

    code_type = getattr(sent, "type", None)
    length = getattr(code_type, "length", 5) or 5

    descriptions = (
        (auth_types.SentCodeTypeApp,
         "📲 Sent inside Telegram — open another device already logged into this number"),
        (auth_types.SentCodeTypeSms, "💬 Sent by SMS"),
        (auth_types.SentCodeTypeFirebaseSms, "💬 Sent by SMS"),
        (auth_types.SentCodeTypeCall, "📞 Dictated by an automated voice call"),
        (auth_types.SentCodeTypeFlashCall,
         "📞 Flash call — the code is part of the calling number"),
        (auth_types.SentCodeTypeMissedCall,
         "📞 Missed call — the code is the last digits of the calling number"),
        (auth_types.SentCodeTypeFragmentSms, "🧩 Delivered through Fragment (fragment.com)"),
        (auth_types.SentCodeTypeEmailCode, "📧 Sent to the account's recovery email"),
        (auth_types.SentCodeTypeSmsWord, "💬 Sent by SMS — the code is a word"),
        (auth_types.SentCodeTypeSmsPhrase, "💬 Sent by SMS — the code is a phrase"),
    )
    for cls, text in descriptions:
        if isinstance(code_type, cls):
            return text, length

    return "📨 Code requested", length

async def cleanup_registration(uid: int, message: Message = None, state: FSMContext = None):
    """Disconnects the client and deletes the session files if active."""
    if uid in _active:
        reg = _active[uid]
        client = reg.get("client")
        
        if "timeout_task" in reg and not reg["timeout_task"].done():
            reg["timeout_task"].cancel()

        phone = reg.get("phone")
        country = reg.get("country")

        try:
            await client.disconnect()
        except Exception:
            pass
            
        if phone and country:
            if "temp_path" in reg:
                for ext in ["", ".session", ".session-journal", ".session-shm", ".session-wal"]:
                    p = reg["temp_path"] + ext
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            else:
                session_dir = get_session_dir(country)
                session_path = os.path.join(session_dir, phone)
                for ext in ["", ".session", ".session-journal", ".session-shm", ".session-wal"]:
                    p = session_path + ext
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
        del _active[uid]

        if message and state:
            try:
                await message.answer("⏳ <b>Timeout!</b> Registration session closed and deleted.", parse_mode="HTML")
                await state.clear()
            except Exception:
                pass


RANDOMUSER_API = "https://randomuser.me/api/"


async def _fetch_random_user() -> dict | None:
    """Fetch one random user from randomuser.me API. Returns the result dict or None on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RANDOMUSER_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if results:
                        return results[0]
    except Exception as e:
        logger.warning(f"randomuser.me API failed: {e}")
    return None


async def _download_url_bytes(url: str) -> bytes | None:
    """Download bytes from a URL. Returns None on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
    return None


def _upscale_photo(raw_bytes: bytes, min_size: int = 512) -> bytes:
    """
    Upscale image to at least min_size x min_size using Pillow (LANCZOS).
    Telegram requires photos >= ~10 KB and reasonable dimensions.
    Returns JPEG bytes.
    """
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    if w < min_size or h < min_size:
        scale = max(min_size / w, min_size / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    out.seek(0)
    return out.read()


# 50 bio templates using randomuser.me fields
_BIO_TEMPLATES = [
    lambda u: f"📍 {u['city']}, {u['country']}",
    lambda u: f"🌍 {u['country']} · {u['age']} y.o.",
    lambda u: f"✈️ From {u['city']} | {u['age']} years",
    lambda u: f"🏙️ {u['city']} | {u['country']}",
    lambda u: f"🎂 {u['age']} · {u['city']}",
    lambda u: f"📌 Based in {u['city']}",
    lambda u: f"🌐 {u['country']} | {u['age']} y.o.",
    lambda u: f"🏠 {u['city']}, {u['state']}",
    lambda u: f"👤 {u['age']} · {u['country']}",
    lambda u: f"🌆 {u['city']} · {u['state']}",
    lambda u: f"📍 {u['state']}, {u['country']}",
    lambda u: f"🗺️ {u['country']} · {u['city']}",
    lambda u: f"🎯 {u['city']} | {u['age']} yrs",
    lambda u: f"🌟 {u['age']} from {u['country']}",
    lambda u: f"☕ {u['city']} vibes · {u['age']}",
    lambda u: f"🏔️ {u['state']} · {u['country']}",
    lambda u: f"🌙 {u['age']} · {u['city']}, {u['country']}",
    lambda u: f"✨ {u['city']} | {u['country']}",
    lambda u: f"🎵 {u['age']} y.o. · {u['city']}",
    lambda u: f"📷 {u['city']} · {u['state']}",
    lambda u: f"💫 {u['country']} · {u['age']}",
    lambda u: f"🌊 {u['city']}, {u['country']} · {u['age']}",
    lambda u: f"🔥 {u['age']} | {u['city']}",
    lambda u: f"🎨 {u['city']} · {u['country']}",
    lambda u: f"⚡ {u['state']} · {u['age']} y.o.",
    lambda u: f"🌺 {u['country']} | {u['city']}",
    lambda u: f"🎸 {u['age']} from {u['city']}",
    lambda u: f"🏖️ {u['city']} · {u['age']}",
    lambda u: f"🎭 {u['state']}, {u['country']}",
    lambda u: f"🚀 {u['age']} · {u['country']}",
    lambda u: f"🌸 {u['city']} | {u['age']} years old",
    lambda u: f"🦋 {u['country']} · {u['city']}",
    lambda u: f"⭐ {u['age']} · {u['state']}",
    lambda u: f"🎯 {u['country']} | {u['city']}",
    lambda u: f"🌈 {u['city']}, {u['state']}",
    lambda u: f"🏄 {u['age']} · {u['city']}, {u['country']}",
    lambda u: f"🎪 {u['state']} · {u['age']}",
    lambda u: f"🦅 {u['country']} born · {u['age']}",
    lambda u: f"🌍 {u['city']} · {u['country']} · {u['age']}",
    lambda u: f"🎻 {u['age']} y.o. · {u['country']}",
    lambda u: f"🌵 {u['state']}, {u['country']}",
    lambda u: f"🏡 {u['city']} · {u['age']} yrs",
    lambda u: f"🎲 {u['age']} | {u['state']}",
    lambda u: f"🌾 From {u['state']}, {u['country']}",
    lambda u: f"🎬 {u['city']} | {u['age']}",
    lambda u: f"🌏 {u['country']} · {u['age']} y.o.",
    lambda u: f"🏋️ {u['age']} · {u['city']}",
    lambda u: f"🎓 {u['city']}, {u['country']}",
    lambda u: f"🌻 {u['age']} from {u['state']}",
    lambda u: f"💎 {u['city']} · {u['country']} · {u['age']}",
]


def _build_bio_from_user(u: dict) -> str:
    """Pick a random bio template and fill it with API data."""
    city    = u.get("location", {}).get("city", "")
    state   = u.get("location", {}).get("state", "")
    country = u.get("location", {}).get("country", "")
    age     = str(u.get("dob", {}).get("age", ""))

    # Only use templates that will produce non-empty output
    data = {"city": city, "state": state, "country": country, "age": age}
    # Shuffle templates and pick first one that doesn't crash / gives content
    templates = list(_BIO_TEMPLATES)
    random.shuffle(templates)
    for tmpl in templates:
        try:
            result = tmpl(data).strip()
            if result and len(result) <= 70:   # Telegram bio limit is 70 chars
                return result
        except Exception:
            continue
    return f"📍 {city}, {country}".strip(" ,") if city or country else ""


async def _apply_profile(client, bot: Bot, profile: dict) -> list[str]:
    """
    Apply profile auto-fill settings to a freshly registered account.
    All data (username, name, photo, bio) is generated from randomuser.me API.
    Returns a list of applied actions (for display in the success message).
    """
    applied = []

    # Fetch random user data once if any field is enabled
    need_api = (
        profile.get("auto_username")
        or profile.get("auto_name")
        or profile.get("auto_photo")
        or profile.get("auto_bio")
    )
    if not need_api:
        return applied

    random_user = await _fetch_random_user()
    if random_user:
        logger.info("randomuser.me data fetched successfully")
    else:
        logger.warning("randomuser.me unavailable — skipping profile apply")
        return applied

    # Extract common fields once
    first   = random_user.get("name", {}).get("first", "")
    last    = random_user.get("name", {}).get("last", "")
    age     = str(random_user.get("dob", {}).get("age", ""))

    try:
        # ── Username: first + last + age + 3 random digits ────────────────────
        if profile.get("auto_username"):
            rand_digits = "".join(random.choices(string.digits, k=3))
            # Build from first+last (ASCII-safe: keep alnum only)
            base = (first + last).lower()
            base = "".join(c for c in base if c.isalnum())
            if not base:
                base = "user"
            raw_username = f"{base}{age}{rand_digits}"
            # Telegram: 5–32 chars, letters/digits/underscore
            username = raw_username[:32]
            if len(username) < 5:
                username = username + rand_digits + "tg"
            try:
                from telethon.tl.functions.account import UpdateUsernameRequest
                await client(UpdateUsernameRequest(username=username))
                applied.append(f"📛 Username: @{username}")
            except Exception as e:
                logger.warning(f"Could not set username: {e}")

        # ── Name ──────────────────────────────────────────────────────────────
        if profile.get("auto_name"):
            if first:
                try:
                    from telethon.tl.functions.account import UpdateProfileRequest
                    await client(UpdateProfileRequest(first_name=first, last_name=last))
                    applied.append(f"👤 Name: {first} {last}".strip())
                except Exception as e:
                    logger.warning(f"Could not set name: {e}")

        # ── Bio (random template from API data) ────────────────────────────────
        if profile.get("auto_bio"):
            bio_text = _build_bio_from_user(random_user)
            if bio_text:
                try:
                    from telethon.tl.functions.account import UpdateProfileRequest
                    await client(UpdateProfileRequest(about=bio_text))
                    applied.append(f"📝 Bio: {bio_text}")
                except Exception as e:
                    logger.warning(f"Could not set bio: {e}")

        # ── Profile Photo (large, upscaled to meet Telegram minimum) ──────────
        if profile.get("auto_photo"):
            photo_url = random_user.get("picture", {}).get("large", "")
            if photo_url:
                photo_data = await _download_url_bytes(photo_url)
                if photo_data:
                    try:
                        # Upscale to at least 512x512 so Telegram accepts it
                        photo_data = _upscale_photo(photo_data, min_size=512)
                        buf = io.BytesIO(photo_data)
                        buf.name = "photo.jpg"
                        from telethon.tl.functions.photos import UploadProfilePhotoRequest
                        uploaded = await client.upload_file(buf)
                        await client(UploadProfilePhotoRequest(file=uploaded))
                        applied.append("🖼 Profile photo: set")
                    except Exception as e:
                        logger.warning(f"Could not set profile photo: {e}")
                else:
                    logger.warning("Photo download returned empty bytes")
            else:
                logger.warning("No large photo URL in randomuser.me response")

    except Exception as e:
        logger.error(f"_apply_profile error: {e}")

    return applied


async def _resolve_advgen_settings(advgen_options: dict | None) -> tuple[dict, bool, str | None, bool]:
    """
    Resolve the effective profile / Auto 2FA / spam-check settings for a registration.

    - Plain "New Session" flow (advgen_options is None): unchanged behavior —
      uses the global Settings (Profile auto-fill, Auto 2FA) and always checks spam.
    - "Advanced Gen" flow (advgen_options is the per-session toggle dict from the
      Advanced Gen menu): the toggles picked in that menu decide behavior for this
      registration, independent of the global Settings.

    Returns (profile, do_2fa, a2fa_password, do_check).
    """
    if advgen_options is None:
        profile = await get_profile_settings()
        a2fa = await get_auto_2fa()
        do_2fa = bool(a2fa.get("enabled") and a2fa.get("password"))
        a2fa_password = a2fa.get("password") if do_2fa else None
        return profile, do_2fa, a2fa_password, True

    if advgen_options.get("auto_profile"):
        profile = {"auto_username": True, "auto_name": True, "auto_photo": True, "auto_bio": True}
    else:
        profile = {}

    a2fa_password = None
    do_2fa = False
    if advgen_options.get("auto_2fa"):
        a2fa = await get_auto_2fa()
        a2fa_password = a2fa.get("password")
        do_2fa = bool(a2fa_password)

    do_check = advgen_options.get("auto_check", True)
    return profile, do_2fa, a2fa_password, do_check


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTO LOG OUT
# ═══════════════════════════════════════════════════════════════════════════════

async def _resolve_logout_mode(advgen_options: dict | None) -> str:
    """
    Decide how a fresh registration should treat the account's other sessions.

    "auto" — terminate them straight away, no questions asked
    "ask"  — prompt the admin first (only when others actually exist)
    "off"  — leave them alone

    The global Settings → Auto Log Out toggle wins everywhere: when it's on,
    every registration cleans up unconditionally. Otherwise the Advanced Gen
    toggle decides for that run, and the plain "New Session" flow asks.
    """
    alo = await get_auto_logout()
    if alo.get("enabled"):
        return "auto"
    if advgen_options is not None:
        return "auto" if advgen_options.get("auto_logout") else "off"
    return "ask"


async def _run_auto_logout(client, phone: str, mode: str, chat_id: int) -> tuple[str, bool]:
    """
    Handle Auto Log Out for a just-registered session, while its client is
    still connected.

    Returns (summary_line, should_prompt). `should_prompt` is only ever True in
    "ask" mode when the account has more than one active session — the caller
    sends the Yes/No question after the success message.
    """
    if mode == "off":
        return "", False

    if mode == "ask":
        total = await count_authorizations(client)
        if total >= 2:
            return "", True
        return "", False

    # mode == "auto"
    result = await terminate_other_sessions(client)

    if result.status == "done":
        return f"\n🚪 <b>Auto Log Out:</b> ✅ {result.terminated} other session(s) terminated\n", False

    if result.status == "nothing":
        return "\n🚪 <b>Auto Log Out:</b> ✅ No other sessions\n", False

    if result.status == "restricted":
        # Telegram blocks fresh sessions from resetting others — retry later.
        await schedule_retry(phone, result.retry_after, chat_id=chat_id, notify=False)
        return (
            f"\n🚪 <b>Auto Log Out:</b> ⏳ Restricted by Telegram — "
            f"will retry automatically in <b>{format_wait(result.retry_after)}</b>\n"
        ), False

    if result.status == "unauthorized":
        return "\n🚪 <b>Auto Log Out:</b> ⚠️ Bot session terminated — other sessions not logged out\n", False

    return f"\n🚪 <b>Auto Log Out:</b> ❌ Failed — <code>{result.error}</code>\n", False


def _logout_prompt_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes", callback_data=f"alogout_yes:{phone}"),
            InlineKeyboardButton(text="❌ No", callback_data="alogout_no"),
        ],
    ])


async def _ask_logout(message: Message, phone: str) -> None:
    """Ask the admin whether to terminate the account's other sessions."""
    await message.answer(
        f"⚠️ <b>More than one active session</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <code>+{phone}</code>\n\n"
        f"There is more than one session active on this account.\n"
        f"Would you like to attempt to terminate the other sessions?",
        reply_markup=_logout_prompt_kb(phone),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("alogout_yes:"))
async def cb_auto_logout_yes(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[len("alogout_yes:"):]
    await callback.answer("⏳ Terminating other sessions...")
    await smart_edit(callback.message,
        f"🚪 <b>Terminating other sessions...</b>\n\n<code>+{phone}</code>",
        parse_mode="HTML",
    )

    result = await logout_others_for_phone(phone)

    if result.status == "done":
        text = (
            f"✅ <b>Other Sessions Terminated!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 <code>+{phone}</code>\n"
            f"🚪 Terminated: <b>{result.terminated}</b> session(s)\n\n"
            f"⭐ Only the bot's session remains active."
        )
    elif result.status == "nothing":
        text = (
            f"ℹ️ <b>Nothing to terminate</b>\n\n"
            f"📱 <code>+{phone}</code>\n"
            f"The bot's session is the only active one."
        )
    elif result.status == "restricted":
        wait = format_wait(result.retry_after)
        await schedule_retry(phone, result.retry_after, chat_id=callback.from_user.id, notify=True)
        text = (
            f"⏳ <b>Failed to terminate sessions</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 <code>+{phone}</code>\n\n"
            f"Telegram does not allow a newly created session to log out older "
            f"ones yet.\n\n"
            f"🕐 Time remaining: <b>{wait}</b>\n"
            f"🔁 They will be terminated automatically once this expires — "
            f"you'll get a message when it's done."
        )
    elif result.status == "unauthorized":
        text = (
            f"⚠️ <b>Bot session was terminated</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 <code>+{phone}</code>\n\n"
            f"The bot's current session was terminated, and it failed to log out "
            f"the other sessions."
        )
    else:
        text = (
            f"❌ <b>Failed to terminate sessions!</b>\n\n"
            f"📱 <code>+{phone}</code>\n"
            f"Error: <code>{result.error}</code>"
        )

    await smart_edit(callback.message, text, reply_markup=back_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "alogout_no")
async def cb_auto_logout_no(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await smart_edit(callback.message,
        "👍 <b>Other sessions kept active.</b>",
        reply_markup=back_menu_kb(), parse_mode="HTML",
    )
    await callback.answer()


# ─── Start Registration ─────────────────────────────────────────────────────

@router.callback_query(F.data == "reg")
async def cb_register(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫 Not authorized", show_alert=True)
        return

    await state.set_state(RegState.phone)
    text = (
        "📱 <b>Register New Number</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the phone number in international format, starting with "
        "<b>+</b> and the country code. Spaces are fine.\n\n"
        "📝 Example: <code>+966512345678</code> or <code>+966 51 234 5678</code>"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Receive Phone ───────────────────────────────────────────────────────────

@router.message(RegState.phone)
async def on_phone(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    try:
        info = parse_phone(message.text)
    except PhoneValidationError as e:
        await message.answer(
            f"{e}\n\n"
            "📝 Example: <code>+966512345678</code> or <code>+966 51 234 5678</code>",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return

    phone = info.digits
    country_name, flag = info.country_name, info.flag

    data = await state.get_data()
    advgen_options = data.get("advgen_options")

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    session_dir = get_session_dir(country_name)
    session_path = os.path.join(session_dir, phone)
    
    uid = message.from_user.id
    if os.path.exists(session_path + ".session"):
        current_path = session_path
        temp_path = None
    else:
        from utils.country_utils import BASE_DIR
        temp_dir = os.path.join(BASE_DIR, "temp_sessions")
        os.makedirs(temp_dir, exist_ok=True)
        current_path = os.path.join(temp_dir, f"{uid}_{phone}")
        temp_path = current_path

    carrier_line = f"📡 <b>Carrier:</b> {info.carrier}\n" if info.carrier else ""
    status_msg = await message.answer(
        f"⏳ <b>Initializing Connection...</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Phone:</b> <code>+{phone}</code>\n"
        f"📍 <b>Region:</b> {flag} <b>{country_name}</b>\n"
        f"{carrier_line}"
        f"\n<i>Requesting authorization code from Telegram...</i>",
        parse_mode="HTML",
    )

    # Cleanup any previous client
    uid = message.from_user.id
    if uid in _active:
        try:
            await _active[uid]["client"].disconnect()
        except Exception:
            pass
        del _active[uid]

    try:
        # phone is passed explicitly: this session still lives under the
        # temporary "<uid>_<phone>" path, and the fingerprint has to survive
        # the move to its final "<phone>.session" name.
        client = create_client(current_path, api_id, api_hash, proxy, phone=phone)
        await client.connect()

        # Check if already authorized
        if await client.is_user_authorized():
            me = await client.get_me()
            status_msg = await smart_edit(status_msg, f"🕵️ Checking account status...\n\n"
                f"📱 <code>+{me.phone}</code>",
                parse_mode="HTML",
            )
            spam_status = await check_spam(client)
            contact_status = await check_contact_limit(client)
            await set_spam_status(me.phone, spam_status)
            await set_account_status(me.phone, "live")
            await set_contact_status(me.phone, contact_status)

            await client.disconnect()
            await state.clear()
            status_msg = await smart_edit(status_msg, 
                f"✅ <b>Session Already Authorized!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Name:</b> {me.first_name or ''} {me.last_name or ''}\n"
                f"📱 <b>Phone:</b> <code>+{me.phone}</code>\n"
                f"🆔 <b>User ID:</b> <code>{me.id}</code>\n"
                f"📍 <b>Region:</b> {flag} {country_name}\n\n"
                f"🛡️ <b>Spam Status:</b> {spam_status}\n"
                f"📇 <b>Contact Limit:</b> {contact_status}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"💾 <i>Session securely saved and active.</i>",
                reply_markup=back_menu_kb(),
                parse_mode="HTML",
            )
            return

        sent = await client.send_code_request("+" + phone)

        _active[uid] = {
            "client": client,
            "phone": phone,
            "hash": sent.phone_code_hash,
            "country": country_name,
            "flag": flag,
            "advgen_options": advgen_options,
        }
        if temp_path:
            _active[uid]["temp_path"] = temp_path

        async def _timeout():
            await asyncio.sleep(300)
            await cleanup_registration(uid, message, state)

        _active[uid]["timeout_task"] = asyncio.create_task(_timeout())

        delivery, code_len = describe_code_delivery(sent)
        await state.set_state(RegState.code)
        status_msg = await smart_edit(status_msg,
            f"📨 <b>Authorization Code Sent</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 <b>Target Phone:</b> <code>+{phone}</code>\n"
            f"📍 <b>Region:</b> {flag} {country_name}\n"
            f"📬 <b>Delivery:</b> {delivery}\n\n"
            f"🔑 <i>Please enter the {code_len}-digit Telegram code below:</i>",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )

    except PhoneNumberInvalidError:
        status_msg = await smart_edit(status_msg, "❌ <b>Invalid phone number!</b>", reply_markup=back_menu_kb(), parse_mode="HTML")
        await state.clear()
    except PhoneNumberBannedError:
        status_msg = await smart_edit(status_msg, "🚫 <b>This number is banned from Telegram!</b>", reply_markup=back_menu_kb(), parse_mode="HTML")
        await state.clear()
    except FloodWaitError as e:
        status_msg = await smart_edit(status_msg, f"⏳ <b>Flood wait!</b> Try again in <b>{e.seconds}</b> seconds.",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        await state.clear()
    except RPCError as e:
        # RECAPTCHA_CHECK_signup__<sitekey>: the number has no Telegram account
        # yet, and Telegram now gates account creation on this api_id behind a
        # reCAPTCHA that only the official app can answer. Telethon has no error
        # class for it, so it would otherwise surface as a raw 403 traceback.
        if "RECAPTCHA_CHECK" in str(e):
            logger.warning(f"Signup captcha required for +{phone} on api_id {api_id}")
            status_msg = await smart_edit(status_msg,
                f"🤖 <b>Telegram requires a captcha to create this account</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>Phone:</b> <code>+{phone}</code>\n\n"
                f"<i>This number has no Telegram account yet, and account creation "
                f"on the current API platform is gated behind a reCAPTCHA that only "
                f"the official app can answer — the bot cannot complete it.\n\n"
                f"Create the account once in the real Telegram app, then this bot "
                f"can log in and manage it normally.</i>",
                reply_markup=back_menu_kb(),
                parse_mode="HTML",
            )
            await state.clear()
            return
        logger.error(f"Registration RPC error: {e}")
        status_msg = await smart_edit(status_msg, f"❌ <b>Error:</b> <code>{type(e).__name__}: {e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        await state.clear()
    except Exception as e:
        logger.error(f"Registration error: {e}")
        status_msg = await smart_edit(status_msg, f"❌ <b>Error:</b> <code>{type(e).__name__}: {e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        await state.clear()


# ─── Receive Code ────────────────────────────────────────────────────────────

@router.message(RegState.code)
async def on_code(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    uid = message.from_user.id
    if uid not in _active:
        await message.answer("❌ Session expired. Start over.", reply_markup=back_menu_kb())
        await state.clear()
        return

    reg = _active[uid]
    client = reg["client"]
    verification_code = message.text.strip().replace(" ", "").replace("-", "")

    try:
        await message.delete()
    except Exception:
        pass

    try:
        await client.sign_in(
            phone="+" + reg["phone"],
            code=verification_code,
            phone_code_hash=reg["hash"],
        )

        # Success!
        me = await client.get_me()

        status_msg = await message.answer("🕵️ Checking account status...")

        advgen_options = reg.get("advgen_options")
        profile, do_2fa, a2fa_password, do_check = await _resolve_advgen_settings(advgen_options)

        # Apply profile auto-fill settings
        applied_profile = []
        if any([
            profile.get("auto_username"),
            profile.get("auto_name"),
            profile.get("auto_photo"),
            profile.get("auto_bio"),
        ]):
            status_msg = await smart_edit(status_msg, "🎨 Applying profile settings...")
            applied_profile = await _apply_profile(client, message.bot, profile)
            status_msg = await smart_edit(status_msg, "🕵️ Checking account status...")

        if do_check:
            spam_status = await check_spam(client)
            contact_status = await check_contact_limit(client)
            await set_spam_status(me.phone, spam_status)
            await set_contact_status(me.phone, contact_status)
        else:
            spam_status = "⏭ Skipped"
            contact_status = "⏭ Skipped"
        await set_account_status(me.phone, "live")

        # Apply Auto 2FA if enabled
        auto_2fa_applied = False
        if do_2fa:
            try:
                status_msg = await smart_edit(status_msg, "🔐 Applying Auto 2FA...")
                await client.edit_2fa(new_password=a2fa_password)
                await db.set_2fa(me.phone, a2fa_password)
                auto_2fa_applied = True
                status_msg = await smart_edit(status_msg, "🕵️ Checking account status...")
            except Exception as e:
                logger.error(f"Failed to apply auto 2FA for {me.phone}: {e}")

        # Auto Log Out — must run while the client is still connected.
        # Never let it raise: the outer handler treats a failure here as a failed
        # registration and deletes the session files we just created.
        try:
            logout_mode = await _resolve_logout_mode(advgen_options)
            logout_line, ask_logout = await _run_auto_logout(
                client, reg["phone"], logout_mode, message.from_user.id
            )
        except Exception as e:
            logger.error(f"Auto Log Out failed for +{reg['phone']}: {e}")
            logout_line, ask_logout = (
                f"\n🚪 <b>Auto Log Out:</b> ❌ Failed — <code>{type(e).__name__}</code>\n", False
            )

        await client.disconnect()

        # Move session from temp folder to real folder
        if "temp_path" in reg:
            import shutil
            real_dir = get_session_dir(reg["country"])
            real_path = os.path.join(real_dir, reg["phone"])
            for ext in ["", ".session", ".session-journal", ".session-shm", ".session-wal"]:
                src = reg["temp_path"] + ext
                dst = real_path + ext
                if os.path.exists(src):
                    try:
                        shutil.move(src, dst)
                    except Exception:
                        pass
            del reg["temp_path"]
            
        if "timeout_task" in _active[uid] and not _active[uid]["timeout_task"].done():
            _active[uid]["timeout_task"].cancel()
        del _active[uid]
        await state.clear()

        try:
            await status_msg.delete()
        except Exception:
            pass

        profile_line = ""
        if applied_profile:
            profile_line = "\n🎨 <b>Profile applied:</b>\n" + "\n".join(f"  • {a}" for a in applied_profile) + "\n"

        if auto_2fa_applied:
            a2fa_line = "\n🔐 <b>Auto 2FA:</b> ✅ Applied\n"
        elif advgen_options is not None and advgen_options.get("auto_2fa"):
            a2fa_line = "\n🔐 <b>Auto 2FA:</b> ⚠️ Failed — set a password in Settings → Auto 2FA\n"
        else:
            a2fa_line = ""

        await message.answer(
            f"✅ <b>Registration Successful!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Name:</b> {me.first_name or ''} {me.last_name or ''}\n"
            f"📛 <b>Username:</b> @{me.username or '—'}\n"
            f"📱 <b>Phone:</b> <code>+{me.phone}</code>\n"
            f"🆔 <b>User ID:</b> <code>{me.id}</code>\n"
            f"📍 <b>Region:</b> {reg['flag']} {reg['country']}\n\n"
            f"🛡️ <b>Spam Status:</b> {spam_status}\n"
            f"📇 <b>Contact Limit:</b> {contact_status}\n"
            f"{profile_line}{a2fa_line}{logout_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💾 <i>Session securely saved.</i>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )

        if ask_logout:
            await _ask_logout(message, reg["phone"])

    except SessionPasswordNeededError:
        if "timeout_task" in _active[uid] and not _active[uid]["timeout_task"].done():
            _active[uid]["timeout_task"].cancel()

        async def _timeout():
            await asyncio.sleep(300)
            await cleanup_registration(uid, message, state)
            
        _active[uid]["timeout_task"] = asyncio.create_task(_timeout())

        await state.set_state(RegState.password)
        await message.answer(
            "🔐 <b>Two-factor authentication enabled!</b>\n\n"
            "🔑 Enter your 2FA password:",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )

    except PhoneCodeInvalidError:
        await message.answer(
            "❌ <b>Invalid code!</b> Try again:",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )

    except PhoneCodeExpiredError:
        # Auto re-request a new code instead of making user start over
        try:
            sent = await client.send_code_request("+" + reg["phone"])
            _active[uid]["hash"] = sent.phone_code_hash
            logger.info(f"Code expired for +{reg['phone']}, auto re-requested new code")
            
            if "timeout_task" in _active[uid] and not _active[uid]["timeout_task"].done():
                _active[uid]["timeout_task"].cancel()

            async def _timeout():
                await asyncio.sleep(300)
                await cleanup_registration(uid, message, state)
                
            _active[uid]["timeout_task"] = asyncio.create_task(_timeout())
            delivery, _ = describe_code_delivery(sent)
            await message.answer(
                "⏰ <b>Code expired — new code sent!</b>\n\n"
                f"📱 Phone: <code>+{reg['phone']}</code>\n"
                f"📬 Delivery: {delivery}\n\n"
                "📥 Enter the new verification code:",
                reply_markup=cancel_kb(),
                parse_mode="HTML",
            )
        except Exception as resend_err:
            logger.error(f"Failed to resend code for +{reg['phone']}: {resend_err}")
            if uid in _active:
                try:
                    await _active[uid]["client"].disconnect()
                except Exception:
                    pass
                del _active[uid]
            await state.clear()
            await message.answer(
                f"⏰ <b>Code expired & resend failed!</b>\n\n"
                f"Error: <code>{resend_err}</code>\n\nPlease start over.",
                reply_markup=back_menu_kb(),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Sign-in error: {e}")
        await cleanup_registration(uid)
        await state.clear()
        await message.answer(
            f"❌ <b>Error:</b> <code>{type(e).__name__}: {e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )


# ─── Receive 2FA Password ───────────────────────────────────────────────────

@router.message(RegState.password)
async def on_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    uid = message.from_user.id
    if uid not in _active:
        await message.answer("❌ Session expired. Start over.", reply_markup=back_menu_kb())
        await state.clear()
        return

    reg = _active[uid]
    client = reg["client"]
    password = message.text.strip()

    try:
        await client.sign_in(password=password)

        me = await client.get_me()

        status_msg = await message.answer("🕵️ Checking account status...")

        advgen_options = reg.get("advgen_options")
        profile, do_2fa, a2fa_password, do_check = await _resolve_advgen_settings(advgen_options)

        # Apply profile auto-fill settings
        applied_profile = []
        if any([
            profile.get("auto_username"),
            profile.get("auto_name"),
            profile.get("auto_photo"),
            profile.get("auto_bio"),
        ]):
            status_msg = await smart_edit(status_msg, "🎨 Applying profile settings...")
            applied_profile = await _apply_profile(client, message.bot, profile)
            status_msg = await smart_edit(status_msg, "🕵️ Checking account status...")

        if do_check:
            spam_status = await check_spam(client)
            contact_status = await check_contact_limit(client)
            await set_spam_status(me.phone, spam_status)
            await set_contact_status(me.phone, contact_status)
        else:
            spam_status = "⏭ Skipped"
            contact_status = "⏭ Skipped"
        await set_account_status(me.phone, "live")

        # Apply Auto 2FA if enabled
        auto_2fa_applied = False
        if do_2fa:
            if password == a2fa_password:
                try:
                    await db.set_2fa(me.phone, password)
                except Exception:
                    pass
                auto_2fa_applied = True
            else:
                try:
                    status_msg = await smart_edit(status_msg, "🔐 Updating Auto 2FA...")
                    await client.edit_2fa(current_password=password, new_password=a2fa_password)
                    await db.set_2fa(me.phone, a2fa_password)
                    auto_2fa_applied = True
                    status_msg = await smart_edit(status_msg, "🕵️ Checking account status...")
                except Exception as e:
                    logger.error(f"Failed to update auto 2FA for {me.phone}: {e}")
                    try:
                        await db.set_2fa(me.phone, password)
                    except Exception:
                        pass
        else:
            try:
                await db.set_2fa(me.phone, password)
            except Exception:
                pass

        # Auto Log Out — must run while the client is still connected.
        # Never let it raise: the outer handler treats a failure here as a failed
        # registration and deletes the session files we just created.
        try:
            logout_mode = await _resolve_logout_mode(advgen_options)
            logout_line, ask_logout = await _run_auto_logout(
                client, reg["phone"], logout_mode, message.from_user.id
            )
        except Exception as e:
            logger.error(f"Auto Log Out failed for +{reg['phone']}: {e}")
            logout_line, ask_logout = (
                f"\n🚪 <b>Auto Log Out:</b> ❌ Failed — <code>{type(e).__name__}</code>\n", False
            )

        await client.disconnect()

        # Move session from temp folder to real folder
        if "temp_path" in reg:
            import shutil
            real_dir = get_session_dir(reg["country"])
            real_path = os.path.join(real_dir, reg["phone"])
            for ext in ["", ".session", ".session-journal", ".session-shm", ".session-wal"]:
                src = reg["temp_path"] + ext
                dst = real_path + ext
                if os.path.exists(src):
                    try:
                        shutil.move(src, dst)
                    except Exception:
                        pass
            del reg["temp_path"]

        if "timeout_task" in _active[uid] and not _active[uid]["timeout_task"].done():
            _active[uid]["timeout_task"].cancel()
        del _active[uid]
        await state.clear()

        # Delete the password and status message for cleanliness
        try:
            await message.delete()
            await status_msg.delete()
        except Exception:
            pass

        profile_line = ""
        if applied_profile:
            profile_line = "\n🎨 <b>Profile applied:</b>\n" + "\n".join(f"  • {a}" for a in applied_profile) + "\n"

        if auto_2fa_applied:
            a2fa_line = "\n🔐 <b>Auto 2FA:</b> ✅ Applied\n"
        elif advgen_options is not None and advgen_options.get("auto_2fa"):
            a2fa_line = "\n🔐 <b>Auto 2FA:</b> ⚠️ Failed — set a password in Settings → Auto 2FA\n"
        else:
            a2fa_line = ""

        await message.answer(
            f"✅ <b>Successfully registered!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Name: <b>{me.first_name or ''} {me.last_name or ''}</b>\n"
            f"📱 Phone: <code>+{me.phone}</code>\n"
            f"🆔 ID: <code>{me.id}</code>\n"
            f"📛 Username: @{me.username or '—'}\n"
            f"{reg['flag']} Country: <b>{reg['country']}</b>\n"
            f"🛡️ Spam: <b>{spam_status}</b>\n"
            f"📇 Contact: <b>{contact_status}</b>\n"
            f"{profile_line}{a2fa_line}{logout_line}\n"
            f"📁 Session saved!",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )

        if ask_logout:
            await _ask_logout(message, reg["phone"])

    except Exception as e:
        logger.error(f"2FA error: {e}")
        await cleanup_registration(uid)
        await state.clear()
        await message.answer(
            f"❌ <b>2FA Error:</b> <code>{type(e).__name__}: {e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
