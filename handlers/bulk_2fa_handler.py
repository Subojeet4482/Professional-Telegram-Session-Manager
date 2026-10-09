"""
Bulk 2FA Handler — Apply 2FA password to multiple sessions via ZIP or selecting all files.
"""
from utils.utils import smart_edit

import os
import zipfile
import tempfile
import asyncio
import logging
import shutil

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from ui.keyboards import (
    bulk_2fa_action_kb,
    bulk_2fa_menu_kb,
    bulk_2fa_countries_kb,
    back_to_bulk_2fa_kb,
    skip_fallback_kb,
    cancel_kb,
    back_menu_kb
)
from utils.country_utils import (
    get_all_sessions,
    get_country_display,
    SESSIONS_DIR,
)
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client
from handlers.common import is_write_admin_async
from utils.session_lock import session_lock
from ui.states import Bulk2FAState
import database.database as db

router = Router()
logger = logging.getLogger(__name__)

def _clean_phone(filename: str) -> str:
    """Extract clean phone from filename."""
    name = os.path.basename(filename)
    if name.endswith(".session"):
        name = name[:-8]
    return name.replace("+", "").replace(" ", "").replace("-", "").strip()


# ─── Bulk 2FA Menu ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "bulk2fa")
async def cb_bulk2fa_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.clear()
    text = (
        "🔐 <b>Bulk 2FA Management</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose the action you want to perform in bulk:"
    )
    await smart_edit(callback.message, text, reply_markup=bulk_2fa_action_kb(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("b2fa_action:"))
async def cb_b2fa_action(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        return

    action = callback.data.split(":")[1]
    await state.update_data(b2fa_action=action)

    action_names = {"set": "➕ Set 2FA", "update": "🔄 Update 2FA", "delete": "🗑 Delete 2FA"}
    text = (
        f"{action_names.get(action, 'Action')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose an option to select targets:"
    )
    await smart_edit(callback.message, text, reply_markup=bulk_2fa_menu_kb(action), parse_mode="HTML")
    await callback.answer()


# ─── ZIP Upload Flow ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("b2fa_zip:"))
async def cb_b2fa_zip(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    action = callback.data.split(":")[1]
    await state.update_data(b2fa_action=action)

    await state.set_state(Bulk2FAState.waiting_zip)
    text = (
        "📤 <b>Upload ZIP for Bulk 2FA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send me a <b>.zip</b> file containing <code>.session</code> files.\n\n"
        "⚠️ <i>Note: The sessions will be modified directly if they already exist, "
        "or imported if they don't. A 2FA password will be applied to all of them.</i>"
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()

@router.message(Bulk2FAState.waiting_zip, F.document)
async def msg_b2fa_zip_received(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    doc = message.document
    if not doc.file_name.endswith(".zip"):
        await message.answer("❌ Please send a <code>.zip</code> file.", reply_markup=cancel_kb(), parse_mode="HTML")
        return

    # The chosen action (set / update / delete) was stored on the FSM by the
    # b2fa_zip: callback. It is NOT available as a local here — reading it from
    # a non-existent local was raising NameError and killing this handler.
    data = await state.get_data()
    action = data.get("b2fa_action", "set")

    status_msg = await message.answer("⏳ <b>Downloading ZIP...</b>", parse_mode="HTML")

    bot = message.bot
    file_info = await bot.get_file(doc.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)

    # Private per-upload directory (mode 0700) instead of a guessable shared
    # path — the archive holds session files.
    tmp_dir = tempfile.mkdtemp(prefix="b2fa_zip_")
    tmp_zip_path = os.path.join(tmp_dir, "upload.zip")
    keep_upload = False

    try:
        with open(tmp_zip_path, "wb") as f:
            f.write(downloaded_file.read())

        # Verify contents
        try:
            with zipfile.ZipFile(tmp_zip_path, "r") as zf:
                all_files = zf.namelist()
                session_files = [f for f in all_files if f.endswith(".session") and "__MACOSX" not in f]
        except Exception:
            logger.exception("Bulk 2FA: failed to parse uploaded ZIP")
            await smart_edit(
                status_msg,
                "❌ <b>Failed to parse ZIP.</b>\nMake sure it is a valid, uncorrupted archive.",
                reply_markup=back_to_bulk_2fa_kb(), parse_mode="HTML",
            )
            return

        if not session_files:
            await smart_edit(status_msg, "📭 <b>No .session files found in ZIP!</b>",
                             reply_markup=back_to_bulk_2fa_kb(), parse_mode="HTML")
            return

        # Record the target BEFORE branching. The delete path previously called
        # _execute_bulk_2fa() without setting target_type/zip_path, so it found
        # an empty queue and silently did nothing.
        await state.update_data(
            target_type="zip",
            zip_path=tmp_zip_path,
            zip_tmp_dir=tmp_dir,
            session_count=len(session_files),
        )

        # Ownership of tmp_dir passes to the bulk runner, which cleans it up
        # when the batch finishes.
        keep_upload = True

        if action == "delete":
            await _execute_bulk_2fa(message, state, password="")
        else:
            await state.set_state(Bulk2FAState.waiting_password)
            await smart_edit(status_msg, f"✅ <b>ZIP Validated</b> ({len(session_files)} sessions)\n\n"
                f"Please send the <b>2FA Password</b> you want to apply to ALL sessions in this ZIP:",
                reply_markup=cancel_kb(), parse_mode="HTML"
            )
    except Exception:
        logger.exception("Bulk 2FA: unexpected error handling ZIP upload")
        await smart_edit(
            status_msg,
            "❌ <b>An internal error occurred while processing the upload.</b>",
            reply_markup=back_to_bulk_2fa_kb(), parse_mode="HTML",
        )
    finally:
        # Any path that did not hand the directory to the runner must not leak
        # uploaded session files into the temp dir.
        if not keep_upload:
            shutil.rmtree(tmp_dir, ignore_errors=True)

# ─── All Sessions Flow ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("b2fa_all:"))
async def cb_b2fa_all(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        return

    action = callback.data.split(":")[1]
    await state.update_data(b2fa_action=action)

    all_sessions = get_all_sessions()
    total = sum(len(v) for v in all_sessions.values())
    
    if total == 0:
        await callback.answer("📭 No sessions found.", show_alert=True)
        return

    await state.update_data(target_type="all", session_count=total)
    
    if action == "delete":
        await _execute_bulk_2fa(callback.message, state, password="")
    else:
        await state.set_state(Bulk2FAState.waiting_password)
        text = (
            f"📋 <b>Bulk 2FA: All Sessions</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 Target: <b>{total}</b> sessions\n\n"
            f"Please send the <b>2FA Password</b> you want to apply to ALL sessions:"
        )
        await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()

# ─── Country Specific Flow ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("b2fa_country:"))
async def cb_b2fa_country_page(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        return

    parts = callback.data.split(":")
    action = parts[1]
    page = int(parts[2])
    await state.update_data(b2fa_action=action)
    all_sessions = get_all_sessions()

    if not all_sessions:
        await callback.answer("📭 No sessions found.", show_alert=True)
        return

    display = {}
    for folder, phones in all_sessions.items():
        flag, name = get_country_display(folder)
        display[folder] = (flag, name, len(phones))

    text = "🌍 <b>Bulk 2FA by Country</b>\n━━━━━━━━━━━━━━━━━━━━━\n\nSelect a country:"
    await smart_edit(callback.message, text, reply_markup=bulk_2fa_countries_kb(action, display, page), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("b2fa_c:"))
async def cb_b2fa_country_select(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        return

    parts = callback.data.split(":")
    action = parts[1]
    folder = parts[2]
    await state.update_data(b2fa_action=action)
    all_sessions = get_all_sessions()
    
    if folder not in all_sessions:
        await callback.answer("❌ Country not found.", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)

    await state.update_data(target_type="country", target_folder=folder, session_count=len(phones))
    
    if action == "delete":
        await _execute_bulk_2fa(callback.message, state, password="")
    else:
        await state.set_state(Bulk2FAState.waiting_password)
        text = (
            f"🌍 <b>Bulk 2FA: {flag} {name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 Target: <b>{len(phones)}</b> sessions\n\n"
            f"Please send the <b>2FA Password</b> you want to apply:"
        )
        await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Password Received -> Execute Bulk 2FA ───────────────────────────────────

@router.message(Bulk2FAState.waiting_password, F.text)
async def msg_b2fa_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    password = message.text.strip()
    await _execute_bulk_2fa(message, state, password)

async def _execute_bulk_2fa(message: Message, state: FSMContext, password: str):
    data = await state.get_data()
    target_type = data.get("target_type")
    session_count = data.get("session_count", 0)
    action = data.get("b2fa_action", "set")
    
    action_str = {"set": "Setting", "update": "Updating", "delete": "Deleting"}.get(action, "Applying")
    
    status_msg = await message.answer(f"⏳ <b>{action_str} 2FA for {session_count} sessions...</b>\n\n"
        f"⏳ Processing <b>0/{session_count}</b>...", parse_mode="HTML")
    
    pending_phones = []
    tmp_dir = ""

    if target_type == "zip":
        zip_path = data.get("zip_path")
        tmp_dir = tempfile.mkdtemp(prefix="b2fa_")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                all_files = zf.namelist()
                session_files = [f for f in all_files if f.endswith(".session") and "__MACOSX" not in f]
                
                for f in session_files:
                    try:
                        clean = _clean_phone(f)
                        if not clean.isdigit(): continue
                        
                        target = os.path.join(tmp_dir, f"{clean}.session")
                        MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
                        with zf.open(f) as source, open(target, "wb") as t_file:
                            extracted_size = 0
                            while chunk := source.read(8192):
                                extracted_size += len(chunk)
                                if extracted_size > MAX_FILE_SIZE:
                                    raise ValueError(f"File {f} exceeds size limit")
                                t_file.write(chunk)
                        
                        # Add full path for zip extracted files
                        pending_phones.append({"phone": clean, "path": target})
                        
                    except Exception as e:
                        logger.error(f"Bulk 2FA ZIP error on {f}: {e}")
        except Exception:
            logger.exception("Failed to extract ZIP for Bulk 2FA")
            # Drop both the extraction dir and the uploaded archive's dir.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            shutil.rmtree(data.get("zip_tmp_dir") or "", ignore_errors=True)
            await smart_edit(status_msg, "❌ <b>Extraction failed.</b>\nThe archive could not be read.",
                             reply_markup=back_menu_kb(), parse_mode="HTML")
            return

    elif target_type in ("all", "country"):
        all_sessions = get_all_sessions()
        
        folders_to_process = []
        if target_type == "country":
            target_folder = data.get("target_folder")
            if target_folder in all_sessions:
                folders_to_process.append((target_folder, all_sessions[target_folder]))
        else:
            folders_to_process = list(all_sessions.items())

        for folder, phones in folders_to_process:
            for phone in phones:
                session_path = os.path.join(SESSIONS_DIR, folder, phone)
                pending_phones.append({"phone": phone, "path": session_path})

    # Save initial state and begin processing
    await state.update_data(
        pending_phones=pending_phones,
        tmp_dir=tmp_dir,
        success=0,
        failed=0,
        processed=0,
        target_password=password,
        status_msg_id=status_msg.message_id,
        chat_id=message.chat.id
    )

    await _process_next_bulk_2fa(message.bot, state)

async def _process_next_bulk_2fa(bot, state: FSMContext, specific_old_password: str = None):
    data = await state.get_data()
    pending_phones = data.get("pending_phones", [])
    success = data.get("success", 0)
    failed = data.get("failed", 0)
    processed = data.get("processed", 0)
    session_count = data.get("session_count", 0)
    action = data.get("b2fa_action", "set")
    target_password = data.get("target_password", "")
    target_type = data.get("target_type")
    tmp_dir = data.get("tmp_dir", "")
    zip_path = data.get("zip_path", "")
    status_msg_id = data.get("status_msg_id")
    chat_id = data.get("chat_id")

    action_str = {"set": "Setting", "update": "Updating", "delete": "Deleting"}.get(action, "Applying")
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    from telethon.errors.rpcerrorlist import PasswordHashInvalidError

    while pending_phones:
        current_item = pending_phones.pop(0)
        phone = current_item["phone"]
        path = current_item["path"]
        
        try:
            async with session_lock(phone):
                client = create_client(path, api_id, api_hash, proxy)
                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        if specific_old_password is not None:
                            old_password = specific_old_password
                            specific_old_password = None # consume it
                        else:
                            old_password = None
                            if action in ("update", "delete"):
                                old_password = await db.get_2fa(phone)
                        
                        new_pw = target_password if action != "delete" else ""
                        
                        try:
                            await client.edit_2fa(current_password=old_password, new_password=new_pw)
                            if action == "delete":
                                await db.delete_2fa(phone)
                            else:
                                await db.set_2fa(phone, target_password)
                            success += 1
                            
                            if target_type == "zip":
                                # Copy to main sessions dir if success
                                from utils.country_utils import detect_country, get_session_dir
                                _, country_name, _ = detect_country(phone)
                                folder_path = get_session_dir(country_name)
                                shutil.copy2(path, os.path.join(folder_path, f"{phone}.session"))
                                
                        except PasswordHashInvalidError:
                            # Interrupt loop and ask for fallback password
                            await client.disconnect()
                            await state.update_data(
                                pending_phones=pending_phones,
                                success=success,
                                failed=failed,
                                processed=processed,
                                current_failed_item=current_item
                            )
                            await state.set_state(Bulk2FAState.waiting_fallback_password)
                            
                            error_text = (
                                f"❌ <b>Password mismatch for +{phone}</b>\n\n"
                                f"The password in the database does not match the actual Telegram password.\n"
                                f"Please send the <b>correct current password</b> for this account, or click Skip:"
                            )
                            await bot.send_message(chat_id, error_text, reply_markup=skip_fallback_kb(phone), parse_mode="HTML")
                            
                            # Update status message before pausing
                            try:
                                await bot.edit_message_text(f"⏳ <b>{action_str} 2FA Password...</b> (PAUSED)\n\n"
                                    f"⏳ Processing <b>{processed}/{session_count}</b>...\n"
                                    f"✅ Success: {success}  ❌ Failed: {failed}", chat_id=chat_id, message_id=status_msg_id, parse_mode="HTML")
                            except: pass
                            return
                    else:
                        failed += 1
                finally:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Bulk 2FA error on {phone}: {e}")
            failed += 1

        processed += 1
        
        # Periodic UI update
        if processed % 5 == 0 or processed == session_count:
            try:
                await bot.edit_message_text(f"⏳ <b>{action_str} 2FA Password...</b>\n\n"
                    f"⏳ Processing <b>{processed}/{session_count}</b>...\n"
                    f"✅ Success: {success}  ❌ Failed: {failed}", chat_id=chat_id, message_id=status_msg_id, parse_mode="HTML")
            except: pass

    # --- Finished ---
    # Remove the extraction dir AND the directory holding the uploaded archive.
    # Both contain live session files, so neither may be left behind.
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    zip_tmp_dir = data.get("zip_tmp_dir", "")
    if zip_tmp_dir:
        shutil.rmtree(zip_tmp_dir, ignore_errors=True)
    elif zip_path and os.path.exists(zip_path):
        os.remove(zip_path)

    await state.clear()
    
    pw_display = f"<code>{target_password}</code>" if action != "delete" else "<i>Removed</i>"
    
    try:
        await bot.edit_message_text(f"✅ <b>Bulk 2FA {action.capitalize()} Completed!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑 Password: {pw_display}\n"
            f"📱 Total Processed: <b>{session_count}</b>\n"
            f"✅ Success: <b>{success}</b>\n"
            f"❌ Failed / Dead: <b>{failed}</b>",
            chat_id=chat_id, message_id=status_msg_id,
            reply_markup=back_menu_kb(), parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send final summary: {e}")

# ─── Fallback Password Handlers ─────────────────────────────────────────────

@router.message(Bulk2FAState.waiting_fallback_password, F.text)
async def msg_fallback_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    password = message.text.strip()
    data = await state.get_data()
    current_item = data.get("current_failed_item")
    
    if not current_item:
        await message.answer("❌ Error: No session currently waiting for password.", reply_markup=back_menu_kb())
        await _process_next_bulk_2fa(message.bot, state)
        return

    # Put the item back at the front of the queue
    pending_phones = data.get("pending_phones", [])
    pending_phones.insert(0, current_item)
    await state.update_data(pending_phones=pending_phones)
    
    # Try again with the specific password
    await _process_next_bulk_2fa(message.bot, state, specific_old_password=password)


@router.callback_query(F.data.startswith("b2fa_skip:"), Bulk2FAState.waiting_fallback_password)
async def cb_skip_fallback(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        return

    phone = callback.data.split(":")[1]
    data = await state.get_data()
    
    failed = data.get("failed", 0)
    processed = data.get("processed", 0)
    
    await state.update_data(failed=failed + 1, processed=processed + 1)
    
    await smart_edit(callback.message, f"⏭ Skipped +{phone}.", parse_mode="HTML")
    await _process_next_bulk_2fa(callback.bot, state)
