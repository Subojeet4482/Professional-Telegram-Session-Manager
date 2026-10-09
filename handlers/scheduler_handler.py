"""
Scheduler Handler — Auto-check, daily report, and auto-backup.

Features:
  - Auto-Check:    runs spam/contact check on all sessions every N hours
  - Daily Report:  sends stats summary every day at a set hour
  - Auto-Backup:   sends ZIP of all sessions to admin every N days
  - All settings stored in DB (scheduler table)
  - Admin can configure intervals via bot UI
"""

import asyncio
import logging
import os
import shutil
import time
import tempfile
import zipfile
from datetime import datetime, timezone, timedelta

from aiogram import Router, F, Bot
from utils.utils import smart_edit
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database.database as db
from utils.country_utils import get_all_sessions, get_country_display, get_total_stats, SESSIONS_DIR
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client, check_spam, check_contact_limit
from utils.logout_utils import sweep_all_sessions, process_pending_retries, get_pending
from utils.session_lock import session_lock
from handlers.common import is_admin_async, is_write_admin_async

router = Router()
logger = logging.getLogger("scheduler")

# ── Default settings ───────────────────────────────────────────────────────────
_DEFAULTS = {
    "auto_check_enabled":  False,
    "auto_check_interval": 12,     # hours
    "daily_report_enabled": False,
    "daily_report_hour":   9,      # 0-23 UTC
    "auto_backup_enabled":  False,
    "auto_backup_interval": 7,     # days
    "auto_logout_enabled":  False,
    "auto_logout_interval": 1,     # days (1 / 3 / 7)
    "report_chat_id":       None,  # filled from ADMIN_ID on first run
}


async def _get(key: str):
    return await db.sched_get(key, _DEFAULTS.get(key))


async def _set(key: str, value) -> None:
    await db.sched_set(key, value)


# ══════════════════════════════════════════════════════════════════════════════
#  SCHEDULER MENU
# ══════════════════════════════════════════════════════════════════════════════

def _bool_icon(v) -> str:
    return "✅" if v else "❌"


async def _scheduler_text() -> str:
    ac_en  = await _get("auto_check_enabled")
    ac_hr  = await _get("auto_check_interval")
    dr_en  = await _get("daily_report_enabled")
    dr_hr  = await _get("daily_report_hour")
    bk_en  = await _get("auto_backup_enabled")
    bk_day = await _get("auto_backup_interval")
    lo_en  = await _get("auto_logout_enabled")
    lo_day = await _get("auto_logout_interval")

    return (
        "⏰ <b>Scheduler Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_bool_icon(ac_en)} <b>Auto-Check</b> — every <b>{ac_hr}h</b>\n"
        f"{_bool_icon(dr_en)} <b>Daily Report</b> — at <b>{dr_hr:02d}:00 UTC</b>\n"
        f"{_bool_icon(bk_en)} <b>Auto-Backup</b> — every <b>{bk_day} day(s)</b>\n"
        f"{_bool_icon(lo_en)} <b>Auto Log Out</b> — every <b>{lo_day} day(s)</b>\n\n"
        "Choose a setting to configure:"
    )


def _scheduler_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Auto-Check", callback_data="sched:autocheck")],
        [InlineKeyboardButton(text="📊 Daily Report", callback_data="sched:report")],
        [InlineKeyboardButton(text="💾 Auto-Backup", callback_data="sched:backup")],
        [InlineKeyboardButton(text="🚪 Auto Log Out", callback_data="sched:autologout")],
        [InlineKeyboardButton(text="▶️ Run Check Now", callback_data="sched:run_check")],
        [InlineKeyboardButton(text="📊 Run Report Now", callback_data="sched:run_report")],
        [InlineKeyboardButton(text="💾 Run Backup Now", callback_data="sched:run_backup")],
        [InlineKeyboardButton(text="🚪 Run Logout Now", callback_data="sched:run_logout")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="menu")],
    ])


