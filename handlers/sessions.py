"""
Sessions handler — List, browse by country, delete, log out, export as ZIP,
split, search, OTP, and bulk actions.
"""
from utils.utils import smart_edit
import os
import re
import time
import asyncio
import shutil
import zipfile
import tempfile
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext

from ui.keyboards import (
    sessions_menu_kb,
    countries_list_kb,
    country_sessions_kb,
    phone_detail_kb,
    confirm_delete_kb,
    confirm_logout_kb,
    confirm_logout_all_kb,
    confirm_logout_all_country_kb,
    confirm_delete_all_country_kb,
    export_status_kb,
    country_export_status_kb,
    search_result_kb,
    otp_result_kb,
    bulk_action_choice_kb,
    back_menu_kb,
    cancel_kb,
    confirm_delete_dead_kb,
)
from utils.country_utils import (
    get_all_sessions,
    get_all_account_statuses,
    get_all_contact_statuses,
    get_all_spam_statuses,
    get_country_display,
    get_total_stats,
    SESSIONS_DIR,
)
from utils.session_lock import session_lock
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client, get_last_otp
from handlers.common import is_admin_async, log_action, is_write_admin_async
from ui.states import SplitState, SearchState, BulkActionState, TwoFactorState
import database.database as db
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest

router = Router()
logger = logging.getLogger(__name__)


