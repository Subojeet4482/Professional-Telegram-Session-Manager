"""
Check Handler — Spam check, Contact limit check, Session validity check.
Supports checking sessions from stored countries or from uploaded ZIP files.
"""
from utils.utils import smart_edit

import os
import zipfile
import tempfile
import asyncio
import logging
import shutil
import time
import database.database as db

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, FSInputFile
from aiogram.fsm.context import FSMContext

from ui.keyboards import (
    check_menu_kb,
    check_countries_list_kb,
    check_all_menu_kb,
    check_all_countries_kb,
    back_to_check_kb,
    back_menu_kb,
    cancel_kb,
)
from utils.country_utils import (
    get_all_sessions,
    get_country_display,
    SESSIONS_DIR,
)
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client, check_spam, check_contact_limit, check_session_alive, check_frozen
from telethon.errors import (
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    FloodWaitError,
    RPCError,
)
from handlers.common import is_admin_async, is_write_admin_async
from ui.states import CheckState

router = Router()
logger = logging.getLogger(__name__)


# ─── Check Menu ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "chk")
async def cb_check_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.clear()
    text = (
        "🔍 <b>Check Sessions</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose a check type:"
    )
    await smart_edit(callback.message, text, reply_markup=check_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Check Type Selected → Show Countries ────────────────────────────────────

@router.callback_query(F.data.startswith("chk_type:"))
async def cb_check_type(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    check_type = callback.data.split(":")[1]
    await _show_check_countries(callback, check_type, page=0)


@router.callback_query(F.data.startswith("chkl:"))
async def cb_check_countries_page(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    check_type = parts[1]
    page = int(parts[2])
    await _show_check_countries(callback, check_type, page)


async def _show_check_countries(callback: CallbackQuery, check_type: str, page: int):
    """Show paginated country list for a check type."""
    all_sessions = get_all_sessions()

    if not all_sessions:
        await smart_edit(callback.message, "📭 <b>No sessions found.</b>\n\nRegister or import sessions first!",
            reply_markup=back_to_check_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    display = {}
    for folder, phones in all_sessions.items():
        flag, name = get_country_display(folder)
        display[folder] = (flag, name, len(phones))

    type_labels = {
        "spam": "🚫 Spam Check",
        "contact": "📇 Contact Limit Check",
        "alive": "✅ Session Validity Check",
    }
    label = type_labels.get(check_type, "Check")

    text = (
        f"🔍 <b>{label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select a country to check, or upload a ZIP:"
    )
    await smart_edit(callback.message, text,
        reply_markup=check_countries_list_kb(display, check_type, page),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Upload ZIP for Check ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("chk_zip:"))
async def cb_check_upload_zip(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    check_type = callback.data.split(":")[1]
    await state.set_state(CheckState.waiting_for_zip)
    await state.update_data(check_type=check_type)

    type_labels = {
        "spam": "🚫 Spam Check",
        "contact": "📇 Contact Limit Check",
        "alive": "✅ Session Validity Check",
    }
    label = type_labels.get(check_type, "Check")

    text = (
        f"📤 <b>Upload ZIP — {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send me a <b>.zip</b> file containing <code>.session</code> files.\n\n"
        "⚠️ These sessions will <b>NOT</b> be saved to storage.\n"
        "They will only be checked and results sent back."
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(CheckState.waiting_for_zip, F.document)
async def msg_check_zip_received(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    doc = message.document
    if not doc.file_name.endswith(".zip"):
        await message.answer("❌ Please send a <code>.zip</code> file.", reply_markup=cancel_kb(), parse_mode="HTML")
        return

    data = await state.get_data()
    check_type = data.get("check_type", "alive")
    await state.clear()

    status_msg = await message.answer("⏳ <b>Downloading and extracting ZIP...</b>", parse_mode="HTML")

    bot = message.bot
    file_info = await bot.get_file(doc.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)

    # Create temp directory for extracted sessions
    tmp_dir = tempfile.mkdtemp(prefix="chk_zip_")

    try:
        # Extract session files
        session_files = []
        with zipfile.ZipFile(downloaded_file, "r") as zf:
            all_files = zf.namelist()
            for f in all_files:
                if f.endswith(".session") and "__MACOSX" not in f:
                    # Clean the filename
                    basename = os.path.basename(f)
                    clean = _clean_phone(basename)
                    if clean and clean.isdigit():
                        target = os.path.join(tmp_dir, f"{clean}.session")
                        MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
                        with zf.open(f) as source, open(target, "wb") as out:
                            extracted_size = 0
                            while chunk := source.read(8192):
                                extracted_size += len(chunk)
                                if extracted_size > MAX_FILE_SIZE:
                                    raise ValueError("File exceeds size limit")
                                out.write(chunk)
                        session_files.append(clean)

        if not session_files:
            status_msg = await smart_edit(status_msg, "📭 <b>No valid .session files found in ZIP!</b>",
                reply_markup=back_to_check_kb(), parse_mode="HTML",
            )
            return

        status_msg = await smart_edit(status_msg, f"⏳ <b>Checking {len(session_files)} sessions...</b>\n\n"
            f"⏳ Processing <b>0/{len(session_files)}</b>...",
            parse_mode="HTML",
        )

        # Run checks
        results = await _run_checks(
            check_type, session_files, tmp_dir,
            status_msg, message, is_zip=True
        )

        # Send results
        await _send_results(check_type, results, tmp_dir, message, status_msg)

    except Exception as e:
        logger.error(f"Check ZIP error: {e}")
        status_msg = await smart_edit(status_msg, f"❌ <b>Check failed:</b> <code>{e}</code>",
            reply_markup=back_to_check_kb(), parse_mode="HTML",
        )
    finally:
        # Cleanup temp directory - do NOT save to storage
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── Check Country Sessions ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("chkc:"))
async def cb_check_country(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return

    check_type = parts[1]
    folder = ":".join(parts[2:])
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    folder_path = os.path.join(SESSIONS_DIR, folder)

    type_labels = {
        "spam": "🚫 Spam Check",
        "contact": "📇 Contact Limit Check",
        "alive": "✅ Session Validity Check",
    }
    label = type_labels.get(check_type, "Check")

    await smart_edit(callback.message, f"⏳ <b>{label} — {flag} {name}</b>\n\n"
        f"⏳ Processing <b>0/{len(phones)}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    # Run checks
    results = await _run_checks(
        check_type, phones, folder_path,
        callback.message, callback.message, is_zip=False
    )

    # Send results
    await _send_results(check_type, results, folder_path, callback.message, callback.message)


# ─── Check All Countries for Specific Type ────────────────────────────────────

@router.callback_query(F.data.startswith("chk_run_all:"))
async def cb_check_run_all(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    check_type = callback.data.split(":")[1]
    all_sessions = get_all_sessions()
    
    if not all_sessions:
        await smart_edit(callback.message, "📭 <b>No sessions found.</b>",
            reply_markup=back_to_check_kb(), parse_mode="HTML")
        await callback.answer()
        return

    phone_folder_map: dict[str, str] = {}
    all_phones: list[str] = []
    for folder, phones in all_sessions.items():
        folder_path = os.path.join(SESSIONS_DIR, folder)
        for phone in phones:
            if phone not in phone_folder_map:
                phone_folder_map[phone] = folder_path
                all_phones.append(phone)

    total = len(all_phones)
    
    type_labels = {
        "spam": "🚫 Spam Check",
        "contact": "📇 Contact Limit Check",
        "alive": "✅ Session Validity Check",
    }
    label = type_labels.get(check_type, "Check")

    await smart_edit(callback.message,
        f"⏳ <b>{label} — All Countries</b>\n\n"
        f"⏳ Processing <b>0/{total}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    # Run checks
    results = await _run_checks(
        check_type, all_phones, "",
        callback.message, callback.message, is_zip=False,
        phone_folder_map=phone_folder_map
    )

    # Send results
    await _send_results(check_type, results, "", callback.message, callback.message, phone_folder_map=phone_folder_map)


# ─── Check All Types — Sub-menu ─────────────────────────────────────────────

@router.callback_query(F.data == "chk_all")
async def cb_check_all_menu(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    text = (
        "🔍 <b>Check All Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "This will run Spam + Contact + Alive checks on the selected target.\n\n"
        "Choose the target:"
    )
    await smart_edit(callback.message, text, reply_markup=check_all_menu_kb(), parse_mode="HTML")
    await callback.answer()


# ─── Check All Types — Country List ─────────────────────────────────────────

@router.callback_query(F.data.startswith("chk_all_p:"))
async def cb_check_all_countries_page(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    page = int(callback.data.split(":")[1])
    await _show_check_all_countries(callback, page)


async def _show_check_all_countries(callback: CallbackQuery, page: int):
    all_sessions = get_all_sessions()
    if not all_sessions:
        await smart_edit(callback.message, "📭 <b>No sessions found.</b>",
            reply_markup=back_to_check_kb(), parse_mode="HTML")
        await callback.answer()
        return

    display = {}
    for folder, phones in all_sessions.items():
        flag, name = get_country_display(folder)
        display[folder] = (flag, name, len(phones))

    text = (
        "🔍 <b>Comprehensive Check</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select a country to perform a full diagnostic (Spam, Contact Limit, and Validity check):"
    )
    await smart_edit(callback.message, text,
        reply_markup=check_all_countries_kb(display, page),
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Check All Types — Specific Country ──────────────────────────────────────

@router.callback_query(F.data.startswith("chk_all_c:"))
async def cb_check_all_country(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    folder = callback.data.split(":", 1)[1]
    all_sessions = get_all_sessions()

    if folder not in all_sessions:
        await callback.answer("❌ Country not found", show_alert=True)
        return

    phones = all_sessions[folder]
    flag, name = get_country_display(folder)
    folder_path = os.path.join(SESSIONS_DIR, folder)

    await smart_edit(callback.message,
        f"⏳ <b>Checking All Status — {flag} {name}</b>\n\n"
        f"⏳ Processing <b>0/{len(phones)}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    spam_results, contact_results = await _run_all_types_check(
        phones, {p: folder_path for p in phones}, callback.message
    )
    await _send_all_types_results(spam_results, contact_results,
        {p: folder_path for p in phones}, callback.message, callback.message)


# ─── Check All Types — Upload ZIP ────────────────────────────────────────────

@router.callback_query(F.data == "chk_all_zip")
async def cb_check_all_zip(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(CheckState.waiting_for_all_zip)
    text = (
        "📤 <b>Upload ZIP — Check All Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send a <b>.zip</b> file containing <code>.session</code> files.\n\n"
        "Spam + Contact + Alive checks will be run on them.\n"
        "⚠️ Sessions will not be saved in storage."
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(CheckState.waiting_for_all_zip, F.document)
async def msg_check_all_zip_received(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    doc = message.document
    if not doc.file_name.endswith(".zip"):
        await message.answer("❌ Please send a <code>.zip</code> file.", reply_markup=cancel_kb(), parse_mode="HTML")
        return

    await state.clear()
    status_msg = await message.answer("⏳ <b>Downloading and extracting ZIP...</b>", parse_mode="HTML")

    bot = message.bot
    file_info = await bot.get_file(doc.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)

    tmp_dir = tempfile.mkdtemp(prefix="chk_all_zip_")
    try:
        session_files = []
        with zipfile.ZipFile(downloaded_file, "r") as zf:
            for f in zf.namelist():
                if f.endswith(".session") and "__MACOSX" not in f:
                    basename = os.path.basename(f)
                    clean = _clean_phone(basename)
                    if clean and clean.isdigit():
                        target = os.path.join(tmp_dir, f"{clean}.session")
                        MAX_FILE_SIZE = 50 * 1024 * 1024
                        with zf.open(f) as source, open(target, "wb") as out:
                            extracted_size = 0
                            while chunk := source.read(8192):
                                extracted_size += len(chunk)
                                if extracted_size > MAX_FILE_SIZE:
                                    raise ValueError("File exceeds size limit")
                                out.write(chunk)
                        session_files.append(clean)

        if not session_files:
            await smart_edit(status_msg, "📭 <b>No valid .session files found in ZIP!</b>",
                reply_markup=back_to_check_kb(), parse_mode="HTML")
            return

        phone_folder_map = {p: tmp_dir for p in session_files}
        await smart_edit(status_msg,
            f"⏳ <b>Checking {len(session_files)} sessions (All Types)...</b>\n\n"
            f"⏳ Processing <b>0/{len(session_files)}</b>...",
            parse_mode="HTML",
        )

        spam_results, contact_results = await _run_all_types_check(
            session_files, phone_folder_map, status_msg
        )
        await _send_all_types_results(spam_results, contact_results, phone_folder_map, message, status_msg)

    except Exception as e:
        logger.error(f"Check All ZIP error: {e}")
        await smart_edit(status_msg, f"❌ <b>Check failed:</b> <code>{e}</code>",
            reply_markup=back_to_check_kb(), parse_mode="HTML")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── Check All Types — All Countries ─────────────────────────────────────────

@router.callback_query(F.data == "chk_all_run")
async def cb_check_all_run(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    all_sessions = get_all_sessions()
    if not all_sessions:
        await smart_edit(callback.message, "📭 <b>No sessions found.</b>",
            reply_markup=back_to_check_kb(), parse_mode="HTML")
        await callback.answer()
        return

    phone_folder_map: dict[str, str] = {}
    all_phones: list[str] = []
    for folder, phones in all_sessions.items():
        folder_path = os.path.join(SESSIONS_DIR, folder)
        for phone in phones:
            if phone not in phone_folder_map:
                phone_folder_map[phone] = folder_path
                all_phones.append(phone)

    total = len(all_phones)
    await smart_edit(callback.message,
        f"⏳ <b>Checking All Status — All Countries</b>\n\n"
        f"⏳ Processing <b>0/{total}</b>...",
        parse_mode="HTML",
    )
    await callback.answer()

    spam_results, contact_results = await _run_all_types_check(
        all_phones, phone_folder_map, callback.message
    )
    await _send_all_types_results(spam_results, contact_results, phone_folder_map,
        callback.message, callback.message)


# ─── Core Check Logic ────────────────────────────────────────────────────────

def _clean_phone(filename: str) -> str:
    """Extract clean phone from filename."""
    name = os.path.basename(filename)
    if name.endswith(".session"):
        name = name[:-8]
    return name.replace("+", "").replace(" ", "").replace("-", "").strip()


async def _run_checks(
    check_type: str,
    phones: list[str],
    sessions_dir: str,
    status_msg,
    context_msg,
    is_zip: bool = False,
    phone_folder_map: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """
    Run checks on a list of phones.
    Returns dict mapping status -> list of phones.
    """
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    total = len(phones)

    # Initialize results based on check type
    if check_type == "spam":
        results = {"Spam": [], "Free": [], "New": [], "Frozen": [], "Die": [], "Error": []}
    elif check_type == "contact":
        results = {"NoLimit": [], "Limited": [], "Frozen": [], "Die": [], "Error": []}
    else:  # alive
        results = {"Live": [], "Frozen": [], "Die": []}

    sem = asyncio.Semaphore(15)
    processed = 0
    last_update = time.time()

    account_updates = []
    spam_updates = []
    contact_updates = []

    async def _process_phone(phone: str):
        nonlocal processed, last_update
        if phone_folder_map and phone in phone_folder_map:
            session_path = os.path.join(phone_folder_map[phone], phone)
        else:
            session_path = os.path.join(sessions_dir, phone)

        async with sem:
            try:
                client = create_client(session_path, api_id, api_hash, proxy)
                await client.connect()

                if not await client.is_user_authorized():
                    results["Die"].append(phone)
                    account_updates.append((phone, "died"))
                    logger.info(f"Check [{check_type}] +{phone}: Die")
                else:
                    if check_type == "spam":
                        status = await check_spam(client)
                        if status == "FROZEN":
                            results["Frozen"].append(phone)
                            account_updates.append((phone, "frozen"))
                            logger.info(f"Check [{check_type}] +{phone}: Frozen")
                        elif status == "SPAM":
                            results["Spam"].append(phone)
                            spam_updates.append((phone, status))
                            account_updates.append((phone, "live"))
                        elif status == "FREE":
                            results["Free"].append(phone)
                            spam_updates.append((phone, status))
                            account_updates.append((phone, "live"))
                        elif status == "NEW_REGISTERED":
                            status = "NEW"
                            results["New"].append(phone)
                            spam_updates.append((phone, status))
                            account_updates.append((phone, "live"))
                        elif status == "BANNED":
                            results["Die"].append(phone)
                            account_updates.append((phone, "died"))
                        else:
                            results["Error"].append(phone)

                    elif check_type == "contact":
                        status = await check_contact_limit(client)
                        if status == "FROZEN":
                            results["Frozen"].append(phone)
                            account_updates.append((phone, "frozen"))
                            logger.info(f"Check [{check_type}] +{phone}: Frozen")
                        elif status == "NoLimit":
                            results["NoLimit"].append(phone)
                            contact_updates.append((phone, status))
                            account_updates.append((phone, "live"))
                        elif status == "Limited":
                            results["Limited"].append(phone)
                            contact_updates.append((phone, status))
                            account_updates.append((phone, "live"))
                        else:
                            results["Error"].append(phone)

                    else:  # alive
                        # is_user_authorized is passive — won't detect frozen
                        # Use check_frozen (active probe) to detect frozen accounts
                        try:
                            frozen = await check_frozen(client)
                            if frozen:
                                results["Frozen"].append(phone)
                                account_updates.append((phone, "frozen"))
                                logger.info(f"Check [{check_type}] +{phone}: Frozen")
                            else:
                                results["Live"].append(phone)
                                account_updates.append((phone, "live"))
                        except Exception:
                            # If the probe fails for non-FROZEN reasons, account is still live
                            results["Live"].append(phone)
                            account_updates.append((phone, "live"))

                    logger.info(f"Check [{check_type}] +{phone}: done")

            except (UserDeactivatedBanError, AuthKeyUnregisteredError) as e:
                logger.info(f"Check [{check_type}] +{phone}: Die (deactivated/unregistered: {e})")
                results["Die"].append(phone)
                account_updates.append((phone, "died"))
            except FloodWaitError as e:
                logger.warning(f"Check [{check_type}] +{phone}: Frozen (FloodWait {e.seconds}s)")
                results["Frozen"].append(phone)
                account_updates.append((phone, "frozen"))
            except RPCError as e:
                if "FROZEN" in str(e).upper():
                    logger.info(f"Check [{check_type}] +{phone}: Frozen (RPCError FROZEN: {e})")
                    results["Frozen"].append(phone)
                    account_updates.append((phone, "frozen"))
                else:
                    logger.error(f"Check [{check_type}] +{phone}: Error (RPCError: {e})")
                    if "Error" in results:
                        results["Error"].append(phone)
                    else:
                        results["Frozen"].append(phone)
            except (ConnectionError, OSError) as e:
                logger.warning(f"Check [{check_type}] +{phone}: Frozen (connection/OS error: {e})")
                results["Frozen"].append(phone)
            except Exception as e:
                error_msg = str(e).lower()
                if "deactivated" in error_msg or "banned" in error_msg or "deleted" in error_msg:
                    logger.info(f"Check [{check_type}] +{phone}: Die (from error: {e})")
                    results["Die"].append(phone)
                    account_updates.append((phone, "died"))
                elif "frozen" in error_msg:
                    logger.info(f"Check [{check_type}] +{phone}: Frozen (from error: {e})")
                    results["Frozen"].append(phone)
                    account_updates.append((phone, "frozen"))
                else:
                    logger.error(f"Check [{check_type}] +{phone}: Frozen (unknown: {e})")
                    results["Frozen"].append(phone)
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            processed += 1

            # Update progress every 2 seconds or at the end
            now = time.time()
            if now - last_update > 2.0 or processed == total:
                last_update = now
                try:
                    progress_lines = [f"⏳ Processing <b>{processed}/{total}</b>..."]
                    for key, vals in results.items():
                        if vals:
                            progress_lines.append(f"  {key}: {len(vals)}")

                    status_msg = await smart_edit(status_msg, f"⏳ <b>Checking...</b>\n\n" + "\n".join(progress_lines),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    tasks = [_process_phone(phone) for phone in phones]
    if tasks:
        await asyncio.gather(*tasks)

    # Perform bulk database updates
    if not is_zip:
        if account_updates:
            await db.bulk_set_account_status(account_updates)
        if spam_updates:
            await db.bulk_set_spam_status(spam_updates)
        if contact_updates:
            await db.bulk_set_contact_status(contact_updates)
    else:
        if account_updates:
            await db.bulk_update_account_status(account_updates)
        if spam_updates:
            await db.bulk_update_spam_status(spam_updates)
        if contact_updates:
            await db.bulk_update_contact_status(contact_updates)

    return results


async def _send_results(
    check_type: str,
    results: dict[str, list[str]],
    sessions_dir: str,
    context_msg,
    status_msg,
    phone_folder_map: dict[str, str] | None = None,
):
    """Send formatted results and ZIP files grouped by status."""
    total = sum(len(v) for v in results.values())

    # Build results text
    if check_type == "spam":
        title = "✅ Completed limit check!"
        lines = [
            f"🚫 Spam: {len(results.get('Spam', []))}",
            f"✅ Free: {len(results.get('Free', []))}",
            f"🆕 New: {len(results.get('New', []))}",
            f"🧊 Frozen: {len(results.get('Frozen', []))}",
            f"❌ Die: {len(results.get('Die', []))}",
            f"⚠️ Error: {len(results.get('Error', []))}",
        ]
    elif check_type == "contact":
        title = "✅ Completed contact limit check!"
        lines = [
            f"✅ NoLimit: {len(results.get('NoLimit', []))}",
            f"⚠️ Limited: {len(results.get('Limited', []))}",
            f"🧊 Frozen: {len(results.get('Frozen', []))}",
            f"❌ Die: {len(results.get('Die', []))}",
            f"⚠️ Error: {len(results.get('Error', []))}",
        ]
    else:  # alive
        title = "✅ Completed processing!"
        lines = [
            f"✅ Live: {len(results.get('Live', []))}",
            f"🧊 Frozen: {len(results.get('Frozen', []))}",
            f"❌ Die: {len(results.get('Die', []))}",
        ]

    results_text = (
        f"<b>{title}</b>\n\n"
        f"📊 <b>Results:</b>\n" +
        "\n".join(lines) +
        f"\n\n📋 <b>Total: {total}</b>"
    )

    status_msg = await smart_edit(status_msg, results_text, reply_markup=back_to_check_kb(), parse_mode="HTML")

    # Send ZIP files per status (only for statuses with sessions)
    for status_name, phone_list in results.items():
        if not phone_list:
            continue

        _tmp_dir = tempfile.mkdtemp(prefix="check_zip_")
        tmp_zip = os.path.join(_tmp_dir, f"check_{status_name}.zip")
        try:
            session_count = 0
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for phone in phone_list:
                    if phone_folder_map and phone in phone_folder_map:
                        session_file = os.path.join(phone_folder_map[phone], f"{phone}.session")
                    else:
                        session_file = os.path.join(sessions_dir, f"{phone}.session")
                    if os.path.exists(session_file):
                        zf.write(session_file, f"{phone}.session")
                        session_count += 1

            if session_count > 0:
                doc = FSInputFile(tmp_zip, filename=f"{status_name}.zip")
                caption = f"📦 {status_name}: {session_count} session"
                await context_msg.answer_document(doc, caption=caption)
                await asyncio.sleep(0.3)

        except Exception as e:
            logger.error(f"Failed to send {status_name} ZIP: {e}")
        finally:
            shutil.rmtree(_tmp_dir, ignore_errors=True)


# ─── All Types Check Helpers ─────────────────────────────────────────────────

async def _run_all_types_check(
    phones: list[str],
    phone_folder_map: dict[str, str],
    status_msg,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Connect once per session and run spam + contact checks together."""
    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()
    total = len(phones)

    spam_results: dict[str, list[str]] = {"Spam": [], "Free": [], "New": [], "Frozen": [], "Die": [], "Error": []}
    contact_results: dict[str, list[str]] = {"NoLimit": [], "Limited": [], "Frozen": [], "Die": [], "Error": []}

    sem = asyncio.Semaphore(15)
    processed = 0
    last_update = time.time()

    account_updates: list[tuple[str, str]] = []
    spam_updates: list[tuple[str, str]] = []
    contact_updates: list[tuple[str, str]] = []

    async def _process_phone(phone: str):
        nonlocal processed, last_update
        session_path = os.path.join(phone_folder_map.get(phone, ""), phone)

        async with sem:
            try:
                client = create_client(session_path, api_id, api_hash, proxy)
                await client.connect()

                if not await client.is_user_authorized():
                    spam_results["Die"].append(phone)
                    contact_results["Die"].append(phone)
                    account_updates.append((phone, "died"))
                else:
                    # Spam check
                    spam_status = await check_spam(client)
                    if spam_status == "FROZEN":
                        spam_results["Frozen"].append(phone)
                        contact_results["Frozen"].append(phone)
                        account_updates.append((phone, "frozen"))
                        logger.info(f"Check all +{phone}: Frozen (detected via spam check)")
                    elif spam_status == "SPAM":
                        spam_results["Spam"].append(phone)
                        spam_updates.append((phone, "SPAM"))
                        account_updates.append((phone, "live"))
                    elif spam_status == "FREE":
                        spam_results["Free"].append(phone)
                        spam_updates.append((phone, "FREE"))
                        account_updates.append((phone, "live"))
                    elif spam_status == "NEW_REGISTERED":
                        spam_results["New"].append(phone)
                        spam_updates.append((phone, "NEW"))
                        account_updates.append((phone, "live"))
                    elif spam_status == "BANNED":
                        spam_results["Die"].append(phone)
                        contact_results["Die"].append(phone)
                        account_updates.append((phone, "died"))
                    else:
                        spam_results["Error"].append(phone)

                    # Contact check (skip if banned or frozen)
                    if spam_status not in ("BANNED", "FROZEN"):
                        contact_status = await check_contact_limit(client)
                        if contact_status == "FROZEN":
                            contact_results["Frozen"].append(phone)
                            # Update spam too if not already frozen
                            if phone not in spam_results["Frozen"]:
                                spam_results["Frozen"].append(phone)
                            account_updates.append((phone, "frozen"))
                            logger.info(f"Check all +{phone}: Frozen (detected via contact check)")
                        elif contact_status == "NoLimit":
                            contact_results["NoLimit"].append(phone)
                            contact_updates.append((phone, "NoLimit"))
                        elif contact_status == "Limited":
                            contact_results["Limited"].append(phone)
                            contact_updates.append((phone, "Limited"))
                        else:
                            contact_results["Error"].append(phone)

            except (UserDeactivatedBanError, AuthKeyUnregisteredError) as e:
                logger.info(f"Check all +{phone}: Die (deactivated/unregistered: {e})")
                spam_results["Die"].append(phone)
                contact_results["Die"].append(phone)
                account_updates.append((phone, "died"))
            except FloodWaitError as e:
                logger.warning(f"Check all +{phone}: Frozen (FloodWait {e.seconds}s)")
                spam_results["Frozen"].append(phone)
                contact_results["Frozen"].append(phone)
                account_updates.append((phone, "frozen"))
            except RPCError as e:
                if "FROZEN" in str(e).upper():
                    logger.info(f"Check all +{phone}: Frozen (RPCError FROZEN: {e})")
                    spam_results["Frozen"].append(phone)
                    contact_results["Frozen"].append(phone)
                    account_updates.append((phone, "frozen"))
                else:
                    logger.error(f"Check all +{phone}: Error (RPCError: {e})")
                    spam_results["Error"].append(phone)
                    contact_results["Error"].append(phone)
            except (ConnectionError, OSError) as e:
                logger.warning(f"Check all +{phone}: Frozen (connection/OS error: {e})")
                spam_results["Frozen"].append(phone)
                contact_results["Frozen"].append(phone)
            except Exception as e:
                error_msg = str(e).lower()
                if "deactivated" in error_msg or "banned" in error_msg or "deleted" in error_msg:
                    logger.info(f"Check all +{phone}: Die (from error: {e})")
                    spam_results["Die"].append(phone)
                    contact_results["Die"].append(phone)
                    account_updates.append((phone, "died"))
                elif "frozen" in error_msg:
                    logger.info(f"Check all +{phone}: Frozen (from error: {e})")
                    spam_results["Frozen"].append(phone)
                    contact_results["Frozen"].append(phone)
                    account_updates.append((phone, "frozen"))
                else:
                    logger.error(f"Check all +{phone}: Frozen (unknown: {e})")
                    spam_results["Frozen"].append(phone)
                    contact_results["Frozen"].append(phone)
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            processed += 1
            now = time.time()
            if now - last_update > 2.0 or processed == total:
                last_update = now
                try:
                    await smart_edit(
                        status_msg,
                        f"⏳ <b>Checking All Accounts...</b>\n\n"
                        f"⏳ Processing <b>{processed}/{total}</b>...\n\n"
                        f"<b>Spam:</b> 🚫{len(spam_results['Spam'])} ✅{len(spam_results['Free'])} "
                        f"🆕{len(spam_results['New'])} ❌{len(spam_results['Die'])}\n"
                        f"<b>Contact:</b> ✅{len(contact_results['NoLimit'])} "
                        f"⚠️{len(contact_results['Limited'])} ❌{len(contact_results['Die'])}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    await asyncio.gather(*[_process_phone(p) for p in phones])

    if account_updates:
        await db.bulk_set_account_status(account_updates)
    if spam_updates:
        await db.bulk_set_spam_status(spam_updates)
    if contact_updates:
        await db.bulk_set_contact_status(contact_updates)

    return spam_results, contact_results


async def _send_all_types_results(
    spam_results: dict[str, list[str]],
    contact_results: dict[str, list[str]],
    phone_folder_map: dict[str, str],
    context_msg,
    status_msg,
):
    """Send combined results summary and ZIP files for all 3 types."""
    total = sum(len(v) for v in spam_results.values())
    live_count = len(spam_results.get("Free", [])) + len(spam_results.get("Spam", [])) + len(spam_results.get("New", []))
    frozen_count = len(spam_results.get("Frozen", []))
    dead_count = len(spam_results.get("Die", []))

    results_text = (
        "<b>✅ Completed All Checks!</b>\n\n"
        "📊 <b>Spam Results:</b>\n"
        f"🚫 Spam: {len(spam_results.get('Spam', []))}\n"
        f"✅ Free: {len(spam_results.get('Free', []))}\n"
        f"🆕 New: {len(spam_results.get('New', []))}\n"
        f"🧊 Frozen: {frozen_count}\n"
        f"❌ Die: {dead_count}\n"
        f"⚠️ Error: {len(spam_results.get('Error', []))}\n\n"
        "📊 <b>Contact Results:</b>\n"
        f"✅ NoLimit: {len(contact_results.get('NoLimit', []))}\n"
        f"⚠️ Limited: {len(contact_results.get('Limited', []))}\n"
        f"🧊 Frozen: {len(contact_results.get('Frozen', []))}\n"
        f"❌ Die: {len(contact_results.get('Die', []))}\n\n"
        "📊 <b>Alive Results:</b>\n"
        f"✅ Live: {live_count}\n"
        f"🧊 Frozen: {frozen_count}\n"
        f"❌ Die: {dead_count}\n\n"
        f"📋 <b>Total Checked: {total}</b>"
    )

    await smart_edit(status_msg, results_text, reply_markup=back_to_check_kb(), parse_mode="HTML")

    async def _send_zip(prefix: str, status_name: str, phone_list: list[str]):
        if not phone_list:
            return
        zip_name = f"{prefix}_{status_name}.zip" if prefix else f"{status_name}.zip"
        _tmp_dir = tempfile.mkdtemp(prefix="check_zip_")
        tmp_zip = os.path.join(_tmp_dir, zip_name)
        try:
            session_count = 0
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for phone in phone_list:
                    session_file = os.path.join(phone_folder_map.get(phone, ""), f"{phone}.session")
                    if os.path.exists(session_file):
                        zf.write(session_file, f"{phone}.session")
                        session_count += 1
            if session_count > 0:
                doc = FSInputFile(tmp_zip, filename=zip_name)
                caption_prefix = f"{prefix} " if prefix else ""
                await context_msg.answer_document(doc, caption=f"📦 {caption_prefix}{status_name}: {session_count} session(s)")
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Failed to send {prefix} {status_name} ZIP: {e}")
        finally:
            shutil.rmtree(_tmp_dir, ignore_errors=True)

    # Shared statuses
    die_phones = list(set(spam_results.get("Die", []) + contact_results.get("Die", [])))
    frozen_phones = list(set(spam_results.get("Frozen", []) + contact_results.get("Frozen", [])))
    error_phones = list(set(spam_results.get("Error", []) + contact_results.get("Error", [])))

    await _send_zip("", "Die", die_phones)
    await _send_zip("", "Frozen", frozen_phones)
    await _send_zip("", "Error", error_phones)

    # Specific statuses
    for status_name in ["Spam", "Free", "New"]:
        await _send_zip("spam", status_name, spam_results.get(status_name, []))

    for status_name in ["NoLimit", "Limited"]:
        await _send_zip("contact", status_name, contact_results.get(status_name, []))
