"""
Profile Apply Handler — Apply profile (username/name/photo/bio) to existing sessions.
Supports:
  - Upload ZIP containing .session files  (one-time, files not saved)
  - Apply to a stored country's sessions
"""
from utils.utils import smart_edit

import os
import io
import zipfile
import tempfile
import asyncio
import logging
import random
import string
import shutil

import aiohttp
from PIL import Image

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from ui.keyboards import (
    profile_apply_menu_kb,
    profile_apply_countries_kb,
    back_to_profile_apply_kb,
    cancel_kb,
)
from utils.country_utils import get_all_sessions, get_country_display, SESSIONS_DIR
from config.config_manager import get_proxy, get_api_credentials, get_profile_settings
from workers.session_worker import create_client
from handlers.common import is_admin_async, is_write_admin_async
from ui.states import ProfileApplyState

router = Router()
logger = logging.getLogger(__name__)

RANDOMUSER_API = "https://randomuser.me/api/"

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
    lambda u: f"💫 {u['country']} · {u['age']}",
]


async def _fetch_random_user() -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RANDOMUSER_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if results:
                        return results[0]
    except Exception as e:
        logger.warning(f"randomuser.me failed: {e}")
    return None


async def _download_bytes(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        logger.warning(f"Download failed {url}: {e}")
    return None


def _upscale_photo(raw: bytes, min_size: int = 512) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    if w < min_size or h < min_size:
        scale = max(min_size / w, min_size / h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    out.seek(0)
    return out.read()


def _build_bio(u: dict) -> str:
    city    = u.get("location", {}).get("city", "")
    state   = u.get("location", {}).get("state", "")
    country = u.get("location", {}).get("country", "")
    age     = str(u.get("dob", {}).get("age", ""))
    data    = {"city": city, "state": state, "country": country, "age": age}
    templates = list(_BIO_TEMPLATES)
    random.shuffle(templates)
    for t in templates:
        try:
            r = t(data).strip()
            if r and len(r) <= 70:
                return r
        except Exception:
            continue
    return f"📍 {city}, {country}".strip(" ,") if (city or country) else ""


async def _apply_profile_to_client(client, profile: dict) -> list[str]:
    """Apply profile settings to a connected & authorised Telethon client."""
    applied = []
    need = any([
        profile.get("auto_username"),
        profile.get("auto_name"),
        profile.get("auto_photo"),
        profile.get("auto_bio"),
    ])
    if not need:
        return applied

    ru = await _fetch_random_user()
    if not ru:
        logger.warning("randomuser.me unavailable — skipping")
        return applied

    first = ru.get("name", {}).get("first", "")
    last  = ru.get("name", {}).get("last", "")
    age   = str(ru.get("dob", {}).get("age", ""))

    try:
        if profile.get("auto_username"):
            rand_digits = "".join(random.choices(string.digits, k=3))
            base = "".join(c for c in (first + last).lower() if c.isalnum()) or "user"
            username = (base + age + rand_digits)[:32]
            if len(username) < 5:
                username += rand_digits + "tg"
            try:
                from telethon.tl.functions.account import UpdateUsernameRequest
                await client(UpdateUsernameRequest(username=username))
                applied.append(f"📛 @{username}")
            except Exception as e:
                logger.warning(f"username: {e}")

        if profile.get("auto_name") and first:
            try:
                from telethon.tl.functions.account import UpdateProfileRequest
                await client(UpdateProfileRequest(first_name=first, last_name=last))
                applied.append(f"👤 {first} {last}".strip())
            except Exception as e:
                logger.warning(f"name: {e}")

        if profile.get("auto_bio"):
            bio = _build_bio(ru)
            if bio:
                try:
                    from telethon.tl.functions.account import UpdateProfileRequest
                    await client(UpdateProfileRequest(about=bio))
                    applied.append(f"📝 {bio}")
                except Exception as e:
                    logger.warning(f"bio: {e}")

        if profile.get("auto_photo"):
            photo_url = ru.get("picture", {}).get("large", "")
            if photo_url:
                raw = await _download_bytes(photo_url)
                if raw:
                    try:
                        raw = _upscale_photo(raw)
                        buf = io.BytesIO(raw)
                        buf.name = "photo.jpg"
                        from telethon.tl.functions.photos import UploadProfilePhotoRequest
                        uploaded = await client.upload_file(buf)
                        await client(UploadProfilePhotoRequest(file=uploaded))
                        applied.append("🖼 Photo set")
                    except Exception as e:
                        logger.warning(f"photo: {e}")

    except Exception as e:
        logger.error(f"_apply_profile_to_client: {e}")

    return applied


# ─── Entry: Profile Apply Menu ────────────────────────────────────────────────

@router.callback_query(F.data == "prf_apply")
async def cb_profile_apply_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.clear()
    profile = await get_profile_settings()
    u_icon  = "✅" if profile.get("auto_username") else "❌"
    n_icon  = "✅" if profile.get("auto_name")     else "❌"
    ph_icon = "✅" if profile.get("auto_photo")    else "❌"
    b_icon  = "✅" if profile.get("auto_bio")      else "❌"

    text = (
        "👤 <b>Apply Profile to Sessions</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Active profile settings:</b>\n"
        f"  {u_icon} Username\n"
        f"  {n_icon} Name\n"
        f"  {ph_icon} Profile Photo\n"
        f"  {b_icon} Bio\n\n"
        "Choose target:\n"
        "• <b>Upload ZIP</b> — send a .zip with .session files\n"
        "• <b>Apply to Country</b> — pick a stored country"
    )
    await smart_edit(callback.message, text, reply_markup=profile_apply_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Country List (paginated) ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("prf_country:"))
async def cb_profile_country_list(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    page = int(callback.data.split(":")[1])
    all_sessions = get_all_sessions()

    if not all_sessions:
        await smart_edit(callback.message, "📭 <b>No sessions found.</b>",
            reply_markup=back_to_profile_apply_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    display = {}
    for folder, phones in all_sessions.items():
        flag, name = get_country_display(folder)
        display[folder] = (flag, name, len(phones))

    await smart_edit(callback.message, "🌍 <b>Select a Country</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose the country whose sessions you want to apply profile to:",
        reply_markup=profile_apply_countries_kb(display, page),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Apply to Country ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("prf_c:"))
async def cb_profile_apply_country(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[len("prf_c:"):]
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    folder_path = os.path.join(SESSIONS_DIR, folder)
    profile = await get_profile_settings()

    await smart_edit(callback.message, f"⏳ <b>Applying profile — {flag} {name}</b>\n\n"
        f"Processing <b>0/{len(phones)}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    await _run_profile_apply(phones, folder_path, profile, callback.message)


# ─── Upload ZIP ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "prf_zip")
async def cb_profile_zip_prompt(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(ProfileApplyState.waiting_for_zip)
    await smart_edit(callback.message, "📤 <b>Upload ZIP — Apply Profile</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send a <b>.zip</b> file containing <code>.session</code> files.\n\n"
        "⚠️ Files will <b>NOT</b> be saved — profile is applied in-place.",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ProfileApplyState.waiting_for_zip, F.document)
async def msg_profile_zip_received(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    doc = message.document
    if not doc.file_name.endswith(".zip"):
        await message.answer("❌ Please send a <code>.zip</code> file.", reply_markup=cancel_kb(), parse_mode="HTML")
        return

    await state.clear()
    profile = await get_profile_settings()

    status_msg = await message.answer("⏳ <b>Downloading & extracting ZIP...</b>", parse_mode="HTML")

    bot = message.bot
    file_info = await bot.get_file(doc.file_id)
    downloaded = await bot.download_file(file_info.file_path)

    tmp_dir = tempfile.mkdtemp(prefix="prf_zip_")
    try:
        phones = []
        with zipfile.ZipFile(downloaded, "r") as zf:
            for f in zf.namelist():
                if f.endswith(".session") and "__MACOSX" not in f:
                    basename = os.path.basename(f)
                    clean = basename[:-8].replace("+", "").replace(" ", "").replace("-", "").strip()
                    if clean and clean.isdigit():
                        target = os.path.join(tmp_dir, f"{clean}.session")
                        with open(target, "wb") as out:
                            out.write(zf.read(f))
                        phones.append(clean)

        if not phones:
            status_msg = await smart_edit(status_msg, "📭 <b>No valid .session files found in ZIP!</b>",
                reply_markup=back_to_profile_apply_kb(),
                parse_mode="HTML",
            )
            return

        status_msg = await smart_edit(status_msg, f"⏳ <b>Applying profile to {len(phones)} sessions...</b>\n\nProcessing <b>0/{len(phones)}</b>...",
            parse_mode="HTML",
        )

        await _run_profile_apply(phones, tmp_dir, profile, status_msg, context_msg=message)

    except Exception as e:
        logger.error(f"Profile ZIP error: {e}")
        status_msg = await smart_edit(status_msg, f"❌ <b>Failed:</b> <code>{e}</code>",
            reply_markup=back_to_profile_apply_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── Core Apply Logic ─────────────────────────────────────────────────────────

async def _run_profile_apply(
    phones: list[str],
    sessions_dir: str,
    profile: dict,
    status_msg,
    context_msg=None,
):
    """Apply profile settings to each session concurrently."""
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    total = len(phones)

    results = {"Applied": [], "Skipped": [], "Dead": [], "Error": []}
    sem = asyncio.Semaphore(10)
    processed = 0

    async def _process(phone: str):
        nonlocal processed
        session_path = os.path.join(sessions_dir, phone)

        async with sem:
            try:
                client = create_client(session_path, api_id, api_hash, proxy)
                await client.connect()

                if not await client.is_user_authorized():
                    await client.disconnect()
                    results["Dead"].append(phone)
                    logger.info(f"Profile apply +{phone}: Dead")
                else:
                    applied = await _apply_profile_to_client(client, profile)
                    await client.disconnect()

                    if applied:
                        results["Applied"].append(phone)
                        logger.info(f"Profile apply +{phone}: {applied}")
                    else:
                        results["Skipped"].append(phone)

            except Exception as e:
                logger.error(f"Profile apply +{phone}: {e}")
                results["Error"].append(phone)

            processed += 1
            if processed % 5 == 0 or processed == total:
                try:
                    status_msg = await smart_edit(status_msg, f"⏳ <b>Applying profile...</b>\n\n"
                        f"Processing <b>{processed}/{total}</b>...\n"
                        f"✅ Applied: {len(results['Applied'])}\n"
                        f"⏭ Skipped: {len(results['Skipped'])}\n"
                        f"❌ Dead:    {len(results['Dead'])}\n"
                        f"⚠️ Error:   {len(results['Error'])}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    tasks = [_process(p) for p in phones]
    if tasks:
        await asyncio.gather(*tasks)

    # Final summary
    summary = (
        "✅ <b>Profile Apply — Done!</b>\n\n"
        f"📊 <b>Results ({total} sessions):</b>\n"
        f"  ✅ Applied:  <b>{len(results['Applied'])}</b>\n"
        f"  ⏭ Skipped:  <b>{len(results['Skipped'])}</b>  (no options enabled)\n"
        f"  ❌ Dead:     <b>{len(results['Dead'])}</b>\n"
        f"  ⚠️ Error:    <b>{len(results['Error'])}</b>"
    )
    status_msg = await smart_edit(status_msg, summary, reply_markup=back_to_profile_apply_kb(), parse_mode="HTML")
