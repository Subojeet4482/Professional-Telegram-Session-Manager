"""
Convert Handler — Convert accounts between tdata, .session, .json and .txt.

Flow:
    🔄 Convert → pick mode → pick source (upload / all countries / one country)
              → "does it have a 2FA password?" → run → result ZIP

Each converted account is delivered as its own folder:

    +48699585146/
        +48699585146.session
        +48699585146.json
        2FA.txt          (omitted when the account has no 2FA)
        tdata/           (session → tdata only)

The bot's own `sessions/` storage is never modified — Convert only reads from
it and writes bundles into a temporary workspace that is zipped and sent back.
"""

import asyncio
import logging
import os
import shutil
import tempfile
import zipfile

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

import database.database as db
from config.config_manager import get_proxy
from handlers.common import is_write_admin_async
from ui.keyboards import (
    CONVERT_MODES,
    back_to_convert_kb,
    cancel_kb,
    convert_countries_kb,
    convert_menu_kb,
    convert_password_kb,
    convert_source_kb,
)
from ui.states import ConvertState
from utils.convert_utils import (
    MAX_ACCOUNTS_PER_BATCH,
    convert_account,
    find_tdata_folders,
    normalize_digits,
    phone_from_filename,
)
from utils.country_utils import (
    SESSIONS_DIR,
    get_all_sessions,
    get_country_display,
)
from utils.utils import smart_edit

router = Router()
logger = logging.getLogger(__name__)

# Telegram rejects bot uploads above 50 MB; leave headroom for the zip container.
MAX_ZIP_BYTES = 45 * 1024 * 1024
# Zip-bomb guard, matching the limit the import flow already applies.
MAX_EXTRACT_BYTES = 50 * 1024 * 1024
# How often the progress message is refreshed, in accounts.
PROGRESS_EVERY = 10


# ── Menu ──────────────────────────────────────────────────────────────────────

def _menu_text() -> str:
    return (
        "🔄 <b>Convert</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose a conversion:\n\n"
        "• <b>TData → Session</b> — upload a .zip of tdata folders\n"
        "• <b>Session → TData</b> — build a Telegram Desktop profile\n"
        "• <b>Session → JSON</b> — export account metadata\n"
        "• <b>Session → All</b> — tdata + session + json + 2FA.txt\n\n"
        "<i>Each account is returned in its own folder.</i>"
    )