@router.callback_query(F.data == "scheduler")
async def cb_scheduler(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await smart_edit(callback.message, await _scheduler_text(), reply_markup=_scheduler_kb(), parse_mode="HTML")
    await callback.answer()


# ── Auto-Check sub-menu ────────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:autocheck")
async def cb_sched_autocheck(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    enabled  = await _get("auto_check_enabled")
    interval = await _get("auto_check_interval")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🔴 Disable' if enabled else '🟢 Enable'} Auto-Check",
            callback_data="sched:ac_toggle"
        )],
        [
            InlineKeyboardButton(text="⏱ 6h",  callback_data="sched:ac_hr:6"),
            InlineKeyboardButton(text="⏱ 12h", callback_data="sched:ac_hr:12"),
            InlineKeyboardButton(text="⏱ 24h", callback_data="sched:ac_hr:24"),
        ],
        [InlineKeyboardButton(text="🔙 Back", callback_data="scheduler")],
    ])
    text = (
        f"🔄 <b>Auto-Check</b>\n\n"
        f"Status: <b>{'✅ Enabled' if enabled else '❌ Disabled'}</b>\n"
        f"Interval: <b>{interval}h</b>\n\n"
        "Checks spam + contact status for all sessions automatically."
    )
    await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "sched:ac_toggle")
async def cb_ac_toggle(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    v = await _get("auto_check_enabled")
    await _set("auto_check_enabled", not v)
    await callback.answer("✅ Auto-check " + ("enabled" if not v else "disabled"))
    await cb_sched_autocheck(callback)


@router.callback_query(F.data.startswith("sched:ac_hr:"))
async def cb_ac_interval(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    hours = int(callback.data.split(":")[2])
    await _set("auto_check_interval", hours)
    await callback.answer(f"✅ Interval set to {hours}h")
    await cb_sched_autocheck(callback)


# ── Daily Report sub-menu ──────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:report")
async def cb_sched_report(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    enabled = await _get("daily_report_enabled")
    hour    = await _get("daily_report_hour")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🔴 Disable' if enabled else '🟢 Enable'} Report",
            callback_data="sched:dr_toggle"
        )],
        [
            InlineKeyboardButton(text="🕐 6:00",  callback_data="sched:dr_hr:6"),
            InlineKeyboardButton(text="🕘 9:00",  callback_data="sched:dr_hr:9"),
            InlineKeyboardButton(text="🕛 12:00", callback_data="sched:dr_hr:12"),
            InlineKeyboardButton(text="🕕 18:00", callback_data="sched:dr_hr:18"),
        ],
        [InlineKeyboardButton(text="🔙 Back", callback_data="scheduler")],
    ])
    text = (
        f"📊 <b>Daily Report</b>\n\n"
        f"Status: <b>{'✅ Enabled' if enabled else '❌ Disabled'}</b>\n"
        f"Send time: <b>{hour:02d}:00 UTC</b>\n\n"
        "Sends a daily statistics summary."
    )
    await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "sched:dr_toggle")
async def cb_dr_toggle(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    v = await _get("daily_report_enabled")
    await _set("daily_report_enabled", not v)
    await callback.answer("✅ Daily report " + ("enabled" if not v else "disabled"))
    await cb_sched_report(callback)


@router.callback_query(F.data.startswith("sched:dr_hr:"))
async def cb_dr_hour(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    hour = int(callback.data.split(":")[2])
    await _set("daily_report_hour", hour)
    await callback.answer(f"✅ Report time set to {hour:02d}:00 UTC")
    await cb_sched_report(callback)


# ── Auto-Backup sub-menu ───────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:backup")
async def cb_sched_backup(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    enabled = await _get("auto_backup_enabled")
    days    = await _get("auto_backup_interval")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🔴 Disable' if enabled else '🟢 Enable'} Backup",
            callback_data="sched:bk_toggle"
        )],
        [
            InlineKeyboardButton(text="📅 1d",  callback_data="sched:bk_day:1"),
            InlineKeyboardButton(text="📅 3d",  callback_data="sched:bk_day:3"),
            InlineKeyboardButton(text="📅 7d",  callback_data="sched:bk_day:7"),
            InlineKeyboardButton(text="📅 14d", callback_data="sched:bk_day:14"),
        ],
        [InlineKeyboardButton(text="🔙 Back", callback_data="scheduler")],
    ])
    text = (
        f"💾 <b>Auto-Backup</b>\n\n"
        f"Status: <b>{'✅ Enabled' if enabled else '❌ Disabled'}</b>\n"
        f"Interval: <b>Every {days} day(s)</b>\n\n"
        "Sends a ZIP of all sessions to admin."
    )
    await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "sched:bk_toggle")
