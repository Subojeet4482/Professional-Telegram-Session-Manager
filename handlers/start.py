"""
Start & Main Menu handler.
Includes persistent Reply Keyboard (bottom menu), inline navigation,
Read OTP quick-access flow, and Advanced Gen options.
"""

import os
import shutil
import zipfile
import tempfile
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from utils.utils import smart_edit
from ui.keyboards import (
    main_menu_kb,
    main_reply_kb,
    sessions_menu_kb,
    check_menu_kb,
    import_menu_kb,
    convert_menu_kb,
    back_menu_kb,
    cancel_kb,
    read_otp_countries_kb,
    read_otp_phones_kb,
    advanced_gen_menu_kb,
    BTN_SESSION_GEN,
    BTN_ADVANCED_GEN,
    BTN_CHECK,
    BTN_READ_OTP,
    BTN_SESSIONS,
    BTN_EXPORT_ALL,
    BTN_IMPORT,
    BTN_CONVERT,
    BTN_STATISTICS,
    BTN_MAIN_MENU,
)
from utils.country_utils import (
    get_total_stats,
    get_all_sessions,
    get_country_display,
    SESSIONS_DIR,
)
from handlers.common import is_admin_async, is_write_admin_async
from handlers.register import cleanup_registration
from ui.states import ReadOTPState
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client, get_last_otp
from utils.session_lock import session_lock
import database.database as db
from ui.keyboards import otp_result_kb

logger = logging.getLogger(__name__)