@router.callback_query(F.data == "cv_menu")
async def cb_convert_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await _cleanup_workspace(state)
    await state.clear()
    await smart_edit(callback.message, _menu_text(), reply_markup=convert_menu_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cv_mode:"))
async def cb_convert_mode(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    mode = callback.data.split(":", 1)[1]
    if mode not in CONVERT_MODES:
        await callback.answer("❌ Unknown conversion.", show_alert=True)
        return

    await state.update_data(mode=mode)

    hint = {
        "tdata2session": "Upload a <b>.zip</b> containing one or more <code>tdata</code> folders.",
        "session2tdata": "Sessions must be <b>alive</b> — tdata can only be built from an authorized session.",
        "session2json":  "Profile fields are read live where possible; unreachable accounts get <code>null</code>.",
        "session2all":   (
            "Full bundle per account: <code>tdata/</code> + <code>.session</code> + "
            "<code>.json</code> + <code>2FA.txt</code>.\n"
            "Sessions must be <b>alive</b> (required to build tdata). "
            "<code>2FA.txt</code> is skipped for accounts with no password."
        ),
    }[mode]

    text = (
        f"{CONVERT_MODES[mode]}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{hint}\n\n"
        "<b>What should I convert?</b>"
    )
    await smart_edit(callback.message, text, reply_markup=convert_source_kb(mode), parse_mode="HTML")
    await callback.answer()


# ── Source selection ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cv_up:"))
async def cb_convert_upload(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    mode = callback.data.split(":", 1)[1]
    await state.update_data(mode=mode, scope="upload")
    await state.set_state(ConvertState.waiting_file)

    accepted = ".zip" if mode == "tdata2session" else ".zip or .session"
    text = (
        f"{CONVERT_MODES[mode]}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 Send me a <b>{accepted}</b> file."
    )
    await smart_edit(callback.message, text, reply_markup=cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cv_all:"))
async def cb_convert_all(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    mode = callback.data.split(":", 1)[1]
    total = sum(len(v) for v in get_all_sessions().values())
    if not total:
        await smart_edit(callback.message, "📭 <b>No sessions stored yet.</b>",
                         reply_markup=back_to_convert_kb(), parse_mode="HTML")
        await callback.answer()
        return

    await state.update_data(mode=mode, scope="all")
    await _ask_password(callback.message, state, f"{total} account(s) across all countries")
    await callback.answer()


@router.callback_query(F.data.startswith("cv_country:"))
async def cb_convert_country_list(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    _, mode, page = callback.data.split(":", 2)
    all_sessions = get_all_sessions()
    if not all_sessions:
        await smart_edit(callback.message, "📭 <b>No sessions stored yet.</b>",
                         reply_markup=back_to_convert_kb(), parse_mode="HTML")
        await callback.answer()
        return

    countries = {}
    for folder, phones in all_sessions.items():
        flag, name = get_country_display(folder)
        countries[folder] = (flag, name, len(phones))

    await smart_edit(
        callback.message,
        f"{CONVERT_MODES[mode]}\n━━━━━━━━━━━━━━━━━━━━━\n\n🌍 <b>Pick a country:</b>",
        reply_markup=convert_countries_kb(mode, countries, int(page)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cv_c:"))
async def cb_convert_country_pick(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    _, mode, folder = callback.data.split(":", 2)
    phones = get_all_sessions().get(folder, [])
    if not phones:
        await callback.answer("📭 No sessions in that country.", show_alert=True)
        return

    flag, name = get_country_display(folder)
    await state.update_data(mode=mode, scope="country", folder=folder)
    await _ask_password(callback.message, state, f"{len(phones)} account(s) in {flag} {name}")
    await callback.answer()


# ── File upload ───────────────────────────────────────────────────────────────

@router.message(ConvertState.waiting_file, F.document)
async def msg_convert_file(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    data = await state.get_data()
    mode = data.get("mode")
    if not mode:
        await message.answer("❌ Session expired. Please start again.", reply_markup=back_to_convert_kb())
        return

    filename = message.document.file_name or ""
    allowed = (".zip",) if mode == "tdata2session" else (".zip", ".session")
    if not filename.lower().endswith(allowed):
        await message.answer(
            f"❌ Please send a {' or '.join(f'<code>{a}</code>' for a in allowed)} file.",
            reply_markup=cancel_kb(), parse_mode="HTML",
        )
        return

    status = await message.answer("⏳ <b>Downloading file...</b>", parse_mode="HTML")

    workspace = tempfile.mkdtemp(prefix="convert_")
    upload_path = os.path.join(workspace, os.path.basename(filename))
    try:
        file_info = await message.bot.get_file(message.document.file_id)
        await message.bot.download_file(file_info.file_path, destination=upload_path)
    except Exception as e:
        shutil.rmtree(workspace, ignore_errors=True)
        logger.error(f"Convert download failed: {e}")
        await smart_edit(status, f"❌ Download failed: <code>{e}</code>",
                         reply_markup=back_to_convert_kb(), parse_mode="HTML")
        return

    await state.update_data(workspace=workspace, upload_path=upload_path)
    await _ask_password(status, state, f"the uploaded <code>{filename}</code>")


# ── 2FA password step ─────────────────────────────────────────────────────────

async def _count_saved_2fa(state: FSMContext) -> int | None:
    """
    How many of the selected accounts already have a password in the database.

    Returns None for uploads, where the phone numbers aren't known until the
    archive is unpacked.
    """
    data = await state.get_data()
    scope = data.get("scope")
    if scope == "upload":
        return None

    try:
        saved = await db.get_all_2fa()
    except Exception as e:
        logger.debug(f"Could not count saved 2FA entries: {e}")
        return None

    all_sessions = get_all_sessions()
    if scope == "country":
        all_sessions = {data.get("folder"): all_sessions.get(data.get("folder"), [])}

    return sum(
        1 for phones in all_sessions.values()
        for phone in phones
        if normalize_digits(phone) in saved
    )


async def _ask_password(message: Message, state: FSMContext, subject: str):
    """Ask where the 2FA password should come from before running."""
    await state.set_state(ConvertState.waiting_password)

    saved_count = await _count_saved_2fa(state)
    text = (
        "🔐 <b>2FA Password</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Ready to convert {subject}.\n\n"
        "<b>Where should the 2FA password come from?</b>\n\n"
        "• <b>Manually</b> — you type one password, applied to every account "
        "in this batch\n"
        "• <b>Database</b> — each account uses its own password saved in the "
        "bot; accounts with none get <code>null</code>\n"
        "• <b>No password</b> — force <code>null</code> for every account, "
        "even if a password is saved\n\n"
        "<i>Accounts ending up with no password get <code>null</code> in the "
        "JSON and no 2FA.txt file.</i>"
    )
    await smart_edit(message, text, reply_markup=convert_password_kb(saved_count), parse_mode="HTML")


@router.callback_query(F.data == "cv_pw:yes")
async def cb_convert_pw_yes(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.set_state(ConvertState.waiting_password)
    await state.update_data(awaiting_password_text=True)
    await smart_edit(
        callback.message,
        "🔐 <b>Send the 2FA password</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Type the password now. It will be written to the JSON and to "
        "<code>2FA.txt</code> for every account in this batch.",
        reply_markup=cancel_kb(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cv_pw:db")
async def cb_convert_pw_db(callback: CallbackQuery, state: FSMContext):
    """Take each account's password from the bot's saved 2FA database."""
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.update_data(password=None, use_db=True, awaiting_password_text=False)
    await callback.answer("🗄 Using saved passwords")
    await _run_conversion(callback.message, state)


@router.callback_query(F.data == "cv_pw:no")
async def cb_convert_pw_no(callback: CallbackQuery, state: FSMContext):
    """Force null for every account, ignoring anything saved in the database."""
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await state.update_data(password=None, use_db=False, awaiting_password_text=False)
    await callback.answer()
    await _run_conversion(callback.message, state)


@router.message(ConvertState.waiting_password, F.text)
async def msg_convert_password(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    data = await state.get_data()
    if not data.get("awaiting_password_text"):
        return

    password = (message.text or "").strip()
    if not password:
        await message.answer("❌ Password cannot be empty. Try again.", reply_markup=cancel_kb())
        return

    await state.update_data(password=password, awaiting_password_text=False)

    # Remove the password from the chat — it shouldn't linger in history.
    try:
        await message.delete()
    except Exception:
        pass

    status = await message.answer("✅ Password saved for this batch.", parse_mode="HTML")
    await _run_conversion(status, state)


# ── Batch runner ──────────────────────────────────────────────────────────────

async def _collect_jobs(mode: str, data: dict, workspace: str) -> tuple[list[dict], str | None]:
    """
    Resolve the chosen source into a list of conversion jobs.

    Returns (jobs, error_message). Each job is a dict with the kwargs
    `convert_account` needs beyond mode/out_dir/twofa.
    """
    scope = data.get("scope")
    jobs: list[dict] = []

    if scope == "upload":
        upload_path = data.get("upload_path")
        if not upload_path or not os.path.exists(upload_path):
            return [], "Uploaded file is no longer available. Please start again."

        extract_dir = os.path.join(workspace, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        if upload_path.lower().endswith(".zip"):
            try:
                _safe_extract(upload_path, extract_dir)
            except Exception as e:
                return [], f"Could not extract the ZIP: {e}"
        else:
            shutil.copy2(upload_path, os.path.join(extract_dir, os.path.basename(upload_path)))

        if mode == "tdata2session":
            for tdata_dir in find_tdata_folders(extract_dir):
                jobs.append({"phone": "", "tdata_dir": tdata_dir})
            if not jobs:
                return [], "No <code>tdata</code> folders found in that archive."
        else:
            for root, _dirs, files in os.walk(extract_dir):
                if "__MACOSX" in root:
                    continue
                for f in files:
                    if f.lower().endswith(".session"):
                        phone = phone_from_filename(f)
                        if phone:
                            jobs.append({"phone": phone, "session_path": os.path.join(root, f)})
            if not jobs:
                return [], "No <code>.session</code> files found in that upload."

    else:
        all_sessions = get_all_sessions()
        if scope == "country":
            folder = data.get("folder")
            all_sessions = {folder: all_sessions.get(folder, [])}

        for folder, phones in all_sessions.items():
            for phone in phones:
                jobs.append({
                    "phone": phone,
                    "session_path": os.path.join(SESSIONS_DIR, folder, f"{phone}.session"),
                })
        if not jobs:
            return [], "No sessions found for that selection."

    if len(jobs) > MAX_ACCOUNTS_PER_BATCH:
        jobs = jobs[:MAX_ACCOUNTS_PER_BATCH]

    return jobs, None


def _safe_extract(zip_path: str, dest: str) -> None:
    """Extract a ZIP with path-traversal and zip-bomb protection."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if "__MACOSX" in name:
                continue

            target = os.path.normpath(os.path.join(dest, name))
            if not target.startswith(os.path.normpath(dest) + os.sep):
                logger.warning(f"Skipping path-traversal entry: {name}")
                continue

            os.makedirs(os.path.dirname(target), exist_ok=True)
            written = 0
            with zf.open(member) as src, open(target, "wb") as out:
                while chunk := src.read(8192):
                    written += len(chunk)
                    if written > MAX_EXTRACT_BYTES:
                        raise ValueError(f"'{name}' exceeds the size limit")
                    out.write(chunk)


async def _run_conversion(status: Message, state: FSMContext):
    """Execute the batch, then deliver the result as one or more ZIP files."""
    data = await state.get_data()
    mode = data.get("mode")
    if not mode:
        await smart_edit(status, "❌ Session expired. Please start again.",
                         reply_markup=back_to_convert_kb(), parse_mode="HTML")
        return

    await state.set_state(ConvertState.running)

    workspace = data.get("workspace") or tempfile.mkdtemp(prefix="convert_")
    await state.update_data(workspace=workspace)
    out_dir = os.path.join(workspace, "output")
    os.makedirs(out_dir, exist_ok=True)

    try:
        status = await smart_edit(status, "⏳ <b>Collecting accounts...</b>", parse_mode="HTML")

        jobs, error = await _collect_jobs(mode, data, workspace)
        if error:
            await smart_edit(status, f"❌ {error}", reply_markup=back_to_convert_kb(), parse_mode="HTML")
            return

        batch_password = data.get("password")
        use_db = bool(data.get("use_db"))
        proxy = await get_proxy()

        total = len(jobs)
        succeeded: list[str] = []
        failures: list[tuple[str, str]] = []

        for index, job in enumerate(jobs, start=1):
            phone = normalize_digits(job.get("phone") or "")

            # A manually typed password wins. Otherwise consult the database
            # only when the user explicitly chose that source — "No password"
            # must stay null even for accounts that have one saved.
            twofa = batch_password
            if not twofa and use_db and phone:
                try:
                    twofa = await db.get_2fa(phone)
                except Exception as e:
                    logger.debug(f"2FA lookup failed for {phone}: {e}")
                    twofa = None

            result = await convert_account(
                mode=mode,
                out_base_dir=out_dir,
                phone=phone,
                session_path=job.get("session_path"),
                tdata_dir=job.get("tdata_dir"),
                twofa=twofa,
                proxy_config=proxy,
            )

            if result.ok:
                succeeded.append(result.phone)
            else:
                failures.append((result.phone or phone or "unknown", result.error or "unknown error"))

            if index % PROGRESS_EVERY == 0 or index == total:
                status = await smart_edit(
                    status,
                    f"⏳ <b>Converting...</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{CONVERT_MODES[mode]}\n"
                    f"Progress: <b>{index}/{total}</b>\n"
                    f"✅ {len(succeeded)}   ❌ {len(failures)}",
                    parse_mode="HTML",
                )

            await asyncio.sleep(0.3)  # anti-FloodWait pacing

        if not succeeded:
            detail = "\n".join(f"• <code>{p}</code> — {e}" for p, e in failures[:10])
            await smart_edit(
                status,
                f"❌ <b>Nothing was converted.</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n{detail}",
                reply_markup=back_to_convert_kb(), parse_mode="HTML",
            )
            return

        status = await smart_edit(status, "📦 <b>Packaging results...</b>", parse_mode="HTML")
        archives = _package(out_dir, workspace, mode)

        for i, archive in enumerate(archives, start=1):
            part = f" (part {i}/{len(archives)})" if len(archives) > 1 else ""
            await status.answer_document(
                FSInputFile(archive, filename=os.path.basename(archive)),
                caption=f"✅ <b>{CONVERT_MODES[mode]}</b>{part}",
                parse_mode="HTML",
            )
            await asyncio.sleep(0.3)

        summary = [
            "✅ <b>Conversion Complete</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"{CONVERT_MODES[mode]}",
            f"✅ Converted: <b>{len(succeeded)}</b>",
            f"❌ Failed: <b>{len(failures)}</b>",
        ]
        if failures:
            summary.append("\n<b>Failures:</b>")
            summary += [f"• <code>{p}</code> — {e}" for p, e in failures[:10]]
            if len(failures) > 10:
                summary.append(f"<i>...and {len(failures) - 10} more</i>")

        await status.answer("\n".join(summary), reply_markup=back_to_convert_kb(), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Convert batch failed: {type(e).__name__}: {e}", exc_info=True)
        await smart_edit(status, f"❌ <b>Conversion failed:</b> <code>{type(e).__name__}: {e}</code>",
                         reply_markup=back_to_convert_kb(), parse_mode="HTML")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        await state.clear()


# ── Packaging ─────────────────────────────────────────────────────────────────

def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _package(out_dir: str, workspace: str, mode: str) -> list[str]:
    """
    Zip the per-account bundles, splitting into parts so no archive exceeds
    Telegram's upload limit. Bundles are never split across parts.
    """
    bundles = sorted(
        os.path.join(out_dir, d) for d in os.listdir(out_dir)
        if os.path.isdir(os.path.join(out_dir, d))
    )

    groups: list[list[str]] = []
    current: list[str] = []
    current_size = 0

    for bundle in bundles:
        size = _dir_size(bundle)
        if current and current_size + size > MAX_ZIP_BYTES:
            groups.append(current)
            current, current_size = [], 0
        current.append(bundle)
        current_size += size
    if current:
        groups.append(current)

    archives = []
    for i, group in enumerate(groups, start=1):
        suffix = f"_part{i}" if len(groups) > 1 else ""
        archive = os.path.join(workspace, f"{mode}{suffix}.zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for bundle in group:
                bundle_name = os.path.basename(bundle)
                for root, _dirs, files in os.walk(bundle):
                    for f in files:
                        full = os.path.join(root, f)
                        rel = os.path.join(bundle_name, os.path.relpath(full, bundle))
                        zf.write(full, rel)
        archives.append(archive)

    return archives


# ── Cleanup ───────────────────────────────────────────────────────────────────

async def _cleanup_workspace(state: FSMContext):
    """Remove a leftover workspace when the user backs out mid-flow."""
    data = await state.get_data()
    workspace = data.get("workspace")
    if workspace and os.path.isdir(workspace):
        shutil.rmtree(workspace, ignore_errors=True)
