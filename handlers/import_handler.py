"""
Import Handler — Receive ZIP/session/txt/json files, parse them, detect country, and save directly to main sessions folder.

Supported formats:
  .zip   — archive of .session files
  .session — single session file
  .txt   — plain phone number list (one per line, no session data saved)
  .json  — session data exported by other tools [{"phone": "...", ...}]
"""
from utils.utils import smart_edit

import json
import os
import shutil
import zipfile
import tempfile
import asyncio
import logging
from io import BytesIO

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

from ui.states import ImportState
from ui.keyboards import cancel_kb, back_menu_kb, import_session_kb
from utils.country_utils import SESSIONS_DIR, get_all_sessions, detect_country, get_session_dir
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client, get_last_otp
from handlers.common import is_admin_async, is_write_admin_async
from database import database as db
from utils.session_lock import session_lock

router = Router()
logger = logging.getLogger(__name__)


def clean_phone_number(filename: str) -> str:
    """Takes a filename like '+48 459 07 65 83.session' and returns '48459076583'."""
    name = os.path.basename(filename)
    if name.endswith(".session"):
        name = name[:-8]  # Remove .session
    # Remove +, spaces, hyphens
    clean = name.replace("+", "").replace(" ", "").replace("-", "").strip()
    return clean


from ui.keyboards import cancel_kb, back_menu_kb, import_session_kb, import_menu_kb

