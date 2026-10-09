"""
Admin Handler — Multi-admin management.
Supports:
  - Adding / removing admins
  - Roles: 'superadmin' (full control) and 'readonly' (view only)
  - Viewing action logs
  - Rate-limiting middleware
"""

import os
import time
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from utils.utils import smart_edit
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database.database as db
from handlers.common import (
    is_admin_async, log_action, is_write_admin_async,
    is_superadmin_async, get_primary_admin,
)

router = Router()
logger = logging.getLogger(__name__)

# ── Primary admin (from env) always has full rights ───────────────────────────
# Both helpers delegate to handlers.common so there is exactly one
# implementation of "who is the primary admin" and one of "who is a
# superadmin". A second local copy is how these checks drift apart.

_primary_admin = get_primary_admin
_is_superadmin = is_superadmin_async


# ══════════════════════════════════════════════════════════════════════════════
#  ADD-ADMIN FSM
# ══════════════════════════════════════════════════════════════════════════════

class AddAdminState(StatesGroup):
    waiting_for_id = State()
    waiting_for_role = State()


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN MANAGEMENT MENU
# ══════════════════════════════════════════════════════════════════════════════

async def _admin_list_text() -> str:
    admins = await db.get_all_admins()
    primary = _primary_admin()
    lines = [
        "👥 <b>Admin Management</b>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
        f"🔑 <b>Primary Admin:</b> <code>{primary}</code> (superadmin)\n",
    ]
    if admins:
        lines.append("<b>Additional Admins:</b>")
        for a in admins:
            role_icon = "👑" if a["role"] == "superadmin" else "👁"
            ts = datetime.fromtimestamp(a["added_at"], tz=timezone.utc).strftime("%Y-%m-%d")
            lines.append(f"  {role_icon} <code>{a['user_id']}</code> — {a['role']} (added {ts})")
    else:
        lines.append("<i>No additional admins.</i>")
    lines.append("\n<b>Roles:</b>")
    lines.append("  👑 superadmin — full control")
    lines.append("  👁 readonly   — view stats & logs only")
    return "\n".join(lines)


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Admin", callback_data="adm:add")],
        [InlineKeyboardButton(text="➖ Remove Admin", callback_data="adm:remove_list")],
        [InlineKeyboardButton(text="📋 Action Logs", callback_data="adm:logs:0")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="menu")],
    ])


@router.callback_query(F.data == "adm_menu")
async def cb_admin_menu(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫 Not authorized", show_alert=True)
        return
        
    text = await _admin_list_text()
    is_super = await _is_superadmin(callback.from_user.id)
    
    if is_super:
        kb = _admin_menu_kb()
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Action Logs", callback_data="adm:logs:0")],
            [InlineKeyboardButton(text="🔙 Back", callback_data="menu")],
        ])
        
    await smart_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# ── Add Admin ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:add")
async def cb_add_admin(callback: CallbackQuery, state: FSMContext):
    if not await _is_superadmin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await state.set_state(AddAdminState.waiting_for_id)
    await smart_edit(callback.message, "➕ <b>Add Admin</b>\n\n"
        "Send me the <b>Telegram user ID</b> of the new admin:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="adm_menu")]
        ]),
    )
    await callback.answer()


@router.message(AddAdminState.waiting_for_id)
async def msg_add_admin_id(message: Message, state: FSMContext):
    if not await _is_superadmin(message.from_user.id):
        return
    text = message.text.strip() if message.text else ""
    if not text.lstrip("-").isdigit():
        await message.answer("❌ Please send a valid numeric Telegram ID.")
        return
    await state.update_data(new_admin_id=int(text))
    await state.set_state(AddAdminState.waiting_for_role)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 superadmin (full control)", callback_data="adm:role:superadmin")],
        [InlineKeyboardButton(text="👁 readonly (view only)", callback_data="adm:role:readonly")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="adm_menu")],
    ])
    await message.answer(
        f"✅ ID: <code>{text}</code>\n\nChoose role:",
        reply_markup=kb, parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm:role:"), AddAdminState.waiting_for_role)
async def cb_add_admin_role(callback: CallbackQuery, state: FSMContext):
    if not await _is_superadmin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    role = callback.data.split(":")[2]
    data = await state.get_data()
    new_id = data.get("new_admin_id")
    await state.clear()

    if not new_id:
        await callback.answer("❌ No ID stored", show_alert=True)
        return

    await db.add_admin(new_id, role)
    await log_action(callback.from_user.id, "ADD_ADMIN", f"Added {new_id} as {role}")
    logger.info(f"Admin added: {new_id} role={role} by {callback.from_user.id}")

    text = await _admin_list_text()
    await smart_edit(callback.message, f"✅ <b>Admin added!</b>\n<code>{new_id}</code> → {role}\n\n" + text,
        reply_markup=_admin_menu_kb(), parse_mode="HTML",
    )
    await callback.answer()


# ── Remove Admin ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:remove_list")
async def cb_remove_admin_list(callback: CallbackQuery):
    if not await _is_superadmin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    admins = await db.get_all_admins()
    if not admins:
        await callback.answer("📭 No additional admins to remove.", show_alert=True)
        return

    rows = []
    for a in admins:
        rows.append([InlineKeyboardButton(
            text=f"❌ {a['user_id']} ({a['role']})",
            callback_data=f"adm:rm:{a['user_id']}",
        )])
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="adm_menu")])
    await smart_edit(callback.message, "➖ <b>Remove Admin</b>\n\nSelect an admin to remove:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:rm:"))
async def cb_remove_admin(callback: CallbackQuery):
    if not await _is_superadmin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    uid = int(callback.data.split(":")[2])
    await db.remove_admin(uid)
    # Revoke any web panel session/token the removed admin already holds —
    # otherwise they keep working via the browser for up to 24h.
    await db.delete_web_sessions_for_user(uid)
    await db.delete_web_tokens_for_user(uid)
    await log_action(callback.from_user.id, "REMOVE_ADMIN", f"Removed {uid}")
    await callback.answer(f"✅ Admin {uid} removed")
    text = await _admin_list_text()
    await smart_edit(callback.message, text, reply_markup=_admin_menu_kb(), parse_mode="HTML")


# ── Action Logs viewer ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:logs:"))
async def cb_action_logs(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    page = int(callback.data.split(":")[2])
    PER_PAGE = 10
    logs = await db.get_recent_logs(limit=100)  # fetch up to 100 and paginate locally

    total_pages = max(1, (len(logs) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_logs = logs[page * PER_PAGE:(page + 1) * PER_PAGE]

    lines = [
        f"📋 <b>Action Logs</b> — page {page + 1}/{total_pages}",
        "━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    if page_logs:
        for entry in page_logs:
            ts = datetime.fromtimestamp(entry["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            detail = entry.get("detail", "") or ""
            short_detail = detail[:40] + "…" if len(detail) > 40 else detail
            lines.append(
                f"<code>{ts}</code> <b>{entry['action']}</b> by <code>{entry['user_id']}</code>\n"
                f"  ↳ {short_detail}"
            )
    else:
        lines.append("<i>No logs yet.</i>")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"adm:logs:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"adm:logs:{page + 1}"))

    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="adm_menu")])

    await smart_edit(callback.message, "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()