# ─── Sessions Menu ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "ses")
async def cb_sessions(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    total, countries = get_total_stats()
    text = (
        "📂 <b>Sessions Management</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 Total: <b>{total}</b> sessions in <b>{countries}</b> countries"
    )
    await smart_edit(callback.message, text, reply_markup=sessions_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Countries List (paginated) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("sl:"))
async def cb_countries_list(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    page = int(callback.data.split(":")[1])
    all_sessions = get_all_sessions()

    if not all_sessions:
        await smart_edit(callback.message, "📭 <b>No sessions found.</b>\n\nRegister a number first!",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    # Build display data: {folder: (flag, name, count)}
    display = {}
    for folder, phones in all_sessions.items():
        flag, name = get_country_display(folder)
        display[folder] = (flag, name, len(phones))

    text = "🌍 <b>Sessions by Country</b>\n━━━━━━━━━━━━━━━━━━━━━\n\nSelect a country:"
    await smart_edit(callback.message, text,
        reply_markup=countries_list_kb(display, page),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Country Detail ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sc:"))
async def cb_country_detail(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[3:]
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    spam_statuses = await get_all_spam_statuses()
    account_statuses = await get_all_account_statuses()
    statuses = {}
    for p in phones:
        acc_status = account_statuses.get(p, "")
        if acc_status.lower() in ("died", "dead", "die", "banned"):
            statuses[p] = "died"
        elif acc_status.lower() == "live":
            statuses[p] = spam_statuses.get(p, "live")
        else:
            statuses[p] = spam_statuses.get(p, "")

    lines = [
        f"{flag} <b>{name}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"\n📱 <b>{len(phones)} session{'s' if len(phones) > 1 else ''}</b>:\n",
    ]

    text = "\n".join(lines)
    await smart_edit(callback.message, text,
        reply_markup=country_sessions_kb(folder, phones, page=0, statuses=statuses),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Country Phones Pagination ───────────────────────────────────────────────

@router.callback_query(F.data.startswith("scp:"))
async def cb_country_page(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    # Data format: scp:folder:page
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    folder = parts[1]
    page = int(parts[2])
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    spam_statuses = await get_all_spam_statuses()
    account_statuses = await get_all_account_statuses()
    statuses = {}
    for p in phones:
        acc_status = account_statuses.get(p, "")
        if acc_status.lower() in ("died", "dead", "die", "banned"):
            statuses[p] = "died"
        elif acc_status.lower() == "live":
            statuses[p] = spam_statuses.get(p, "live")
        else:
            statuses[p] = spam_statuses.get(p, "")

    lines = [
        f"{flag} <b>{name}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"\n📱 <b>{len(phones)} session{'s' if len(phones) > 1 else ''}</b>:\n",
    ]

    text = "\n".join(lines)
    await smart_edit(callback.message, text,
        reply_markup=country_sessions_kb(folder, phones, page=page, statuses=statuses),
        parse_mode="HTML",
    )
    await callback.answer()


def _health_score(spam: str, contact: str, has_2fa: bool, session_mtime: float | None) -> tuple[int, str]:
    """Calculate a 0-100 health score and return (score, bar_string)."""
    if spam in ("FREE", "NEW_REGISTERED") and contact == "NoLimit":
        return 100, "█" * 10
    score = 0
    if spam == "FREE":
        score += 40
    elif spam in ("SPAM",):
        score += 10
    if contact == "NoLimit":
        score += 20
    if has_2fa:
        score += 15
    if session_mtime is not None:
        age_days = (time.time() - session_mtime) / 86400
        if age_days >= 30:
            score += 25
        elif age_days >= 7:
            score += 15
    filled = round(score / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return score, bar


# ─── Phone Detail ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sp:"))
async def cb_phone_detail(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[3:]
    all_sessions = get_all_sessions()

    # Find which folder this phone belongs to
    folder = None
    for f, phones in all_sessions.items():
        if phone in phones:
            folder = f
            break

    if not folder:
        await callback.answer("❌ Session not found", show_alert=True)
        return

    flag, name = get_country_display(folder)

    # Fetch statuses and 2FA for health score
    spam_status = await db.get_spam_status(phone)
    contact_status = await db.get_contact_status(phone)
    account_status = await db.get_account_status(phone)
    twofa_pass = await db.get_2fa(phone)
    has_2fa = bool(twofa_pass)

    session_path = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
    try:
        mtime = os.path.getmtime(session_path) if os.path.exists(session_path) else None
    except OSError:
        mtime = None

    score, bar = _health_score(spam_status, contact_status, has_2fa, mtime)

    spam_icons = {"FREE": "🟢", "SPAM": "🟡", "BANNED": "🔴", "NEW_REGISTERED": "🔵"}
    spam_icon = spam_icons.get(spam_status, "⚪")
    contact_icon = "✅" if contact_status == "NoLimit" else ("⚠️" if contact_status == "Limited" else "❓")
    twofa_icon = "✅" if has_2fa else "❌"

    if account_status in ("died", "Die", "BANNED", "Dead"):
        text = (
            f"📱 <b>Session Details</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{flag} Country: <b>{name}</b>\n"
            f"📞 Phone: <code>+{phone}</code>\n\n"
            f"❌ <b>Session is Invalid (Dead)</b>"
        )
    else:
        text = (
            f"📱 <b>Session Details</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{flag} Country: <b>{name}</b>\n"
            f"📞 Phone: <code>+{phone}</code>\n\n"
            f"🏥 Health: <b>{bar} {score}/100</b>\n"
            f"{spam_icon} Spam: <b>{spam_status or 'Unknown'}</b>  "
            f"{contact_icon} Contact: <b>{contact_status or 'Unknown'}</b>  "
            f"🔐 2FA: {twofa_icon}"
        )

    await smart_edit(callback.message, text,
        reply_markup=phone_detail_kb(phone, folder, account_status, has_2fa),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Statistics ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ss")
async def cb_stats(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    all_sessions = get_all_sessions()
    total = sum(len(v) for v in all_sessions.values())

    lines = [
        "📊 <b>System Statistics</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"👥 <b>Total Sessions:</b> <code>{total}</code>",
        f"🌍 <b>Active Countries:</b> <code>{len(all_sessions)}</code>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    if all_sessions:
        lines.append("📍 <b>Country Breakdown:</b>\n")
        for folder, phones in sorted(all_sessions.items(), key=lambda x: len(x[1]), reverse=True):
            flag, name = get_country_display(folder)
            lines.append(f" ├ {flag} <b>{name}</b> ─ <code>{len(phones)}</code>")
        lines.append(" └ <i>End of List</i>")

    text = "\n".join(lines)
    await smart_edit(callback.message, text, reply_markup=back_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Delete Session ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sd:"))
async def cb_delete_ask(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[3:]
    text = (
        f"🗑 <b>Delete Session?</b>\n\n"
        f"📱 Phone: <code>+{phone}</code>\n\n"
        f"⚠️ This action cannot be undone!"
    )
    await smart_edit(callback.message, text, reply_markup=confirm_delete_kb(phone), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("sdc:"))
async def cb_delete_confirm(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[4:]
    deleted = False

    # Search for .session file in all country folders
    if os.path.exists(SESSIONS_DIR):
        for folder in os.listdir(SESSIONS_DIR):
            folder_path = os.path.join(SESSIONS_DIR, folder)
            if not os.path.isdir(folder_path):
                continue
            session_file = os.path.join(folder_path, f"{phone}.session")
            if os.path.exists(session_file):
                for _path in [session_file, session_file + "-journal"]:
                    if os.path.exists(_path):
                        try:
                            os.remove(_path)
                        except PermissionError:
                            await asyncio.sleep(1)
                            try:
                                os.remove(_path)
                            except PermissionError:
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

    if deleted:
        await smart_edit(callback.message, f"✅ <b>Deleted!</b>\n\n📱 <code>+{phone}</code> session removed.",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    else:
        await smart_edit(callback.message, f"❌ <b>Session not found:</b> <code>+{phone}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    await callback.answer()


# ─── Log Out Session ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lo:"))
async def cb_logout_ask(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[3:]
    text = (
        f"🚪 <b>Log Out Session?</b>\n\n"
        f"📱 Phone: <code>+{phone}</code>\n\n"
        f"⚠️ This will <b>terminate the session on Telegram</b>\n"
        f"and delete the local session file."
    )
    await smart_edit(callback.message, text, reply_markup=confirm_logout_kb(phone), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("loc:"))
async def cb_logout_confirm(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[4:]
    session_file = None

    # Find the session file
    if os.path.exists(SESSIONS_DIR):
        for folder in os.listdir(SESSIONS_DIR):
            folder_path = os.path.join(SESSIONS_DIR, folder)
            if not os.path.isdir(folder_path):
                continue
            candidate = os.path.join(folder_path, f"{phone}.session")
            if os.path.exists(candidate):
                session_file = os.path.join(folder_path, phone)
                break

    if not session_file:
        await smart_edit(callback.message, f"❌ <b>Session not found:</b> <code>+{phone}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await smart_edit(callback.message, f"🚪 <b>Logging out</b> <code>+{phone}</code>...\n\n"
        f"⏳ Connecting to Telegram...",
        parse_mode="HTML",
    )

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    client = None
    try:
        async with session_lock(phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()

            if await client.is_user_authorized():
                await client.log_out()
                logger.info(f"Logged out: +{phone}")
            await client.disconnect()
            client = None
            await asyncio.sleep(0.5)  # Let OS release the file lock (Windows)

            # Delete local session file
            for ext in [".session", ".session-journal"]:
                path = session_file + ext
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except PermissionError:
                        await asyncio.sleep(1)
                        os.remove(path)

        await db.delete_phone_status(phone)

        # Remove folder if empty
        folder_path = os.path.dirname(session_file)
        remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
        if not remaining:
            try:
                os.rmdir(folder_path)
            except OSError:
                pass

        await smart_edit(callback.message, f"✅ <b>Logged out successfully!</b>\n\n"
            f"📱 <code>+{phone}</code>\n\n"
            f"🚪 Session terminated on Telegram\n"
            f"🗑 Local file deleted",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Logout error for +{phone}: {e}")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        await smart_edit(callback.message, f"❌ <b>Logout failed!</b>\n\n"
            f"📱 <code>+{phone}</code>\n"
            f"Error: <code>{type(e).__name__}: {e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )

    await callback.answer()


# ─── Log Out ALL Sessions (Global) ─── 3-step confirmation ───────────────

@router.callback_query(F.data == "loa")
async def cb_logout_all_ask(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    all_sessions = get_all_sessions()
    total = sum(len(v) for v in all_sessions.values())

    if total == 0:
        await callback.answer("📭 No sessions to log out!", show_alert=True)
        return

    text = (
        f"🚪 <b>Log Out All Sessions?</b>\n\n"
        f"📱 Total: <b>{total}</b> sessions\n\n"
        f"⚠️ This will <b>terminate ALL sessions on Telegram</b>\n"
        f"and delete all local session files.\n\n"
        f"❗ Confirmation <b>1/3</b> — Are you sure?"
    )
    await smart_edit(callback.message, text, reply_markup=confirm_logout_all_kb(step=1), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("loag:"))
async def cb_logout_all_step(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    step = int(callback.data.split(":")[1])
    all_sessions = get_all_sessions()
    total = sum(len(v) for v in all_sessions.values())

    if total == 0:
        await callback.answer("📭 No sessions!", show_alert=True)
        return

    if step < 3:
        # Ask for next confirmation
        next_step = step + 1
        text = (
            f"🚪 <b>Log Out All Sessions?</b>\n\n"
            f"📱 Total: <b>{total}</b> sessions\n\n"
            f"⚠️ This will <b>terminate ALL sessions on Telegram</b>\n"
            f"and delete all local session files.\n\n"
            f"❗ Confirmation <b>{next_step}/3</b> — Are you really sure?"
        )
        await smart_edit(callback.message, text, reply_markup=confirm_logout_all_kb(step=next_step), parse_mode="HTML")
        await callback.answer()
        return

    # Step 3 reached — execute logout
    await smart_edit(callback.message, f"🚪 <b>Logging out all sessions...</b>\n\n"
        f"⏳ Processing <b>0/{total}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    success = 0
    failed = 0
    processed = 0

    for folder, phones in all_sessions.items():
        folder_path = os.path.join(SESSIONS_DIR, folder)
        for phone in phones:
            processed += 1
            session_file = os.path.join(folder_path, phone)

            _client = None
            try:
                _client = create_client(session_file, api_id, api_hash, proxy)
                await _client.connect()

                if await _client.is_user_authorized():
                    await _client.log_out()
                await _client.disconnect()
                await asyncio.sleep(0.3)

                for ext in [".session", ".session-journal"]:
                    path = session_file + ext
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except PermissionError:
                            await asyncio.sleep(1)
                            os.remove(path)

                success += 1
                await db.delete_phone_status(phone)
                logger.info(f"Logged out: +{phone} ({processed}/{total})")

            except Exception as e:
                failed += 1
                logger.error(f"Logout failed +{phone}: {e}")
                if _client:
                    try:
                        await _client.disconnect()
                    except Exception:
                        pass
                await asyncio.sleep(0.3)
                for ext in [".session", ".session-journal"]:
                    path = session_file + ext
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                await db.delete_phone_status(phone)

            if processed % 3 == 0 or processed == total:
                try:
                    await smart_edit(callback.message, f"🚪 <b>Logging out all sessions...</b>\n\n"
                        f"⏳ Processing <b>{processed}/{total}</b>...\n"
                        f"✅ Success: {success}  ❌ Failed: {failed}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        if os.path.exists(folder_path):
            remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
            if not remaining:
                try:
                    os.rmdir(folder_path)
                except OSError:
                    pass

    await smart_edit(callback.message, f"✅ <b>Log Out All — Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Total: <b>{total}</b>\n"
        f"✅ Success: <b>{success}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        reply_markup=back_menu_kb(),
        parse_mode="HTML",
    )


# ─── Log Out ALL Sessions (Per Country) ── Choice: All vs Specific ───────

@router.callback_query(F.data.startswith("loac:"))
async def cb_logout_country_all(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[5:]
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    flag, name = get_country_display(folder)
    total = len(all_sessions[folder])

    text = (
        f"🚪 <b>Log Out {flag} {name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Total: <b>{total}</b> sessions\n\n"
        f"Choose an option:"
    )
    await smart_edit(callback.message, text, reply_markup=bulk_action_choice_kb("lo", folder), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("bulk_all_lo:"))
async def cb_bulk_all_logout(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[len("bulk_all_lo:"):]
    all_sessions = get_all_sessions()
    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    total = len(phones)

    text = (
        f"🚪 <b>Log Out All {flag} {name}?</b>\n\n"
        f"📱 Total: <b>{total}</b> sessions\n\n"
        f"❗ Confirmation <b>1/3</b> — Are you sure?"
    )
    await smart_edit(callback.message, text, reply_markup=confirm_logout_all_country_kb(folder, step=1), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("loacc:"))
async def cb_logout_country_step(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    # Data format: loacc:folder:step
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    folder = parts[1]
    step = int(parts[2])
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    total = len(phones)

    if step < 3:
        next_step = step + 1
        text = (
            f"🚪 <b>Log Out All {flag} {name}?</b>\n\n"
            f"📱 Total: <b>{total}</b> sessions\n\n"
            f"⚠️ This will <b>terminate ALL sessions on Telegram</b>\n\n"
            f"❗ Confirmation <b>{next_step}/3</b> — Are you really sure?"
        )
        await smart_edit(callback.message, text, reply_markup=confirm_logout_all_country_kb(folder, step=next_step), parse_mode="HTML")
        await callback.answer()
        return

    # Step 3 reached — execute logout
    await smart_edit(callback.message, f"🚪 <b>Logging out {flag} {name}...</b>\n\n"
        f"⏳ Processing <b>0/{total}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    folder_path = os.path.join(SESSIONS_DIR, folder)

    success = 0
    failed = 0

    for i, phone in enumerate(phones, 1):
        session_file = os.path.join(folder_path, phone)

        _client = None
        try:
            _client = create_client(session_file, api_id, api_hash, proxy)
            await _client.connect()

            if await _client.is_user_authorized():
                await _client.log_out()
            await _client.disconnect()
            await asyncio.sleep(0.3)

            for ext in [".session", ".session-journal"]:
                path = session_file + ext
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except PermissionError:
                        await asyncio.sleep(1)
                        os.remove(path)

            success += 1
            await db.delete_phone_status(phone)
            logger.info(f"Logged out: +{phone} ({i}/{total})")

        except Exception as e:
            failed += 1
            logger.error(f"Logout failed +{phone}: {e}")
            if _client:
                try:
                    await _client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(0.3)
            for ext in [".session", ".session-journal"]:
                path = session_file + ext
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            await db.delete_phone_status(phone)

        if i % 3 == 0 or i == total:
            try:
                await smart_edit(callback.message, f"🚪 <b>Logging out {flag} {name}...</b>\n\n"
                    f"⏳ Processing <b>{i}/{total}</b>...\n"
                    f"✅ Success: {success}  ❌ Failed: {failed}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    # Remove folder if empty
    if os.path.exists(folder_path):
        remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
        if not remaining:
            try:
                os.rmdir(folder_path)
            except OSError:
                pass

    await smart_edit(callback.message, f"✅ <b>{flag} {name} — Log Out Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Total: <b>{total}</b>\n"
        f"✅ Success: <b>{success}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        reply_markup=back_menu_kb(),
        parse_mode="HTML",
    )


# ─── Delete ALL Sessions (Per Country) ── Choice: All vs Specific ────────

@router.callback_query(F.data.startswith("dac:"))
async def cb_delete_country_all(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[4:]
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    flag, name = get_country_display(folder)
    total = len(all_sessions[folder])

    text = (
        f"🗑 <b>Delete {flag} {name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Total: <b>{total}</b> sessions\n\n"
        f"Choose an option:"
    )
    await smart_edit(callback.message, text, reply_markup=bulk_action_choice_kb("da", folder), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("bulk_all_da:"))
async def cb_bulk_all_delete(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[len("bulk_all_da:"):]
    all_sessions = get_all_sessions()
    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    total = len(phones)

    text = (
        f"🗑 <b>Delete All {flag} {name}?</b>\n\n"
        f"📱 Total: <b>{total}</b> sessions\n\n"
        f"⚠️ This will <b>delete all local session files</b>\n\n"
        f"❗ Confirmation <b>1/3</b> — Are you sure?"
    )
    await smart_edit(callback.message, text, reply_markup=confirm_delete_all_country_kb(folder, step=1), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("dacc:"))
async def cb_delete_country_step(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    # Data format: dacc:folder:step
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    folder = parts[1]
    step = int(parts[2])
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    total = len(phones)

    if step < 3:
        next_step = step + 1
        text = (
            f"🗑 <b>Delete All {flag} {name}?</b>\n\n"
            f"📱 Total: <b>{total}</b> sessions\n\n"
            f"⚠️ This will <b>delete all local session files</b>\n\n"
            f"❗ Confirmation <b>{next_step}/3</b> — Are you really sure?"
        )
        await smart_edit(callback.message, text, reply_markup=confirm_delete_all_country_kb(folder, step=next_step), parse_mode="HTML")
        await callback.answer()
        return

    # Step 3 reached — execute delete
    deleted = 0
    folder_path = os.path.join(SESSIONS_DIR, folder)

    for phone in phones:
        for ext in [".session", ".session-journal"]:
            path = os.path.join(folder_path, f"{phone}{ext}")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except PermissionError:
                    await asyncio.sleep(1)
                    try:
                        os.remove(path)
                    except PermissionError:
                        pass
        deleted += 1
        await db.delete_phone_status(phone)

    # Remove folder if empty
    if os.path.exists(folder_path):
        remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
        if not remaining:
            try:
                os.rmdir(folder_path)
            except OSError:
                pass

    await smart_edit(callback.message, f"✅ <b>{flag} {name} — Delete Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🗑 Deleted: <b>{deleted}</b> sessions",
        reply_markup=back_menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Export All Sessions (ZIP) ───────────────────────────────────────────────

@router.callback_query(F.data == "se")
async def cb_export_all(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    all_sessions = get_all_sessions()
    if not all_sessions:
        await callback.answer("📭 No sessions to export!", show_alert=True)
        return

    await callback.answer("📦 Creating ZIP...")

    total = 0
    exported_phones = []
    _tmp_dir = tempfile.mkdtemp(prefix="export_")
    tmp_zip = os.path.join(_tmp_dir, "all_sessions.zip")
    tmp_txt = os.path.join(_tmp_dir, "all_sessions_numbers.txt")

    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, phones in all_sessions.items():
                for phone in phones:
                    session_file = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                    if os.path.exists(session_file):
                        arcname = f"{folder}/{phone}.session"
                        zf.write(session_file, arcname)
                        exported_phones.append(phone)
                        total += 1

        doc = FSInputFile(tmp_zip, filename="all_sessions.zip")
        await callback.message.answer_document(
            doc,
            caption=f"📦 <b>All Sessions Export</b>\n\n📱 {total} sessions exported.",
            parse_mode="HTML",
        )

        # Send phone numbers list as .txt
        if exported_phones:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename="all_sessions_numbers.txt")
            await callback.message.answer_document(
                txt_doc,
                caption=f"📋 <b>Exported Numbers</b>\n\n📱 {total} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Export error: {e}")
        await callback.message.answer(
            f"❌ Export failed: <code>{e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── Export Country Sessions (ZIP) ───────────────────────────────────────────

@router.callback_query(F.data.startswith("sec:"))
async def cb_export_country(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[4:]
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ No sessions for this country", show_alert=True)
        return

    await callback.answer("📦 Creating ZIP...")

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    _tmp_dir = tempfile.mkdtemp(prefix="export_")
    tmp_zip = os.path.join(_tmp_dir, f"{folder}_sessions.zip")
    tmp_txt = os.path.join(_tmp_dir, f"{folder}_sessions_numbers.txt")

    try:
        total = 0
        exported_phones = []
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for phone in phones:
                session_file = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                if os.path.exists(session_file):
                    zf.write(session_file, f"{phone}.session")
                    exported_phones.append(phone)
                    total += 1

        doc = FSInputFile(tmp_zip, filename=f"{folder}_sessions.zip")
        await callback.message.answer_document(
            doc,
            caption=f"📦 <b>{flag} {name} Sessions</b>\n\n📱 {total} sessions exported.",
            parse_mode="HTML",
        )

        # Send phone numbers list as .txt
        if exported_phones:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename=f"{folder}_sessions_numbers.txt")
            await callback.message.answer_document(
                txt_doc,
                caption=f"📋 <b>{flag} {name} — Exported Numbers</b>\n\n📱 {total} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Export error: {e}")
        await callback.message.answer(
            f"❌ Export failed: <code>{e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── Export by Status ────────────────────────────────────────────────────────

@router.callback_query(F.data == "ses_exp_status")
async def cb_export_status_menu(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    text = (
        "📦 <b>Export Sessions by Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose which category to export:"
    )
    await smart_edit(callback.message, text, reply_markup=export_status_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("exp_status:"))
async def cb_export_status(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    target_status = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    all_statuses = await get_all_account_statuses()

    if not all_sessions:
        await callback.answer("📭 No sessions to export!", show_alert=True)
        return

    await callback.answer(f"📦 Creating ZIP for {target_status}...")

    total = 0
    exported_phones = []
    _tmp_dir = tempfile.mkdtemp(prefix="export_")
    tmp_zip = os.path.join(_tmp_dir, f"{target_status}_sessions.zip")
    tmp_txt = os.path.join(_tmp_dir, f"{target_status}_sessions_numbers.txt")

    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, phones in all_sessions.items():
                for phone in phones:
                    # Default status is 'UNKNOWN' if not found, but we match exactly what was stored
                    status = all_statuses.get(phone, "UNKNOWN")
                    if status == target_status:
                        session_file = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                        if os.path.exists(session_file):
                            arcname = f"{folder}/{phone}.session"
                            zf.write(session_file, arcname)
                            exported_phones.append(phone)
                            total += 1

        if total == 0:
            await callback.message.answer(
                f"📭 Found <b>0</b> sessions with status <b>{target_status}</b>.",
                reply_markup=back_menu_kb(),
                parse_mode="HTML",
            )
            return

        doc = FSInputFile(tmp_zip, filename=f"{target_status}_sessions.zip")
        await callback.message.answer_document(
            doc,
            caption=f"📦 <b>{target_status} Sessions Export</b>\n\n📱 {total} sessions exported.",
            parse_mode="HTML",
        )

        # Send phone numbers list as .txt
        if exported_phones:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename=f"{target_status}_sessions_numbers.txt")
            await callback.message.answer_document(
                txt_doc,
                caption=f"📋 <b>{target_status} — Exported Numbers</b>\n\n📱 {total} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Status export error: {e}")
        await callback.message.answer(
            f"❌ Export failed: <code>{e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)

# ─── Export Country by Status ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("c_es:"))
async def cb_country_export_status_menu(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[5:]
    flag, name = get_country_display(folder)

    text = (
        f"📦 <b>Export {flag} {name} by Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose which category to export:"
    )
    await smart_edit(callback.message, text, reply_markup=country_export_status_kb(folder), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("ce:"))
async def cb_country_export_status(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    # Data format: ce:STATUS:folder_name
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    target_status = parts[1]
    folder = ":".join(parts[2:])
    all_sessions = get_all_sessions()
    all_statuses = await get_all_account_statuses()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found!", show_alert=True)
        return

    phones = all_sessions[folder]
    if not phones:
        await callback.answer("📭 No sessions in this country!", show_alert=True)
        return

    flag, name = get_country_display(folder)
    await callback.answer(f"📦 Creating ZIP for {target_status}...")

    total = 0
    exported_phones = []
    _tmp_dir = tempfile.mkdtemp(prefix="export_")
    tmp_zip = os.path.join(_tmp_dir, f"{folder}_{target_status}_sessions.zip")
    tmp_txt = os.path.join(_tmp_dir, f"{folder}_{target_status}_numbers.txt")

    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for phone in phones:
                status = all_statuses.get(phone, "UNKNOWN")
                if status == target_status:
                    session_file = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                    if os.path.exists(session_file):
                        zf.write(session_file, f"{phone}.session")
                        exported_phones.append(phone)
                        total += 1

        if total == 0:
            await callback.message.answer(
                f"📭 Found <b>0</b> sessions with status <b>{target_status}</b> in {flag} {name}.",
                reply_markup=back_menu_kb(),
                parse_mode="HTML",
            )
            return

        doc = FSInputFile(tmp_zip, filename=f"{folder}_{target_status}_sessions.zip")
        await callback.message.answer_document(
            doc,
            caption=f"📦 <b>{flag} {name} - {target_status}</b>\n\n📱 {total} sessions exported.",
            parse_mode="HTML",
        )

        # Send phone numbers list as .txt
        if exported_phones:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename=f"{folder}_{target_status}_numbers.txt")
            await callback.message.answer_document(
                txt_doc,
                caption=f"📋 <b>{flag} {name} - {target_status} Numbers</b>\n\n📱 {total} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Country status export error: {e}")
        await callback.message.answer(
            f"❌ Export failed: <code>{e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── Export by Contact Status (Global) ───────────────────────────────────────

@router.callback_query(F.data.startswith("exp_cstatus:"))
async def cb_export_contact_status(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    target_status = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    contact_statuses = await get_all_contact_statuses()

    if not all_sessions:
        await callback.answer("📭 No sessions to export!", show_alert=True)
        return

    await callback.answer(f"📦 Creating ZIP for Contact {target_status}...")

    total = 0
    exported_phones = []
    _tmp_dir = tempfile.mkdtemp(prefix="export_")
    tmp_zip = os.path.join(_tmp_dir, f"contact_{target_status}_sessions.zip")
    tmp_txt = os.path.join(_tmp_dir, f"contact_{target_status}_numbers.txt")

    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, phones in all_sessions.items():
                for phone in phones:
                    status = contact_statuses.get(phone, "UNKNOWN")
                    if status == target_status:
                        session_file = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                        if os.path.exists(session_file):
                            arcname = f"{folder}/{phone}.session"
                            zf.write(session_file, arcname)
                            exported_phones.append(phone)
                            total += 1

        if total == 0:
            await callback.message.answer(
                f"📭 Found <b>0</b> sessions with contact status <b>{target_status}</b>.",
                reply_markup=back_menu_kb(),
                parse_mode="HTML",
            )
            return

        doc = FSInputFile(tmp_zip, filename=f"contact_{target_status}_sessions.zip")
        await callback.message.answer_document(
            doc,
            caption=f"📦 <b>Contact {target_status} Sessions</b>\n\n📱 {total} sessions exported.",
            parse_mode="HTML",
        )

        if exported_phones:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename=f"contact_{target_status}_numbers.txt")
            await callback.message.answer_document(
                txt_doc,
                caption=f"📋 <b>Contact {target_status} — Numbers</b>\n\n📱 {total} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Contact status export error: {e}")
        await callback.message.answer(
            f"❌ Export failed: <code>{e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── Export Country by Contact Status ────────────────────────────────────────

@router.callback_query(F.data.startswith("ce_c:"))
async def cb_country_export_contact_status(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    target_status = parts[1]
    folder = ":".join(parts[2:])
    all_sessions = get_all_sessions()
    contact_statuses = await get_all_contact_statuses()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found!", show_alert=True)
        return

    phones = all_sessions[folder]
    if not phones:
        await callback.answer("📭 No sessions in this country!", show_alert=True)
        return

    flag, name = get_country_display(folder)
    await callback.answer(f"📦 Creating ZIP for Contact {target_status}...")

    total = 0
    exported_phones = []
    _tmp_dir = tempfile.mkdtemp(prefix="export_")
    tmp_zip = os.path.join(_tmp_dir, f"{folder}_contact_{target_status}.zip")
    tmp_txt = os.path.join(_tmp_dir, f"{folder}_contact_{target_status}_numbers.txt")

    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for phone in phones:
                status = contact_statuses.get(phone, "UNKNOWN")
                if status == target_status:
                    session_file = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                    if os.path.exists(session_file):
                        zf.write(session_file, f"{phone}.session")
                        exported_phones.append(phone)
                        total += 1

        if total == 0:
            await callback.message.answer(
                f"📭 Found <b>0</b> sessions with contact status <b>{target_status}</b> in {flag} {name}.",
                reply_markup=back_menu_kb(),
                parse_mode="HTML",
            )
            return

        doc = FSInputFile(tmp_zip, filename=f"{folder}_contact_{target_status}.zip")
        await callback.message.answer_document(
            doc,
            caption=f"📦 <b>{flag} {name} - Contact {target_status}</b>\n\n📱 {total} sessions exported.",
            parse_mode="HTML",
        )

        if exported_phones:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename=f"{folder}_contact_{target_status}_numbers.txt")
            await callback.message.answer_document(
                txt_doc,
                caption=f"📋 <b>{flag} {name} - Contact {target_status} Numbers</b>\n\n📱 {total} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Country contact status export error: {e}")
        await callback.message.answer(
            f"❌ Export failed: <code>{e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── Split (Country) ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("spl:"))
async def cb_split_country(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[4:]
    all_sessions = get_all_sessions()
    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)

    await state.set_state(SplitState.count)
    await state.update_data(split_folder=folder, split_status=None)

    text = (
        f"✂️ <b>Split {flag} {name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Available: <b>{len(phones)}</b> sessions\n\n"
        f"Send the number of sessions to extract (max {len(phones)}):"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Split (By Status) ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("spls:"))
async def cb_split_status(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    # Format: spls:STATUS:folder
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    status = parts[1]
    folder = ":".join(parts[2:])
    all_sessions = get_all_sessions()
    all_statuses = await get_all_account_statuses()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    # Count matching sessions
    matching = [p for p in all_sessions[folder] if all_statuses.get(p, "UNKNOWN") == status]
    if not matching:
        await callback.answer(f"📭 No {status} sessions!", show_alert=True)
        return

    flag, name = get_country_display(folder)
    await state.set_state(SplitState.count)
    await state.update_data(split_folder=folder, split_status=status)

    text = (
        f"✂️ <b>Split {flag} {name} — {status}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Available: <b>{len(matching)}</b> sessions\n\n"
        f"Send the number of sessions to extract (max {len(matching)}):"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Split: Receive Count ────────────────────────────────────────────────────

@router.message(SplitState.count)
async def msg_split_count(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    data = await state.get_data()
    folder = data.get("split_folder")
    split_status = data.get("split_status")

    if not message.text or not message.text.strip().isdigit():
        await message.answer("❌ Please send a valid number.", reply_markup=cancel_kb())
        return

    count = int(message.text.strip())
    all_sessions = get_all_sessions()
    all_statuses = await get_all_account_statuses()

    if folder not in all_sessions:
        await message.answer("❌ Country not found.", reply_markup=back_menu_kb())
        await state.clear()
        return

    phones = all_sessions[folder]
    if split_status:
        phones = [p for p in phones if all_statuses.get(p, "UNKNOWN") == split_status]

    if count < 1 or count > len(phones):
        await message.answer(
            f"❌ Invalid number. Enter between <b>1</b> and <b>{len(phones)}</b>.",
            reply_markup=cancel_kb(), parse_mode="HTML",
        )
        return

    await state.clear()
    selected = phones[:count]
    flag, name = get_country_display(folder)
    status_label = f"_{split_status}" if split_status else ""
    _tmp_dir = tempfile.mkdtemp(prefix="export_")
    tmp_zip = os.path.join(_tmp_dir, f"{folder}{status_label}_split_{count}.zip")

    try:
        exported_phones = []
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for phone in selected:
                session_file = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                if os.path.exists(session_file):
                    zf.write(session_file, f"{phone}.session")
                    exported_phones.append(phone)

        doc = FSInputFile(tmp_zip, filename=f"{folder}{status_label}_split_{count}.zip")
        caption = f"✂️ <b>{flag} {name}{' — ' + split_status if split_status else ''}</b>\n\n📱 {count} sessions extracted."
        await message.answer_document(doc, caption=caption, parse_mode="HTML")

        # Send phone numbers list as .txt
        if exported_phones:
            tmp_txt = os.path.join(_tmp_dir, f"{folder}{status_label}_split_{count}_numbers.txt")
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename=f"{folder}{status_label}_split_{count}_numbers.txt")
            await message.answer_document(
                txt_doc,
                caption=f"📋 <b>Split Numbers{' — ' + split_status if split_status else ''}</b>\n\n📱 {len(exported_phones)} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Split error: {e}")
        await message.answer(f"❌ Split failed: <code>{e}</code>", reply_markup=back_menu_kb(), parse_mode="HTML")
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── Search ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "search")
async def cb_search(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(SearchState.phone)
    text = (
        "🔍 <b>Search Session</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the phone number to search for\n"
        "(with or without +):"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(SearchState.phone)
async def msg_search_phone(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        return

    phone = message.text.strip().lstrip("+").replace(" ", "")
    if not phone.isdigit():
        await message.answer("❌ Invalid phone number. Send digits only.", reply_markup=cancel_kb())
        return

    await state.clear()
    all_sessions = get_all_sessions()

    # Search for the phone in all folders
    found_folder = None
    for folder, phones in all_sessions.items():
        if phone in phones:
            found_folder = folder
            break

    if not found_folder:
        await message.answer(
            f"📭 <b>Not Found</b>\n\n"
            f"📱 <code>+{phone}</code> is not in any session.",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )
        return

    flag, name = get_country_display(found_folder)
    account_status = await db.get_account_status(phone)
    text = (
        f"✅ <b>Session Found!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{flag} Country: <b>{name}</b>\n"
        f"📱 Phone: <code>+{phone}</code>\n\n"
        f"Choose an action:"
    )
    await message.answer(text, reply_markup=search_result_kb(phone, found_folder, account_status), parse_mode="HTML")


# ─── OTP (Get Last Verification Code) ────────────────────────────────────────

@router.callback_query(F.data.startswith("otp:"))
async def cb_get_otp(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[4:]
    all_sessions = get_all_sessions()

    # Find folder
    folder = None
    for f, phones in all_sessions.items():
        if phone in phones:
            folder = f
            break

    if not folder:
        await callback.answer("❌ Session not found", show_alert=True)
        return

    session_file = os.path.join(SESSIONS_DIR, folder, phone)
    flag, name = get_country_display(folder)

    await smart_edit(callback.message, f"📨 <b>Fetching OTP...</b>\n\n"
        f"{flag} {name} | <code>+{phone}</code>\n\n"
        f"⏳ Connecting to Telegram...",
        parse_mode="HTML",
    )
    await callback.answer()

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    try:
        async with session_lock(phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                for ext in [".session", ".session-journal"]:
                    if os.path.exists(session_file + ext):
                        os.remove(session_file + ext)
                await db.delete_phone_status(phone)
                await smart_edit(callback.message, f"❌ <b>Session not authorized (Deleted automatically)</b>\n\n<code>+{phone}</code>",
                    reply_markup=back_menu_kb(), parse_mode="HTML",
                )
                return

            found, result_text = await get_last_otp(client)
            await client.disconnect()

        account_status = await db.get_account_status(phone)
        text = (
            f"📨 <b>OTP Result</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{flag} {name} | <code>+{phone}</code>\n\n"
            f"{result_text}"
        )
        await smart_edit(callback.message, text, reply_markup=otp_result_kb(phone, found, account_status), parse_mode="HTML",
        )

    except Exception as e:
        import traceback
        logger.error(f"OTP error for +{phone}: {e}\n{traceback.format_exc()}")
        await smart_edit(callback.message, f"❌ <b>OTP fetch failed!</b>\n\n"
            f"<code>+{phone}</code>\n"
            f"Error: <code>{type(e).__name__}: {e}</code>",
            reply_markup=otp_result_kb(phone, False, await db.get_account_status(phone)), parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("otp_keep:"))
async def cb_otp_keep(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer("✅ Session kept!", show_alert=True)
    await smart_edit(callback.message, "✅ <b>Session kept.</b>\n\nReturning to menu...",
        reply_markup=back_menu_kb(), parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("otp_lo:"))
async def cb_otp_logout(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[7:]
    all_sessions = get_all_sessions()
    folder = None
    for f, phones in all_sessions.items():
        if phone in phones:
            folder = f
            break

    if not folder:
        await callback.answer("❌ Session not found", show_alert=True)
        return

    session_file = os.path.join(SESSIONS_DIR, folder, phone)
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    _client = None
    try:
        async with session_lock(phone):
            _client = create_client(session_file, api_id, api_hash, proxy)
            await _client.connect()
            if await _client.is_user_authorized():
                await _client.log_out()
            await _client.disconnect()
            _client = None
            await asyncio.sleep(0.5)

            for ext in [".session", ".session-journal"]:
                path = session_file + ext
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except PermissionError:
                        await asyncio.sleep(1)
                        os.remove(path)

        await db.delete_phone_status(phone)

        if os.path.exists(os.path.join(SESSIONS_DIR, folder)):
            remaining = [f for f in os.listdir(os.path.join(SESSIONS_DIR, folder)) if f.endswith(".session")]
            if not remaining:
                try:
                    os.rmdir(os.path.join(SESSIONS_DIR, folder))
                except OSError:
                    pass

        await smart_edit(callback.message, f"✅ <b>Logged out & deleted!</b>\n\n<code>+{phone}</code>",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"OTP logout error: {e}")
        if _client:
            try:
                await _client.disconnect()
            except Exception:
                pass
        await smart_edit(callback.message, f"❌ Logout failed: <code>{e}</code>",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )
    await callback.answer()


# ─── Other Sessions (View & Terminate foreign sessions) ──────────────────────

@router.callback_query(F.data.startswith("other_sess:"))
async def cb_other_sessions(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[len("other_sess:"):]
    all_sessions = get_all_sessions()

    # Find the session file
    session_file = None
    for folder, phones in all_sessions.items():
        if phone in phones:
            session_file = os.path.join(SESSIONS_DIR, folder, phone)
            break

    if not session_file:
        await callback.answer("❌ Session not found", show_alert=True)
        return

    await smart_edit(callback.message, f"📋 <b>Fetching other sessions...</b>\n\n"
        f"<code>+{phone}</code>\n\n"
        f"⏳ Connecting to Telegram...",
        parse_mode="HTML",
    )
    await callback.answer()

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    client = None
    try:
        async with session_lock(phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                await smart_edit(callback.message, f"❌ <b>Session not authorized</b>\n\n<code>+{phone}</code>",
                    reply_markup=back_menu_kb(), parse_mode="HTML",
                )
                return

            result = await client(GetAuthorizationsRequest())
            authorizations = result.authorizations
            await client.disconnect()

        lines = [
            f"📋 <b>Active Sessions for</b> <code>+{phone}</code>",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"\n📱 <b>{len(authorizations)}</b> session(s) found:\n",
        ]

        for i, auth in enumerate(authorizations, 1):
            current_tag = " ⭐ <b>(Current)</b>" if auth.current else ""
            created = auth.date_created.strftime("%Y-%m-%d %H:%M UTC") if auth.date_created else "N/A"
            active = auth.date_active.strftime("%Y-%m-%d %H:%M UTC") if auth.date_active else "N/A"

            lines.append(f"<b>#{i}</b>{current_tag}")
            lines.append(f"  📱 Device: <code>{auth.device_model}</code>")
            lines.append(f"  💻 Platform: <code>{auth.platform}</code>")
            lines.append(f"  🖥 System: <code>{auth.system_version}</code>")
            lines.append(f"  📦 App: <code>{auth.app_name}</code>")
            lines.append(f"  📅 Created: {created}")
            lines.append(f"  🕐 Last Active: {active}")
            if auth.ip:
                lines.append(f"  🌐 IP: <code>{auth.ip}</code>")
            if auth.country:
                lines.append(f"  🏳️ Country: {auth.country}")
            lines.append("")

        text = "\n".join(lines)

        # Build keyboard
        other_count = sum(1 for a in authorizations if not a.current)
        kb_rows = []
        if other_count > 0:
            kb_rows.append([InlineKeyboardButton(text=f"🚪 Log Out Other Sessions ({other_count})", callback_data=f"lo_other:{phone}")])
        kb_rows.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"sp:{phone}")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

        await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Other sessions error for +{phone}: {e}")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        await smart_edit(callback.message, f"❌ <b>Failed to fetch sessions!</b>\n\n"
            f"<code>+{phone}</code>\n"
            f"Error: <code>{type(e).__name__}: {e}</code>",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("lo_other:"))
async def cb_logout_other_sessions(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data[len("lo_other:"):]
    all_sessions = get_all_sessions()

    # Find the session file
    session_file = None
    for folder, phones in all_sessions.items():
        if phone in phones:
            session_file = os.path.join(SESSIONS_DIR, folder, phone)
            break

    if not session_file:
        await callback.answer("❌ Session not found", show_alert=True)
        return

    await smart_edit(callback.message, f"🚪 <b>Terminating other sessions...</b>\n\n"
        f"<code>+{phone}</code>\n\n"
        f"⏳ Connecting to Telegram...",
        parse_mode="HTML",
    )
    await callback.answer()

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    client = None
    try:
        async with session_lock(phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                await smart_edit(callback.message, f"❌ <b>Session not authorized</b>\n\n<code>+{phone}</code>",
                    reply_markup=back_menu_kb(), parse_mode="HTML",
                )
                return

            result = await client(GetAuthorizationsRequest())
            authorizations = result.authorizations

            terminated = 0
            for auth in authorizations:
                if not auth.current:
                    try:
                        await client(ResetAuthorizationRequest(hash=auth.hash))
                        terminated += 1
                    except Exception as e:
                        logger.warning(f"Failed to terminate session hash={auth.hash}: {e}")

            await client.disconnect()

        await smart_edit(callback.message, f"✅ <b>Other Sessions Terminated!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 <code>+{phone}</code>\n"
            f"🚪 Terminated: <b>{terminated}</b> session(s)\n\n"
            f"⭐ Your bot session is still active.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 View Sessions", callback_data=f"other_sess:{phone}")],
                [InlineKeyboardButton(text="🔙 Back", callback_data=f"sp:{phone}")],
            ]),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Logout other sessions error for +{phone}: {e}")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        await smart_edit(callback.message, f"❌ <b>Failed to terminate sessions!</b>\n\n"
            f"<code>+{phone}</code>\n"
            f"Error: <code>{type(e).__name__}: {e}</code>",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )


# ─── Bulk Specific (Log Out / Delete specific phones) ────────────────────────

@router.callback_query(F.data.startswith("bulk_sp_lo:"))
async def cb_bulk_specific_logout(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[len("bulk_sp_lo:"):]
    await state.set_state(BulkActionState.phones)
    await state.update_data(bulk_folder=folder, bulk_action="logout")

    text = (
        "📝 <b>Specific Numbers — Log Out</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the phone numbers to log out.\n\n"
        "You can:\n"
        "• Send numbers as text (one per line)\n"
        "• Send a .txt file with numbers\n\n"
        "Format: with or without +"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("bulk_sp_da:"))
async def cb_bulk_specific_delete(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[len("bulk_sp_da:"):]
    await state.set_state(BulkActionState.phones)
    await state.update_data(bulk_folder=folder, bulk_action="delete")

    text = (
        "📝 <b>Specific Numbers — Delete</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the phone numbers to delete.\n\n"
        "You can:\n"
        "• Send numbers as text (one per line)\n"
        "• Send a .txt file with numbers\n\n"
        "Format: with or without +"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(BulkActionState.phones)
async def msg_bulk_phones(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    data = await state.get_data()
    folder = data.get("bulk_folder")
    action = data.get("bulk_action")  # "logout" or "delete"

    # Parse phone numbers from text or file
    phone_list = []
    if message.document and message.document.file_name.endswith(".txt"):
        import io
        bot = message.bot
        file = await bot.download(message.document)
        content = file.read().decode("utf-8", errors="ignore")
        phone_list = [line.strip().lstrip("+").replace(" ", "") for line in content.splitlines() if line.strip()]
    elif message.text:
        phone_list = [line.strip().lstrip("+").replace(" ", "") for line in message.text.splitlines() if line.strip()]

    # Filter valid numbers
    phone_list = [p for p in phone_list if p.isdigit() and len(p) >= 7]

    if not phone_list:
        await message.answer("❌ No valid phone numbers found. Try again.", reply_markup=cancel_kb())
        return

    await state.clear()

    all_sessions = get_all_sessions()
    if folder not in all_sessions:
        await message.answer("❌ Country not found.", reply_markup=back_menu_kb())
        return

    available = all_sessions[folder]
    matched = [p for p in phone_list if p in available]
    not_found = [p for p in phone_list if p not in available]

    if not matched:
        await message.answer(
            f"❌ None of the {len(phone_list)} numbers were found in this country.",
            reply_markup=back_menu_kb(),
        )
        return

    flag, name = get_country_display(folder)
    total = len(matched)

    if action == "delete":
        deleted = 0
        for phone in matched:
            for ext in [".session", ".session-journal"]:
                path = os.path.join(SESSIONS_DIR, folder, f"{phone}{ext}")
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except PermissionError:
                        await asyncio.sleep(1)
                        try:
                            os.remove(path)
                        except PermissionError:
                            pass
            deleted += 1
            await db.delete_phone_status(phone)

        # Clean empty folder
        folder_path = os.path.join(SESSIONS_DIR, folder)
        if os.path.exists(folder_path):
            remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
            if not remaining:
                try:
                    os.rmdir(folder_path)
                except OSError:
                    pass

        text = (
            f"✅ <b>{flag} {name} — Delete Complete!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🗑 Deleted: <b>{deleted}</b>\n"
        )
        if not_found:
            text += f"⚠️ Not found: <b>{len(not_found)}</b>"
        await message.answer(text, reply_markup=back_menu_kb(), parse_mode="HTML")

    elif action == "logout":
        api_id, api_hash = await get_api_credentials()
        proxy = await get_proxy()
        success = 0
        failed = 0

        status_msg = await message.answer(
            f"🚪 <b>Logging out {total} sessions...</b>\n\n⏳ Processing...",
            parse_mode="HTML",
        )

        for i, phone in enumerate(matched, 1):
            session_file = os.path.join(SESSIONS_DIR, folder, phone)
            _client = None
            try:
                _client = create_client(session_file, api_id, api_hash, proxy)
                await _client.connect()
                if await _client.is_user_authorized():
                    await _client.log_out()
                await _client.disconnect()
                await asyncio.sleep(0.3)
                for ext in [".session", ".session-journal"]:
                    path = session_file + ext
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except PermissionError:
                            await asyncio.sleep(1)
                            os.remove(path)
                success += 1
                await db.delete_phone_status(phone)
            except Exception as e:
                failed += 1
                logger.error(f"Bulk logout failed +{phone}: {e}")
                if _client:
                    try:
                        await _client.disconnect()
                    except Exception:
                        pass
                await asyncio.sleep(0.3)
                for ext in [".session", ".session-journal"]:
                    path = session_file + ext
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                await db.delete_phone_status(phone)

            if i % 3 == 0 or i == total:
                try:
                    status_msg = await smart_edit(status_msg, f"🚪 <b>Logging out...</b>\n\n"
                        f"⏳ {i}/{total}\n"
                        f"✅ {success}  ❌ {failed}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        # Clean empty folder
        folder_path = os.path.join(SESSIONS_DIR, folder)
        if os.path.exists(folder_path):
            remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
            if not remaining:
                try:
                    os.rmdir(folder_path)
                except OSError:
                    pass

        text = (
            f"✅ <b>{flag} {name} — Log Out Complete!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Success: <b>{success}</b>\n"
            f"❌ Failed: <b>{failed}</b>\n"
        )
        if not_found:
            text += f"⚠️ Not found: <b>{len(not_found)}</b>"
        status_msg = await smart_edit(status_msg, text, reply_markup=back_menu_kb(), parse_mode="HTML")


# ─── Delete All Dead Sessions ───────────────────────────────────────────────

@router.callback_query(F.data == "del_dead")
async def cb_del_dead_ask(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    from workers.session_worker import create_client, check_session_alive

    all_sessions = get_all_sessions()
    total = sum(len(v) for v in all_sessions.values())

    if total == 0:
        await callback.answer("💭 No sessions found.", show_alert=True)
        return

    # Quick count of dead sessions from DB statuses
    import database as _db
    statuses = await get_all_account_statuses()
    dead_in_db = sum(1 for s in statuses.values() if s in ("BANNED", "Die", "Dead", "died"))

    text = (
        f"🧹 <b>Delete Dead Sessions</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Sessions:</b> <code>{total}</code>\n"
        f"❌ <b>Known Dead:</b> <code>{dead_in_db}</code>\n\n"
        f"⚠️ <i>This action will permanently delete all sessions that failed the last check (BANNED/Die).</i>\n"
        f"💡 <i>Tip: Run a fresh check first for accurate results.</i>"
    )
    await smart_edit(callback.message, text, reply_markup=confirm_delete_dead_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "del_dead_confirm")
async def cb_del_dead_confirm(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    import database as _db
    statuses = await get_all_account_statuses()
    dead_phones = {p for p, s in statuses.items() if s in ("BANNED", "Die", "Dead", "died")}

    if not dead_phones:
        await smart_edit(callback.message, "✅ <b>No dead sessions found in records.</b>\n"
            "Run a check first to identify dead sessions.",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )
        await callback.answer()
        return

    await smart_edit(callback.message, f"🧹 <b>Deleting {len(dead_phones)} dead sessions...</b>",
        parse_mode="HTML",
    )
    await callback.answer()

    deleted = 0
    not_found_count = 0
    all_sessions = get_all_sessions()

    for folder, phones in all_sessions.items():
        folder_path = os.path.join(SESSIONS_DIR, folder)
        for phone in phones:
            if phone in dead_phones:
                for ext in [".session", ".session-journal"]:
                    path = os.path.join(folder_path, phone + ext)
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                            deleted += 1
                        except Exception as e:
                            logger.error(f"Failed to delete dead {phone}: {e}")
                    else:
                        not_found_count += 1

                # Remove DB entries
                await db.delete_phone_status(phone)

        # Remove empty folder
        if os.path.exists(folder_path):
            remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
            if not remaining:
                try:
                    os.rmdir(folder_path)
                except OSError:
                    pass

    await log_action(
        callback.from_user.id, "DELETE_DEAD",
        f"Deleted {deleted} dead sessions (not found on disk: {not_found_count})"
    )
    logger.info(f"Delete dead: {deleted} sessions removed by {callback.from_user.id}")

    await smart_edit(callback.message, f"✅ <b>Dead Sessions Cleanup Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Targeted: <b>{len(dead_phones)}</b>\n"
        f"🗑 Deleted: <b>{deleted // 1}</b> session files\n"
        f"⚠️ Not on disk: <b>{not_found_count}</b>",
        reply_markup=back_menu_kb(), parse_mode="HTML",
    )

# ─── 2FA Setup ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("set2fa:"))
async def cb_set2fa(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    
    # Check if session exists
    folder = None
    for f, phones in all_sessions.items():
        if phone in phones:
            folder = f
            break
            
    if not folder:
        await callback.answer("❌ Session not found.", show_alert=True)
        return

    await state.update_data(target_phone=phone, target_folder=folder)
    await state.set_state(TwoFactorState.waiting_password)
    
    await smart_edit(callback.message, f"🔑 <b>Set 2FA Password</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Phone: <b>+{phone}</b>\n\n"
        f"Please send me the new 2FA password for this session:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ]), parse_mode="HTML"
    )
    await callback.answer()

@router.message(TwoFactorState.waiting_password)
async def msg_set2fa_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    password = message.text.strip()
    data = await state.get_data()
    phone = data.get("target_phone")
    folder = data.get("target_folder")
    await state.clear()
    
    status_msg = await message.answer(f"⏳ Setting 2FA for <b>+{phone}</b>...", parse_mode="HTML")
    
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    session_path = os.path.join(SESSIONS_DIR, folder, phone)
    
    try:
        async with session_lock(phone):
            client = create_client(session_path, api_id, api_hash, proxy)
            await client.connect()
            if not await client.is_user_authorized():
                await smart_edit(status_msg, "❌ Session is dead.", parse_mode="HTML")
                await client.disconnect()
                return

            await client.edit_2fa(new_password=password)
            await client.disconnect()
        await smart_edit(status_msg, f"✅ <b>2FA Password Set!</b>\n\n"
            f"📱 Phone: <b>+{phone}</b>\n"
            f"🔑 New Password: <code>{password}</code>", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back to Session", callback_data=f"sp:{phone}")]
            ])
        )
    except Exception as e:
        logger.error(f"Set 2FA Error: {e}")


# ─── 2FA Setup ─────────────────────────────────────────────────────────────

from ui.keyboards import manage_2fa_kb

@router.callback_query(F.data.startswith("manage2fa:"))
async def cb_manage_2fa(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
        
    phone = callback.data.split(":")[1]
    await smart_edit(callback.message, f"🔑 <b>Manage 2FA for +{phone}</b>\n\nChoose an action:",
        reply_markup=manage_2fa_kb(phone), parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("view2fa:"))
async def cb_view2fa(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        return

    phone = callback.data.split(":")[1]
    password = await db.get_2fa(phone)
    if password:
        await callback.answer(f"🔑 Password: {password}", show_alert=True)
    else:
        await callback.answer("📭 No 2FA password stored for this session.", show_alert=True)

@router.callback_query(F.data.startswith("set2fa:"))
async def cb_set2fa(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    
    # Check if session exists
    folder = None
    for f, phones in all_sessions.items():
        if phone in phones:
            folder = f
            break
            
    if not folder:
        await callback.answer("❌ Session not found.", show_alert=True)
        return

    await state.update_data(target_phone=phone, target_folder=folder)
    await state.set_state(TwoFactorState.waiting_password)
    
    await smart_edit(callback.message, f"➕ <b>Set New 2FA Password</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Phone: <b>+{phone}</b>\n\n"
        f"Please send me the new 2FA password for this session:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ]), parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("update2fa:"))
async def cb_update2fa(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        return
        
    phone = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    folder = next((f for f, p in all_sessions.items() if phone in p), None)
    if not folder:
        await callback.answer("❌ Session not found.", show_alert=True)
        return

    await state.update_data(target_phone=phone, target_folder=folder)
    await state.set_state(TwoFactorState.waiting_update_password)
    await smart_edit(callback.message, f"🔄 <b>Update 2FA Password</b>\n\n"
        f"Please send the <b>Current Password</b> and <b>New Password</b> separated by a comma (,)\n\n"
        f"<i>Example:</i> <code>oldpass123,newpass456</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ]), parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("delete2fa:"))
async def cb_delete2fa(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        return
        
    phone = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    folder = next((f for f, p in all_sessions.items() if phone in p), None)
    if not folder:
        await callback.answer("❌ Session not found.", show_alert=True)
        return

    await state.update_data(target_phone=phone, target_folder=folder)
    await state.set_state(TwoFactorState.waiting_delete_password)
    await smart_edit(callback.message, f"🗑 <b>Delete 2FA Password</b>\n\n"
        f"Please send the <b>Current Password</b> to remove 2FA:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ]), parse_mode="HTML"
    )
    await callback.answer()

@router.message(TwoFactorState.waiting_password)
@router.message(TwoFactorState.waiting_update_password)
@router.message(TwoFactorState.waiting_delete_password)
async def msg_manage_2fa_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    input_text = message.text.strip()
    data = await state.get_data()
    phone = data.get("target_phone")
    folder = data.get("target_folder")
    
    current_state = await state.get_state()
    await state.clear()
    
    current_password = None
    new_password = None
    action_label = "Setting"
    
    if current_state == TwoFactorState.waiting_password.state:
        new_password = input_text
        action_label = "Setting"
    elif current_state == TwoFactorState.waiting_update_password.state:
        if "," not in input_text:
            await message.answer("❌ Invalid format. Use: <code>old_password,new_password</code>", parse_mode="HTML")
            return
        parts = input_text.split(",", 1)
        current_password = parts[0].strip()
        new_password = parts[1].strip()
        action_label = "Updating"
    elif current_state == TwoFactorState.waiting_delete_password.state:
        current_password = input_text
        new_password = None
        action_label = "Deleting"
    
    status_msg = await message.answer(f"⏳ {action_label} 2FA for <b>+{phone}</b>...", parse_mode="HTML")
    
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    session_path = os.path.join(SESSIONS_DIR, folder, phone)
    
    client = None
    try:
        async with session_lock(phone):
            client = create_client(session_path, api_id, api_hash, proxy)
            await client.connect()
            if not await client.is_user_authorized():
                await smart_edit(status_msg, "❌ Session is dead.", parse_mode="HTML")
                await client.disconnect()
                client = None
                return

            await client.edit_2fa(current_password=current_password, new_password=new_password)

            success_text = f"✅ <b>2FA {action_label} Complete!</b>\n\n📱 Phone: <b>+{phone}</b>\n"
            if new_password:
                await db.set_2fa(phone, new_password)
                success_text += f"🔑 New Password: <code>{new_password}</code>"
            else:
                await db.delete_2fa(phone)
                success_text += "🔑 Password has been removed."

        await smart_edit(status_msg, success_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back to Session", callback_data=f"sp:{phone}")]
            ])
        )
    except Exception as e:
        logger.error(f"2FA Error: {e}")
        await smart_edit(status_msg, f"❌ Failed: <code>{type(e).__name__}: {e}</code>", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back to Session", callback_data=f"sp:{phone}")]
            ])
        )
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


# ─── Delete All Dead Sessions ───────────────────────────────────────────────

@router.callback_query(F.data == "del_dead")
async def cb_del_dead_ask(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    from workers.session_worker import create_client, check_session_alive

    all_sessions = get_all_sessions()
    total = sum(len(v) for v in all_sessions.values())

    if total == 0:
        await callback.answer("💭 No sessions found.", show_alert=True)
        return

    # Quick count of dead sessions from DB statuses
    import database as _db
    statuses = await get_all_account_statuses()
    dead_in_db = sum(1 for s in statuses.values() if s in ("BANNED", "Die", "Dead", "died"))

    text = (
        f"🧹 <b>Delete Dead Sessions</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Sessions:</b> <code>{total}</code>\n"
        f"❌ <b>Known Dead:</b> <code>{dead_in_db}</code>\n\n"
        f"⚠️ <i>This action will permanently delete all sessions that failed the last check (BANNED/Die).</i>\n"
        f"💡 <i>Tip: Run a fresh check first for accurate results.</i>"
    )
    await smart_edit(callback.message, text, reply_markup=confirm_delete_dead_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "del_dead_confirm")
async def cb_del_dead_confirm(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    import database as _db
    statuses = await get_all_account_statuses()
    dead_phones = {p for p, s in statuses.items() if s in ("BANNED", "Die", "Dead", "died")}

    if not dead_phones:
        await smart_edit(callback.message, "✅ <b>No dead sessions found in records.</b>\n"
            "Run a check first to identify dead sessions.",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )
        await callback.answer()
        return

    await smart_edit(callback.message, f"🧹 <b>Deleting {len(dead_phones)} dead sessions...</b>",
        parse_mode="HTML",
    )
    await callback.answer()

    deleted = 0
    not_found_count = 0
    all_sessions = get_all_sessions()

    for folder, phones in all_sessions.items():
        folder_path = os.path.join(SESSIONS_DIR, folder)
        for phone in phones:
            if phone in dead_phones:
                for ext in [".session", ".session-journal"]:
                    path = os.path.join(folder_path, phone + ext)
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                            deleted += 1
                        except Exception as e:
                            logger.error(f"Failed to delete dead {phone}: {e}")
                    else:
                        not_found_count += 1

                # Remove DB entries
                await db.delete_phone_status(phone)

        # Remove empty folder
        if os.path.exists(folder_path):
            remaining = [f for f in os.listdir(folder_path) if f.endswith(".session")]
            if not remaining:
                try:
                    os.rmdir(folder_path)
                except OSError:
                    pass

    await log_action(
        callback.from_user.id, "DELETE_DEAD",
        f"Deleted {deleted} dead sessions (not found on disk: {not_found_count})"
    )
    logger.info(f"Delete dead: {deleted} sessions removed by {callback.from_user.id}")

    await smart_edit(callback.message, f"✅ <b>Dead Sessions Cleanup Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Targeted: <b>{len(dead_phones)}</b>\n"
        f"🗑 Deleted: <b>{deleted // 1}</b> session files\n"
        f"⚠️ Not on disk: <b>{not_found_count}</b>",
        reply_markup=back_menu_kb(), parse_mode="HTML",
    )

# ─── 2FA Setup ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("set2fa:"))
async def cb_set2fa(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    
    # Check if session exists
    folder = None
    for f, phones in all_sessions.items():
        if phone in phones:
            folder = f
            break
            
    if not folder:
        await callback.answer("❌ Session not found.", show_alert=True)
        return

    await state.update_data(target_phone=phone, target_folder=folder)
    await state.set_state(TwoFactorState.waiting_password)
    
    await smart_edit(callback.message, f"🔑 <b>Set 2FA Password</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Phone: <b>+{phone}</b>\n\n"
        f"Please send me the new 2FA password for this session:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ]), parse_mode="HTML"
    )
    await callback.answer()

@router.message(TwoFactorState.waiting_password)
async def msg_set2fa_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    password = message.text.strip()
    data = await state.get_data()
    phone = data.get("target_phone")
    folder = data.get("target_folder")
    await state.clear()
    
    status_msg = await message.answer(f"⏳ Setting 2FA for <b>+{phone}</b>...", parse_mode="HTML")
    
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    session_path = os.path.join(SESSIONS_DIR, folder, phone)
    
    client = None
    try:
        async with session_lock(phone):
            client = create_client(session_path, api_id, api_hash, proxy)
            await client.connect()
            if not await client.is_user_authorized():
                await smart_edit(status_msg, "❌ Session is dead.", parse_mode="HTML")
                await client.disconnect()
                client = None
                return

            await client.edit_2fa(new_password=password)
        await smart_edit(status_msg, f"✅ <b>2FA Password Set!</b>\n\n"
            f"📱 Phone: <b>+{phone}</b>\n"
            f"🔑 New Password: <code>{password}</code>", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back to Session", callback_data=f"sp:{phone}")]
            ])
        )
    except Exception as e:
        logger.error(f"Set 2FA Error: {e}")
        await smart_edit(status_msg, f"❌ Failed to set 2FA: <code>{type(e).__name__}: {e}</code>", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back to Session", callback_data=f"sp:{phone}")]
            ])
        )
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

# ─── Clean Sessions ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "clean_all")
async def cb_clean_all(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    all_sessions = get_all_sessions()
    await _run_clean(callback, all_sessions, "All Sessions")

@router.callback_query(F.data.startswith("clean_country:"))
async def cb_clean_country(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    if folder not in all_sessions:
        await callback.answer("❌ Country not found.", show_alert=True)
        return

    sessions_to_clean = {folder: all_sessions[folder]}
    flag, name = get_country_display(folder)
    await _run_clean(callback, sessions_to_clean, f"{flag} {name}")

async def _run_clean(callback: CallbackQuery, sessions_dict: dict, label: str):
    total = sum(len(v) for v in sessions_dict.values())
    if total == 0:
        await callback.answer("📭 No sessions found.", show_alert=True)
        return

    status_msg = await smart_edit(callback.message, f"🧹 <b>Cleaning {label}...</b>\n\n"
        f"⏳ Processing <b>0/{total}</b>...", parse_mode="HTML")

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    success = 0
    failed = 0
    processed = 0

    from telethon.tl.functions.messages import ReadHistoryRequest

    for folder, phones in sessions_dict.items():
        for phone in phones:
            processed += 1
            session_path = os.path.join(SESSIONS_DIR, folder, phone)
            client = None
            try:
                client = create_client(session_path, api_id, api_hash, proxy)
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    failed += 1
                    continue

                # Delete SpamBot
                try:
                    await client.delete_dialog("@SpamBot")
                except:
                    pass
                
                # Mark all as read
                try:
                    async for dialog in client.iter_dialogs(limit=50):
                        if dialog.unread_count > 0:
                            await client(ReadHistoryRequest(peer=dialog.entity, max_id=0))
                except:
                    pass

                await client.disconnect()
                success += 1
            except Exception as e:
                failed += 1
                logger.error(f"Clean failed +{phone}: {e}")
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass

            if processed % 5 == 0 or processed == total:
                try:
                    status_msg = await smart_edit(status_msg, f"🧹 <b>Cleaning {label}...</b>\n\n"
                        f"⏳ Processing <b>{processed}/{total}</b>...\n"
                        f"✅ Success: {success}  ❌ Failed: {failed}",
                        parse_mode="HTML"
                    )
                except:
                    pass

    await smart_edit(status_msg, f"✅ <b>Cleanup Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Target: <b>{label}</b>\n"
        f"📱 Total: <b>{total}</b>\n"
        f"✅ Cleaned: <b>{success}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Back", callback_data="ses")]
        ])
    )
    await callback.answer()


# ─── Batch OTP ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("batch_otp:"))
async def cb_batch_otp(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[len("batch_otp:"):]
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    if not phones:
        await callback.answer("📭 No sessions in this country", show_alert=True)
        return

    flag, name = get_country_display(folder)
    total = len(phones)

    await smart_edit(callback.message,
        f"📨 <b>Batch OTP — {flag} {name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ Processing <b>0/{total}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    results: list[tuple[str, bool, str]] = []
    processed = 0

    for phone in phones:
        processed += 1
        session_file = os.path.join(SESSIONS_DIR, folder, phone)
        client = None
        try:
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                results.append((phone, False, "Dead session"))
                continue
            found, otp_text = await get_last_otp(client)
            await client.disconnect()
            results.append((phone, found, otp_text.strip()))
        except Exception as e:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            results.append((phone, False, f"Error: {type(e).__name__}"))

        if processed % 3 == 0 or processed == total:
            bar_filled = "█" * processed
            bar_empty = "░" * (total - processed)
            try:
                await smart_edit(callback.message,
                    f"📨 <b>Batch OTP — {flag} {name}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"⏳ {bar_filled}{bar_empty} {processed}/{total}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    found_count = sum(1 for _, f, _ in results if f)

    # Build compact results text
    _otp_re = re.compile(r"<code>(\d+)</code>")
    _date_re = re.compile(r"📅 <b>Date:</b> (.+)")

    lines = [
        f"📨 <b>Batch OTP Results</b>",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"{flag} <b>{name}</b>  |  🔑 {found_count}/{total} found",
        "",
    ]
    for phone, found, text in results:
        if found:
            code_m = _otp_re.search(text)
            date_m = _date_re.search(text)
            code = code_m.group(1) if code_m else "?"
            date_str = date_m.group(1).strip() if date_m else ""
            lines.append(f"+{phone} → 🔑 <code>{code}</code>  <i>{date_str}</i>")
        else:
            lines.append(f"+{phone} → 📭 No code")

    result_text = "\n".join(lines)

    if len(result_text) <= 4000:
        await smart_edit(callback.message, result_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back", callback_data=f"sc:{folder[:50]}")],
            ]),
            parse_mode="HTML",
        )
    else:
        # Too long — send as a .txt file
        _tmp_dir = tempfile.mkdtemp(prefix="batch_otp_")
        tmp_path = os.path.join(_tmp_dir, f"batch_otp_{folder}.txt")
        plain = [f"Batch OTP Results — {name}", "=" * 35, ""]
        for phone, found, text in results:
            if found:
                code_m = _otp_re.search(text)
                code = code_m.group(1) if code_m else "?"
                plain.append(f"+{phone} -> {code}")
            else:
                plain.append(f"+{phone} -> No code")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(plain))
        try:
            await smart_edit(callback.message,
                f"📨 <b>Batch OTP Complete!</b>\n\n"
                f"{flag} {name}: {found_count}/{total} codes found\n\n"
                f"Results saved to file below.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Back", callback_data=f"sc:{folder[:50]}")],
                ]),
                parse_mode="HTML",
            )
            doc = FSInputFile(tmp_path, filename=f"batch_otp_{folder}.txt")
            await callback.message.answer_document(doc,
                caption=f"📨 <b>Batch OTP — {flag} {name}</b>", parse_mode="HTML")
        finally:
            shutil.rmtree(_tmp_dir, ignore_errors=True)