@router.callback_query(F.data == "imp_menu")
async def cb_import_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    text = (
        "📥 <b>Import Sessions</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• <b>Import Standard</b>: .zip, .session, .txt, .json\n\n"
        "<i>Looking to convert formats? Use the 🔄 Convert menu.</i>"
    )
    await smart_edit(callback.message, text, reply_markup=import_menu_kb(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "imp_standard")
async def cb_imp_standard(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(ImportState.waiting_for_file)
    text = (
        "📥 <b>Import Standard Sessions</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send me a <b>.zip</b>, <b>.session</b>, <b>.txt</b>, or <b>.json</b> file."
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(ImportState.waiting_for_file, F.document)
async def msg_receive_import(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    doc = message.document
    filename = doc.file_name or ""
    allowed_exts = (".zip", ".session", ".txt", ".json")
    if not any(filename.endswith(ext) for ext in allowed_exts):
        await message.answer(
            "❌ Please send a <code>.zip</code>, <code>.session</code>, "
            "<code>.txt</code>, or <code>.json</code> file.",
            reply_markup=cancel_kb(), parse_mode="HTML",
        )
        return

    status_msg = await message.answer("⏳ <b>Downloading file...</b>", parse_mode="HTML")
    await state.clear()

    bot = message.bot
    file_info = await bot.get_file(doc.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)

    found_phones = []
    txt_only = False  # .txt imports are phone-only (no session files)

    if filename.endswith(".zip"):
        status_msg = await smart_edit(status_msg, "⏳ <b>Extracting ZIP and sorting into countries...</b>", parse_mode="HTML")
        try:
            with zipfile.ZipFile(downloaded_file, "r") as zf:
                all_files = zf.namelist()
                session_files = [f for f in all_files if f.endswith(".session") and "__MACOSX" not in f]

                # Enforce Max 1000 limit
                if len(session_files) > 1000:
                    session_files = session_files[:1000]
                    await message.answer("⚠️ <b>Limit Reached!</b>\nOnly processing the first 1000 `.session` files.", parse_mode="HTML")

                for s_file in session_files:
                    try:
                        clean_num = clean_phone_number(s_file)
                        if not clean_num.isdigit():
                            continue

                        # Detect country and get folder
                        _, country_name, _ = detect_country(clean_num)
                        folder_path = get_session_dir(country_name)

                        target_path = os.path.join(folder_path, f"{clean_num}.session")

                        # Extract in chunks with a max size limit to prevent Zip Bombs
                        MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
                        with zf.open(s_file) as source, open(target_path, "wb") as target:
                            extracted_size = 0
                            while chunk := source.read(8192):
                                extracted_size += len(chunk)
                                if extracted_size > MAX_FILE_SIZE:
                                    raise ValueError(f"File {s_file} exceeds size limit (Zip Bomb protection)")
                                target.write(chunk)

                        found_phones.append(clean_num)
                    except Exception as e:
                        logger.warning(f"Failed to process {s_file} from ZIP: {e}")
        except Exception as e:
            logger.error(f"ZIP parse error: {e}")
            status_msg = await smart_edit(status_msg, f"❌ Failed to parse ZIP: <code>{e}</code>", reply_markup=back_menu_kb(), parse_mode="HTML")
            return

    elif filename.endswith(".session"):
        clean_num = clean_phone_number(filename)
        if clean_num.isdigit():
            _, country_name, _ = detect_country(clean_num)
            folder_path = get_session_dir(country_name)
            target_path = os.path.join(folder_path, f"{clean_num}.session")

            with open(target_path, "wb") as f:
                f.write(downloaded_file.read())
            found_phones.append(clean_num)

    elif filename.endswith(".txt"):
        # Phone list: one phone per line, no session data
        txt_only = True
        status_msg = await smart_edit(status_msg, "⏳ <b>Parsing phone list (.txt)...</b>", parse_mode="HTML")
        try:
            content = downloaded_file.read().decode("utf-8", errors="ignore")
            lines = content.strip().splitlines()
            count = 0
            for line in lines:
                phone = line.strip().replace("+", "").replace(" ", "").replace("-", "")
                if phone.isdigit() and 7 <= len(phone) <= 15:
                    found_phones.append(phone)
                    count += 1
                    if count >= 1000:
                        await message.answer("⚠️ Limit: first 1000 numbers processed.", parse_mode="HTML")
                        break
        except Exception as e:
            logger.error(f".txt parse error: {e}")
            status_msg = await smart_edit(status_msg, f"❌ Failed to parse .txt: <code>{e}</code>", reply_markup=back_menu_kb(), parse_mode="HTML")
            return

    elif filename.endswith(".json"):
        # JSON session list: [{"phone": "...", "session_string": "..."}, ...]
        status_msg = await smart_edit(status_msg, "⏳ <b>Parsing .json session data...</b>", parse_mode="HTML")
        try:
            content = downloaded_file.read().decode("utf-8", errors="ignore")
            data = json.loads(content)
            if isinstance(data, dict):
                data = [data]  # wrap single object
            if not isinstance(data, list):
                raise ValueError("JSON must be a list or single object")

            count = 0
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                phone_raw = str(entry.get("phone", "")).strip()
                phone = phone_raw.replace("+", "").replace(" ", "").replace("-", "")
                if not phone.isdigit() or not (7 <= len(phone) <= 15):
                    continue

                # Try to write session_string if present (Pyrogram/Telethon string sessions)
                session_str = entry.get("session_string") or entry.get("string_session") or ""
                # We skip writing .session files from JSON strings (would need full decode)
                # Just record the phone number for reporting
                found_phones.append(phone)
                count += 1
                if count >= 1000:
                    await message.answer("⚠️ Limit: first 1000 entries processed.", parse_mode="HTML")
                    break
            txt_only = True  # JSON phones: no local session file written
        except Exception as e:
            logger.error(f".json parse error: {e}")
            status_msg = await smart_edit(status_msg, f"❌ Failed to parse .json: <code>{e}</code>", reply_markup=back_menu_kb(), parse_mode="HTML")
            return


    if not found_phones:
        status_msg = await smart_edit(status_msg, "📭 <b>No valid entries found!</b>",
            reply_markup=back_menu_kb(), parse_mode="HTML",
        )
        return

    # For .txt and .json: just report phones (no session controls)
    if txt_only:
        await status_msg.delete()
        _tmp_dir = tempfile.mkdtemp(prefix="import_")
        txt_path = os.path.join(_tmp_dir, "imported_numbers.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(found_phones))
            summary_doc = FSInputFile(txt_path, filename="imported_numbers.txt")
            await message.answer_document(
                summary_doc,
                caption=(
                    f"✅ <b>Import Complete!</b>\n\n"
                    f"📱 {len(found_phones)} phone numbers parsed.\n"
                    f"⚠️ No session files saved (phone-list only import)."
                ),
                parse_mode="HTML",
                reply_markup=back_menu_kb(),
            )
        finally:
            shutil.rmtree(_tmp_dir, ignore_errors=True)
        return

    # Delete status message as we will start sending individual ones
    await status_msg.delete()

    # Create Summary TXT
    _tmp_dir = tempfile.mkdtemp(prefix="import_")
    txt_path = os.path.join(_tmp_dir, "imported_summary.txt")
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(found_phones))

        # Send TXT Summary
        summary_doc = FSInputFile(txt_path, filename="imported_summary.txt")
        await message.answer_document(
            summary_doc,
            caption=f"✅ <b>Import Scan Completed & Sorted!</b>\n\n📱 Total Added to Bot: <b>{len(found_phones)}</b>",
            parse_mode="HTML"
        )
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)

    if len(found_phones) > 10:
        await state.set_state(ImportState.action_choice)
        await state.update_data(imported_phones=found_phones)

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Save Only", callback_data="imp_act:save")],
            [InlineKeyboardButton(text="📨 Get Codes", callback_data="imp_act:fetch")]
        ])

        await message.answer(
            f"⚠️ <b>Large Import Detected ({len(found_phones)} sessions)</b>\n\n"
            f"Would you like to fetch OTPs for all these numbers interactively, or just save them to storage?",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        # Deliver individual messages immediately
        for phone in found_phones:
            _, name, flag = detect_country(phone)
            account_status = await db.get_account_status(phone)
            msg_text = (
                f"📱 {flag} {name} | <code>+{phone}</code>\n"
                f"Tap <b>Get Code</b> whenever you need it."
            )
            try:
                await message.answer(msg_text, reply_markup=import_session_kb(phone, account_status), parse_mode="HTML")
                await asyncio.sleep(0.3)  # Anti-FloodWait Delay
            except Exception as e:
                logger.error(f"Failed to send message for {phone}: {e}")
                await asyncio.sleep(1) # Extra delay on error

        await message.answer("✅ <b>Finished sending all controls.</b>", reply_markup=back_menu_kb(), parse_mode="HTML")


@router.callback_query(ImportState.action_choice, F.data.startswith("imp_act:"))
async def cb_import_action_choice(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer()

    action = callback.data.split(":")[1]
    data = await state.get_data()
    found_phones = data.get("imported_phones", [])
    
    await state.clear()
    
    if action == "save":
        await smart_edit(callback.message, "✅ <b>Sessions saved to storage successfully.</b>\nNo individual fetching controls will be sent.", parse_mode="HTML")
        return
        
    if action == "fetch":
        await smart_edit(callback.message, "⏳ <b>Generating OTP fetch controls...</b>", parse_mode="HTML")
        for phone in found_phones:
            _, name, flag = detect_country(phone)
            account_status = await db.get_account_status(phone)
            msg_text = (
                f"📱 {flag} {name} | <code>+{phone}</code>\n"
                f"Tap <b>Get Code</b> whenever you need it."
            )
            try:
                await callback.message.answer(msg_text, reply_markup=import_session_kb(phone, account_status), parse_mode="HTML")
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.error(f"Failed to send interactive message: {e}")
                await asyncio.sleep(1)
        
        await callback.message.answer("✅ <b>Finished sending all controls.</b>", reply_markup=back_menu_kb(), parse_mode="HTML")



# ─── Imported Sessions Controls ──────────────────────────────────────────────

def _get_session_path_of(phone: str) -> str | None:
    """Finds the absolute path without .session extension of a registered phone."""
    all_sessions = get_all_sessions()
    for folder, phones in all_sessions.items():
        if phone in phones:
            return os.path.join(SESSIONS_DIR, folder, phone)
    return None


@router.callback_query(F.data.startswith("imp_otp:"))
async def cb_imp_get_otp(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data.split(":")[1]
    base_path = _get_session_path_of(phone)

    if not base_path or not os.path.exists(base_path + ".session"):
        await callback.answer("❌ Local session file not found. It may have been deleted.", show_alert=True)
        return

    _, name, flag = detect_country(phone)

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
            client = create_client(base_path, api_id, api_hash, proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                for ext in [".session", ".session-journal"]:
                    if os.path.exists(base_path + ext):
                        os.remove(base_path + ext)
                await smart_edit(callback.message, f"❌ <b>Session not authorized (Deleted automatically)</b>\n\n<code>{phone}</code>",
                    parse_mode="HTML",
                )
                return

            found, result_text = await get_last_otp(client)
            await client.disconnect()

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from ui.keyboards import _DEAD_STATUSES
        account_status = await db.get_account_status(phone)
        is_dead = account_status in _DEAD_STATUSES
        kb_rows = [
            [InlineKeyboardButton(text="🔄 Fetch Again", callback_data=f"imp_otp:{phone}")],
        ]
        action_row = []
        if not is_dead:
            action_row.append(InlineKeyboardButton(text="🚪 Log Out", callback_data=f"imp_lo:{phone}"))
        action_row.append(InlineKeyboardButton(text="🗑 Delete", callback_data=f"imp_del:{phone}"))
        kb_rows.append(action_row)
        kb_rows.append([InlineKeyboardButton(text="🔙 Back to Controls", callback_data=f"imp_back:{phone}")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        
        text = (
            f"📨 <b>OTP Result</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{flag} {name} | <code>+{phone}</code>\n\n"
            f"{result_text}"
        )
        await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Import OTP error for {phone}: {e}")
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Fetch Again", callback_data=f"imp_otp:{phone}")],
            [InlineKeyboardButton(text="🔙 Back to Controls", callback_data=f"imp_back:{phone}")],
        ])
        await smart_edit(callback.message, f"❌ <b>OTP fetch failed!</b>\n\n"
            f"{flag} {name} | <code>+{phone}</code>\n"
            f"Error: <code>{type(e).__name__}: {e}</code>",
            reply_markup=kb, parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("imp_lo:"))
async def cb_imp_logout(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data.split(":")[1]
    base_path = _get_session_path_of(phone)

    if not base_path or not os.path.exists(base_path + ".session"):
        await smart_edit(callback.message, f"❌ Session <code>{phone}</code> already deleted or not found.", parse_mode="HTML")
        return

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    try:
        async with session_lock(phone):
            client = create_client(base_path, api_id, api_hash, proxy)
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()
            else:
                await client.disconnect()

            # Delete local files
            for ext in [".session", ".session-journal"]:
                if os.path.exists(base_path + ext):
                    os.remove(base_path + ext)

        await smart_edit(callback.message, f"✅ <b>Logged out & deleted!</b>\n\n<code>{phone}</code>", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Import logout error: {e}")
        await smart_edit(callback.message, f"❌ Logout failed: <code>{e}</code>", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("imp_del:"))
async def cb_imp_delete(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data.split(":")[1]
    base_path = _get_session_path_of(phone)

    deleted = False
    if base_path:
        for ext in [".session", ".session-journal"]:
            if os.path.exists(base_path + ext):
                os.remove(base_path + ext)
                deleted = True

    if deleted:
        await smart_edit(callback.message, f"🗑 <b>Deleted!</b>\n\n<code>{phone}</code>", parse_mode="HTML")
    else:
        await smart_edit(callback.message, f"❌ Session <code>{phone}</code> not found or already deleted.", parse_mode="HTML")
    
    await callback.answer()


@router.callback_query(F.data.startswith("imp_back:"))
async def cb_imp_back(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    phone = callback.data.split(":")[1]
    _, name, flag = detect_country(phone)
    account_status = await db.get_account_status(phone)
    msg_text = (
        f"📱 {flag} {name} | <code>+{phone}</code>\n"
        f"Tap <b>Get Code</b> whenever you need it."
    )
    await smart_edit(callback.message, msg_text, reply_markup=import_session_kb(phone, account_status), parse_mode="HTML")
    await callback.answer()