async def cb_bk_toggle(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    v = await _get("auto_backup_enabled")
    await _set("auto_backup_enabled", not v)
    await callback.answer("✅ Auto-backup " + ("enabled" if not v else "disabled"))
    await cb_sched_backup(callback)


@router.callback_query(F.data.startswith("sched:bk_day:"))
async def cb_bk_days(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    days = int(callback.data.split(":")[2])
    await _set("auto_backup_interval", days)
    await callback.answer(f"✅ Backup interval set to {days} day(s)")
    await cb_sched_backup(callback)


# ── Auto Log Out sub-menu ──────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:autologout")
async def cb_sched_autologout(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    enabled = await _get("auto_logout_enabled")
    days    = await _get("auto_logout_interval")
    pending = len(await get_pending())

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🔴 Disable' if enabled else '🟢 Enable'} Auto Log Out",
            callback_data="sched:lo_toggle"
        )],
        [
            InlineKeyboardButton(text="📅 24h", callback_data="sched:lo_day:1"),
            InlineKeyboardButton(text="📅 3d",  callback_data="sched:lo_day:3"),
            InlineKeyboardButton(text="📅 7d",  callback_data="sched:lo_day:7"),
        ],
        [InlineKeyboardButton(text="🚪 Run logout now", callback_data="sched:run_logout")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="scheduler")],
    ])

    pending_line = f"\n🔁 Queued retries: <b>{pending}</b>\n" if pending else ""
    text = (
        f"🚪 <b>Auto Log Out</b>\n\n"
        f"Status: <b>{'✅ Enabled' if enabled else '❌ Disabled'}</b>\n"
        f"Interval: <b>Every {days} day(s)</b>\n"
        f"{pending_line}\n"
        "Terminates every session on each account except the bot's own, so only "
        "the bot's session stays active.\n\n"
        "<i>Accounts Telegram still restricts are retried automatically once the "
        "restriction expires.</i>"
    )
    await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "sched:lo_toggle")