router = Router()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _main_menu_text() -> str:
    total, countries = get_total_stats()
    return (
        "🛠️ <b>System Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Sessions:</b> <code>{total}</code>\n"
        f"🌍 <b>Active Countries:</b> <code>{countries}</code>\n\n"
        "⚡ <i>Use the bottom menu for core actions.</i>\n"
        "⚙️ <i>Select an option below for advanced management:</i>"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("🚫 You are not authorized to use this bot.")
        return

    await state.clear()

    # Send welcome with Reply Keyboard
    total, countries = get_total_stats()
    welcome = (
        "👋 <b>Welcome to Session Manager!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>{total}</b> sessions  •  🌍 <b>{countries}</b> countries\n\n"
        "⚡ Quick actions are ready below ⬇️\n"
        "🔧 Admin tools available in the menu:"
    )
    await message.answer(welcome, reply_markup=main_reply_kb(), parse_mode="HTML")
    await message.answer(_main_menu_text(), reply_markup=main_menu_kb(), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════════
# Main Menu (callback) — now shows admin tools only
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫 Not authorized", show_alert=True)
        return

    await state.clear()
    await smart_edit(callback.message, _main_menu_text(), reply_markup=main_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ═══════════════════════════════════════════════════════════════════════════════
# Cancel — clears state and shows menu
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫 Not authorized", show_alert=True)
        return

    await cleanup_registration(callback.from_user.id)
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("❌ Cancelled")


# ═══════════════════════════════════════════════════════════════════════════════
# Noop
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


# ═══════════════════════════════════════════════════════════════════════════════
# Reply Keyboard Button Handlers
# ═══════════════════════════════════════════════════════════════════════════════


# ─── 📱 Session Gen ──────────────────────────────────────────────────────────

@router.message(F.text == BTN_SESSION_GEN)
async def reply_session_gen(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    from ui.states import RegState

    await state.set_state(RegState.phone)
    text = (
        "➕ <b>New Session Setup</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 Please enter the phone number including the country code:\n\n"
        "💡 <i>Example:</i> <code>+966512345678</code>"
    )
    await message.answer(text, reply_markup=cancel_kb(), parse_mode="HTML")


# ─── 🔧 Advanced Gen ────────────────────────────────────────────────────────

_ADVGEN_TEXT = (
    "🔧 <b>Advanced Session Gen</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Configure options before registration:\n\n"
    "• <b>Auto 2FA</b> — Set 2FA password automatically\n"
    "• <b>Auto Profile</b> — Apply profile settings after reg\n"
    "• <b>Auto Spam Check</b> — Check spam status after reg\n"
    "• <b>Auto Log Out</b> — Terminate other sessions after reg\n\n"
    "Toggle options below, then start:"
)


@router.message(F.text == BTN_ADVANCED_GEN)
async def reply_advanced_gen(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    await state.clear()

    # Default options
    options = {"auto_2fa": False, "auto_profile": False, "auto_check": False, "auto_logout": False}
    await state.update_data(advgen_options=options)

    await message.answer(_ADVGEN_TEXT, reply_markup=advanced_gen_menu_kb(options), parse_mode="HTML")


# ─── Advanced Gen: Toggle Options ────────────────────────────────────────────

@router.callback_query(F.data.startswith("advgen_toggle:"))
async def cb_advgen_toggle(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    option = callback.data.split(":")[1]
    data = await state.get_data()
    options = data.get("advgen_options") or {
        "auto_2fa": False, "auto_profile": False, "auto_check": False, "auto_logout": False,
    }
    options[option] = not options.get(option, False)
    await state.update_data(advgen_options=options)

    status = "✅ ON" if options[option] else "❌ OFF"
    await callback.answer(f"{option.replace('_', ' ').title()}: {status}")

    await smart_edit(callback.message, _ADVGEN_TEXT, reply_markup=advanced_gen_menu_kb(options), parse_mode="HTML")


# ─── Advanced Gen: Start Registration ────────────────────────────────────────

@router.callback_query(F.data == "advgen_start")
async def cb_advgen_start(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    from ui.states import RegState

    # Keep advgen_options in state — register handler will read them
    await state.set_state(RegState.phone)
    text = (
        "🔧 <b>Advanced Gen — Enter Phone</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 Send the phone number with country code:\n\n"
        "💡 Example: <code>+966512345678</code>"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


# ─── 🔍 Check ───────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CHECK)
async def reply_check(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    await state.clear()
    text = (
        "🔍 <b>Check Sessions</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose a check type:"
    )
    await message.answer(text, reply_markup=check_menu_kb(), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════════
# 📨 Read OTP — Dedicated Quick-Access Flow
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text == BTN_READ_OTP)
async def reply_read_otp(message: Message, state: FSMContext):
    """Show country list for quick OTP reading."""
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    await state.clear()
    all_sessions = get_all_sessions()

    if not all_sessions:
        await message.answer(
            "📭 <b>No sessions available</b>\n\n"
            "Register a number first using 📱 Session Gen.",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        return

    countries = {}
    for folder, phones in sorted(all_sessions.items(), key=lambda x: len(x[1]), reverse=True):
        flag, name = get_country_display(folder)
        countries[folder] = (flag, name, len(phones))

    total = sum(len(v) for v in all_sessions.values())
    text = (
        "📨 <b>Read OTP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>{total}</b> sessions available\n\n"
        "📍 Select a country, then tap a number to read OTP:\n"
        "🔍 Or search by phone number directly:"
    )
    await message.answer(text, reply_markup=read_otp_countries_kb(countries), parse_mode="HTML")


# ─── Read OTP: Country pages ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("rotp_p:"))
async def cb_rotp_page(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    page = int(callback.data.split(":")[1])
    all_sessions = get_all_sessions()
    countries = {}
    for folder, phones in sorted(all_sessions.items(), key=lambda x: len(x[1]), reverse=True):
        flag, name = get_country_display(folder)
        countries[folder] = (flag, name, len(phones))

    total = sum(len(v) for v in all_sessions.values())
    text = (
        "📨 <b>Read OTP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>{total}</b> sessions available\n\n"
        "📍 Select a country, then tap a number to read OTP:"
    )
    await smart_edit(callback.message, text, reply_markup=read_otp_countries_kb(countries, page), parse_mode="HTML")
    await callback.answer()


# ─── Read OTP: Country selected → show phones ───────────────────────────────

@router.callback_query(F.data.startswith("rotp_c:"))
async def cb_rotp_country(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data[7:]
    all_sessions = get_all_sessions()
    phones = all_sessions.get(folder, [])

    if not phones:
        await callback.answer("📭 No sessions in this country", show_alert=True)
        return

    flag, name = get_country_display(folder)
    text = (
        f"📨 <b>Read OTP — {flag} {name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>{len(phones)}</b> sessions\n\n"
        "⚡ Tap a number to read its OTP:"
    )
    await smart_edit(callback.message, text, reply_markup=read_otp_phones_kb(folder, phones), parse_mode="HTML")
    await callback.answer()


# ─── Read OTP: Phone pages ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("rotp_cp:"))
async def cb_rotp_country_page(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    parts = callback.data.split(":")
    folder = parts[1]
    page = int(parts[2])
    all_sessions = get_all_sessions()
    phones = all_sessions.get(folder, [])

    flag, name = get_country_display(folder)
    text = (
        f"📨 <b>Read OTP — {flag} {name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>{len(phones)}</b> sessions\n\n"
        "⚡ Tap a number to read its OTP:"
    )
    await smart_edit(callback.message, text, reply_markup=read_otp_phones_kb(folder, phones, page), parse_mode="HTML")
    await callback.answer()


# ─── Read OTP: Search by phone ──────────────────────────────────────────────

@router.callback_query(F.data == "rotp_search")
async def cb_rotp_search(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(ReadOTPState.waiting_phone)
    text = (
        "🔍 <b>Search & Read OTP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 Send the phone number (full or partial):\n\n"
        "💡 Example: <code>966512345678</code>"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Read OTP: Process search ───────────────────────────────────────────────

@router.message(ReadOTPState.waiting_phone)
async def on_rotp_search_phone(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    phone_query = message.text.strip().lstrip("+")
    if not phone_query:
        await message.answer("❌ Please send a phone number.", reply_markup=cancel_kb())
        return

    await state.clear()

    # Search for the phone
    all_sessions = get_all_sessions()
    found_phone = None
    found_folder = None

    for folder, phones in all_sessions.items():
        for p in phones:
            if phone_query in p or p in phone_query:
                found_phone = p
                found_folder = folder
                break
        if found_phone:
            break

    if not found_phone:
        await message.answer(
            f"❌ <b>Not found</b>\n\n"
            f"No session matches: <code>{phone_query}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        return

    # Found — fetch OTP directly
    flag, name = get_country_display(found_folder)
    session_file = os.path.join(SESSIONS_DIR, found_folder, found_phone)

    await message.answer(
        f"📨 <b>Fetching OTP...</b>\n\n"
        f"{flag} {name} | <code>+{found_phone}</code>\n\n"
        f"⏳ Connecting to Telegram...",
        parse_mode="HTML",
    )

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    try:
        async with session_lock(found_phone):
            client = create_client(session_file, api_id, api_hash, proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                for ext in [".session", ".session-journal"]:
                    if os.path.exists(session_file + ext):
                        os.remove(session_file + ext)
                await db.delete_phone_status(found_phone)
                await message.answer(
                    f"❌ <b>Session expired (auto-deleted)</b>\n\n<code>+{found_phone}</code>",
                    reply_markup=back_menu_kb(), parse_mode="HTML",
                )
                return

            found, result_text = await get_last_otp(client)
            await client.disconnect()

        account_status = await db.get_account_status(found_phone)
        text = (
            f"📨 <b>OTP Result</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{flag} {name} | <code>+{found_phone}</code>\n\n"
            f"{result_text}"
        )
        await message.answer(text, reply_markup=otp_result_kb(found_phone, found, account_status), parse_mode="HTML")

    except Exception as e:
        logger.error(f"OTP error for +{found_phone}: {e}")
        account_status = await db.get_account_status(found_phone)
        await message.answer(
            f"❌ <b>OTP fetch failed!</b>\n\n"
            f"<code>+{found_phone}</code>\n"
            f"Error: <code>{type(e).__name__}: {e}</code>",
            reply_markup=otp_result_kb(found_phone, False, account_status), parse_mode="HTML",
        )


# ─── 📂 Sessions ────────────────────────────────────────────────────────────

@router.message(F.text == BTN_SESSIONS)
async def reply_sessions(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    await state.clear()
    total, countries = get_total_stats()
    text = (
        "📂 <b>Sessions Management</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 Total: <b>{total}</b> sessions  •  🌍 <b>{countries}</b> countries"
    )
    await message.answer(text, reply_markup=sessions_menu_kb(), parse_mode="HTML")


# ─── 📦 Export All ───────────────────────────────────────────────────────────

@router.message(F.text == BTN_EXPORT_ALL)
async def reply_export_all(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    all_sessions = get_all_sessions()
    if not all_sessions:
        await message.answer(
            "📭 <b>No sessions to export</b>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
        return

    await message.answer("📦 <b>Creating ZIP...</b>", parse_mode="HTML")

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
        await message.answer_document(
            doc,
            caption=f"📦 <b>All Sessions Export</b>\n\n📱 {total} sessions exported.",
            parse_mode="HTML",
        )

        if exported_phones:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"+{p}" for p in exported_phones))
            txt_doc = FSInputFile(tmp_txt, filename="all_sessions_numbers.txt")
            await message.answer_document(
                txt_doc,
                caption=f"📋 <b>Exported Numbers</b>\n\n📱 {total} numbers listed.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Export error: {e}")
        await message.answer(
            f"❌ Export failed: <code>{e}</code>",
            reply_markup=back_menu_kb(),
            parse_mode="HTML",
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── 📥 Import ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_IMPORT)
async def reply_import(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    text = (
        "📥 <b>Import Sessions</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• <b>Import Standard</b> — .zip, .session, .txt, .json\n\n"
        "<i>Looking to convert formats? Use the 🔄 Convert menu.</i>"
    )
    await message.answer(text, reply_markup=import_menu_kb(), parse_mode="HTML")


# ─── 🔄 Convert ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CONVERT)
async def reply_convert(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    await state.clear()
    text = (
        "🔄 <b>Convert</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose a conversion:\n\n"
        "• <b>TData → Session</b> — upload a .zip of tdata folders\n"
        "• <b>Session → TData</b> — build a Telegram Desktop profile\n"
        "• <b>Session → JSON</b> — export account metadata\n"
        "• <b>Session → All</b> — tdata + session + json + 2FA.txt\n\n"
        "<i>Each account is returned in its own folder.</i>"
    )
    await message.answer(text, reply_markup=convert_menu_kb(), parse_mode="HTML")


# ─── 📊 Statistics ───────────────────────────────────────────────────────────

@router.message(F.text == BTN_STATISTICS)
async def reply_statistics(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
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
    await message.answer(text, reply_markup=back_menu_kb(), parse_mode="HTML")


# ─── 🏠 Main Menu (reply button) ────────────────────────────────────────────

@router.message(F.text == BTN_MAIN_MENU)
async def reply_main_menu(message: Message, state: FSMContext):
    if not await is_admin_async(message.from_user.id):
        await message.answer("🚫 Not authorized.")
        return

    await state.clear()
    await message.answer(_main_menu_text(), reply_markup=main_menu_kb(), parse_mode="HTML")