async def cb_lo_toggle(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    v = await _get("auto_logout_enabled")
    await _set("auto_logout_enabled", not v)
    await callback.answer("✅ Auto Log Out " + ("enabled" if not v else "disabled"))
    await cb_sched_autologout(callback)


@router.callback_query(F.data.startswith("sched:lo_day:"))
async def cb_lo_days(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    days = int(callback.data.split(":")[2])
    await _set("auto_logout_interval", days)
    await callback.answer(f"✅ Interval set to {days} day(s)")
    await cb_sched_autologout(callback)


# ── Manual run buttons ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:run_check")
async def cb_run_check_now(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer("⏳ Starting auto-check...")
    await smart_edit(callback.message, "⏳ <b>Auto-Check running...</b>", parse_mode="HTML")
    result = await _do_auto_check(callback.bot, callback.from_user.id)
    await smart_edit(callback.message, result, reply_markup=_scheduler_kb(), parse_mode="HTML")


@router.callback_query(F.data == "sched:run_report")
async def cb_run_report_now(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer("📊 Generating report...")
    report = _build_report()
    await callback.message.answer(report, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "sched:run_backup")
async def cb_run_backup_now(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer("💾 Creating backup ZIP...")
    await _do_backup(callback.bot, callback.from_user.id)


@router.callback_query(F.data == "sched:run_logout")
async def cb_run_logout_now(callback: CallbackQuery):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.answer("⏳ Checking for extra sessions...")
    await smart_edit(callback.message, "🚪 <b>Auto Log Out running...</b>", parse_mode="HTML")
    result = await _do_auto_logout(callback.bot, callback.from_user.id)
    await smart_edit(callback.message, result, reply_markup=_scheduler_kb(), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
#  CORE TASK FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _build_report() -> str:
    """Build a statistics text report."""
    all_sessions = get_all_sessions()
    total        = sum(len(v) for v in all_sessions.values())
    now          = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"📊 <b>Daily Report — {now}</b>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
        f"📱 Total Sessions: <b>{total}</b>",
        f"🌍 Countries: <b>{len(all_sessions)}</b>\n",
        "<b>Breakdown by country:</b>",
    ]
    for folder, phones in sorted(all_sessions.items(), key=lambda x: len(x[1]), reverse=True)[:15]:
        flag, name = get_country_display(folder)
        lines.append(f"  {flag} {name}: <b>{len(phones)}</b>")

    if len(all_sessions) > 15:
        lines.append(f"  ... and {len(all_sessions) - 15} more")

    return "\n".join(lines)


# Backoff for repeated auto-backup failures: 5m, 10m, 20m, ... capped at 6h.
# Never waits longer than the configured interval either, so a 1-day interval
# doesn't get stretched by the cap logic.
_BACKUP_BASE_BACKOFF = 300      # seconds
_BACKUP_MAX_BACKOFF  = 21600    # seconds


async def _record_backup_attempt(success: bool) -> None:
    """
    Advance last_backup_ts on every attempt — success or failure — so the
    next due-check in scheduler_loop is always computed from "when we last
    tried", not "when we last succeeded".

    Previously last_backup_ts was only written after a successful send. A
    persistent failure (bad proxy, blocked chat, disk full) meant it never
    advanced at all, so `now_ts >= due_at` stayed true on every 60s tick —
    a retry storm hammering Telegram's API and the logs once a minute,
    forever, until someone noticed the root cause.

    A success now schedules the next attempt a full interval away, same as
    before. A failure schedules a retry after an exponentially growing
    backoff instead of either extreme (instant infinite retry, or silently
    waiting out the full multi-day interval) — it recovers fast from a
    transient blip without storming.
    """
    now = int(time.time())
    bk_days = await _get("auto_backup_interval") or 7
    full_interval = bk_days * 86400

    if success:
        fail_count = 0
        next_in = full_interval
    else:
        fail_count = (await db.sched_get("backup_fail_count", 0) or 0) + 1
        backoff = min(_BACKUP_BASE_BACKOFF * (2 ** (fail_count - 1)), _BACKUP_MAX_BACKOFF)
        next_in = min(backoff, full_interval)

    await db.sched_set("backup_fail_count", fail_count)
    # scheduler_loop computes due_at = last_backup_ts + full_interval, so
    # back-date the anchor by however much less than a full interval we
    # actually want to wait.
    await db.sched_set("last_backup_ts", now - (full_interval - next_in))


async def _do_backup(bot: Bot, chat_id: int) -> None:
    """Create a ZIP of all sessions and send to admin."""
    all_sessions = get_all_sessions()
    if not all_sessions:
        await bot.send_message(chat_id, "📭 No sessions to backup.")
        await _record_backup_attempt(success=True)  # nothing to retry over
        return

    _tmp_dir = tempfile.mkdtemp(prefix="auto_backup_")
    tmp_zip = os.path.join(_tmp_dir, "auto_backup.zip")
    total   = 0
    success = False
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, phones in all_sessions.items():
                for phone in phones:
                    sf = os.path.join(SESSIONS_DIR, folder, f"{phone}.session")
                    if os.path.exists(sf):
                        zf.write(sf, f"{folder}/{phone}.session")
                        total += 1

        if total == 0:
            await bot.send_message(chat_id, "📭 No session files found for backup.")
            success = True  # empty inventory isn't a failure worth retrying sooner
            return

        from aiogram.types import FSInputFile
        now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        doc = FSInputFile(tmp_zip, filename=f"backup_{now}.zip")
        await bot.send_document(
            chat_id, doc,
            caption=f"💾 <b>Auto-Backup</b>\n\n📱 {total} sessions\n🕐 {now} UTC",
            parse_mode="HTML",
        )
        logger.info(f"Backup sent: {total} sessions")
        success = True

    except Exception as e:
        logger.error(f"Backup error: {e}")
        try:
            await bot.send_message(chat_id, f"❌ Backup failed: <code>{e}</code>", parse_mode="HTML")
        except Exception:
            pass
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)
        await _record_backup_attempt(success)


async def _do_auto_logout(bot: Bot, chat_id: int) -> str:
    """
    Terminate every session except the bot's own, across all stored accounts.
    Restricted accounts are queued for an automatic retry. Returns summary text.
    """
    summary = await sweep_all_sessions(bot, chat_id)
    if not summary["total"]:
        return "📭 No sessions to process."

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"✅ <b>Auto Log Out Complete — {now}</b>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
        f"📱 Accounts processed: <b>{summary['total']}</b>",
        f"🚪 Sessions terminated: <b>{summary['terminated']}</b>",
        f"🧹 Accounts cleaned: <b>{summary['cleaned']}</b>",
        f"⭐ Already clean: <b>{summary['clean']}</b>",
    ]
    if summary["restricted"]:
        lines.append(
            f"⏳ Restricted (retry queued): <b>{summary['restricted']}</b>"
        )
    if summary["dead"]:
        lines.append(f"❌ Dead sessions: <b>{summary['dead']}</b>")
    if summary["errors"]:
        lines.append(f"⚠️ Errors: <b>{summary['errors']}</b>")

    return "\n".join(lines)


async def _do_auto_check(bot: Bot, chat_id: int) -> str:
    """Run spam + contact check on all sessions. Returns summary text."""
    all_sessions = get_all_sessions()
    if not all_sessions:
        return "📭 No sessions to check."

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    total = sum(len(v) for v in all_sessions.values())
    spam_counts    = {"FREE": 0, "SPAM": 0, "NEW_REGISTERED": 0, "BANNED": 0, "UNKNOWN": 0}
    contact_counts = {"NoLimit": 0, "Limited": 0, "UNKNOWN": 0}
    dead = 0
    sem  = asyncio.Semaphore(10)

    async def _check_one(folder: str, phone: str):
        nonlocal dead
        session_path = os.path.join(SESSIONS_DIR, folder, phone)
        async with sem:
            # This background sweep touches every stored session on a timer.
            # Without a per-phone lock it can open the same .session SQLite
            # file at the same moment as an admin's on-demand OTP/logout/2FA
            # click, corrupting the auth key. The lock only serializes
            # operations on THIS phone — the other 9 semaphore slots keep
            # running against different phones.
            async with session_lock(phone):
                try:
                    client = create_client(session_path, api_id, api_hash, proxy)
                    await client.connect()
                    if not await client.is_user_authorized():
                        await client.disconnect()
                        dead += 1
                        await db.set_account_status(phone, "died")
                        return
                    spam_s    = await check_spam(client)
                    contact_s = await check_contact_limit(client)
                    await client.disconnect()
                    spam_counts[spam_s]       = spam_counts.get(spam_s, 0) + 1
                    contact_counts[contact_s] = contact_counts.get(contact_s, 0) + 1

                    if spam_s == "BANNED":
                        await db.set_account_status(phone, "died")
                    else:
                        await db.set_account_status(phone, "live")
                    await db.set_spam_status(phone, spam_s)
                    await db.set_contact_status(phone, contact_s)
                except Exception as e:
                    logger.warning(f"Auto-check +{phone}: {e}")
                spam_counts["UNKNOWN"] += 1

    tasks = [
        _check_one(folder, phone)
        for folder, phones in all_sessions.items()
        for phone in phones
    ]
    await asyncio.gather(*tasks)
    await db.sched_set("last_check_ts", int(time.time()))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"✅ <b>Auto-Check Complete — {now}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Total: <b>{total}</b>\n"
        f"❌ Dead: <b>{dead}</b>\n\n"
        f"<b>Spam Status:</b>\n"
        f"  ✅ FREE:           {spam_counts.get('FREE', 0)}\n"
        f"  🚫 SPAM:           {spam_counts.get('SPAM', 0)}\n"
        f"  🆕 NEW_REGISTERED: {spam_counts.get('NEW_REGISTERED', 0)}\n"
        f"  🔴 BANNED:         {spam_counts.get('BANNED', 0)}\n\n"
        f"<b>Contact Status:</b>\n"
        f"  ✅ NoLimit:  {contact_counts.get('NoLimit', 0)}\n"
        f"  ⚠️ Limited:  {contact_counts.get('Limited', 0)}\n"
    )


def _report_due(now_dt: datetime, report_hour: int, last_rpt_ts: float) -> tuple[bool, float]:
    """
    Whether the daily report is due, tracking the most recent *scheduled
    datetime* rather than comparing bare `now_dt.hour >= report_hour`.

    An hour-only comparison breaks across a delayed/restarted loop:
    report_hour=23 with a tick landing at 00:20 the next day sees
    `now_dt.hour` (0) wrap back below 23 and looks like "not due yet",
    silently skipping that entire day's report until hour 23 comes back
    around 24 hours later.

    Instead, find the most recent occurrence of report_hour:00 that is <=
    now — today's if the clock has already passed it, otherwise
    yesterday's — and fire if nothing has been sent for that occurrence yet.
    This has no wraparound: "due" is simply "at or past the last scheduled
    instant, and not already sent for it", independent of where the loop's
    tick times happen to land.

    Returns (is_due, scheduled_ts) so the caller can persist scheduled_ts as
    the new last_report_ts on send.
    """
    scheduled_dt = now_dt.replace(hour=report_hour, minute=0, second=0, microsecond=0)
    if scheduled_dt > now_dt:
        if last_rpt_ts <= 0:
            # Never sent before (feature just enabled) — wait for today's
            # first real occurrence rather than retroactively firing for a
            # "yesterday" that predates activation.
            return False, scheduled_dt.timestamp()
        scheduled_dt -= timedelta(days=1)
    scheduled_ts = scheduled_dt.timestamp()
    return last_rpt_ts < scheduled_ts, scheduled_ts


# ══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND LOOP  (started from bot.py)
# ══════════════════════════════════════════════════════════════════════════════

async def scheduler_loop(bot: Bot) -> None:
    """
    Main scheduler loop — runs forever in background.
    Checks every 60 seconds whether any task is due.
    """
    import os
    admin_id = int(os.getenv("ADMIN_ID", "0"))
    logger.info("⏰ Scheduler loop started (admin_id=%s)", admin_id)

    while True:
        try:
            now_ts = int(time.time())

            # ── Auto-Check ────────────────────────────────────────────────
            if await _get("auto_check_enabled"):
                interval_h  = await _get("auto_check_interval") or 12
                last_check  = await db.sched_get("last_check_ts", 0)
                due_at      = (last_check or 0) + interval_h * 3600
                if now_ts >= due_at:
                    logger.info("⏰ Auto-check triggered")
                    if admin_id:
                        await bot.send_message(admin_id, "⏳ <b>Auto-Check started...</b>", parse_mode="HTML")
                    result = await _do_auto_check(bot, admin_id)
                    if admin_id:
                        await bot.send_message(admin_id, result, parse_mode="HTML")

            # ── Daily Report ──────────────────────────────────────────────
            if await _get("daily_report_enabled") and admin_id:
                report_hour  = await _get("daily_report_hour") or 9
                now_dt       = datetime.now(timezone.utc)
                last_rpt_ts  = await db.sched_get("last_report_ts", 0) or 0
                due, scheduled_ts = _report_due(now_dt, report_hour, last_rpt_ts)
                if due:
                    logger.info("📊 Daily report triggered")
                    await bot.send_message(admin_id, _build_report(), parse_mode="HTML")
                    await db.sched_set("last_report_ts", scheduled_ts)

            # ── Auto-Backup ───────────────────────────────────────────────
            if await _get("auto_backup_enabled") and admin_id:
                bk_days   = await _get("auto_backup_interval") or 7
                last_bk   = await db.sched_get("last_backup_ts", 0)
                bk_due_at = (last_bk or 0) + bk_days * 86400
                if now_ts >= bk_due_at:
                    logger.info("💾 Auto-backup triggered")
                    await _do_backup(bot, admin_id)

            # ── Auto Log Out ──────────────────────────────────────────────
            if await _get("auto_logout_enabled"):
                lo_days   = await _get("auto_logout_interval") or 1
                last_lo   = await db.sched_get("last_logout_ts", 0)
                lo_due_at = (last_lo or 0) + lo_days * 86400
                if now_ts >= lo_due_at:
                    logger.info("🚪 Auto Log Out triggered")
                    result = await _do_auto_logout(bot, admin_id)
                    if admin_id:
                        await bot.send_message(admin_id, result, parse_mode="HTML")

            # ── Pending log-out retries (restriction expired) ─────────────
            await process_pending_retries(bot)

        except Exception as e:
            logger.error("Scheduler error: %s", e)

        await asyncio.sleep(60)  # check every minute
