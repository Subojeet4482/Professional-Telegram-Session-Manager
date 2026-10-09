"""
Broadcast Handler — Send a message via all/country sessions.
Features:
  - Choose all sessions or filter by country
  - Random delay between sends to avoid bans
  - Live progress updates
  - Summary: success / failed
"""
from utils.utils import smart_edit

import asyncio
import logging
import os
import random

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database.database as db
from utils.country_utils import get_all_sessions, get_country_display, SESSIONS_DIR
from config.config_manager import get_proxy, get_api_credentials
from workers.session_worker import create_client
from handlers.common import is_admin_async, log_action, is_write_admin_async

router = Router()
logger = logging.getLogger(__name__)


# ── FSM ────────────────────────────────────────────────────────────────────────

class BroadcastState(StatesGroup):
    choose_target = State()   # all or country
    choose_country = State()  # pick country
    waiting_link = State()    # receive target link
    waiting_message = State() # receive message text


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "broadcast")
async def cb_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    # Check readonly
    admins = await db.get_all_admins()
    uid = callback.from_user.id
    for a in admins:
        if a["user_id"] == uid and a["role"] == "readonly":
            await callback.answer("🚫 Read-only admins cannot broadcast.", show_alert=True)
            return

    all_sessions = get_all_sessions()
    if not all_sessions:
        await callback.answer("📭 No sessions available.", show_alert=True)
        return

    await state.set_state(BroadcastState.choose_target)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 All Sessions", callback_data="bc:all")],
        [InlineKeyboardButton(text="🌍 Choose Country", callback_data="bc:country")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")],
    ])
    total = sum(len(v) for v in all_sessions.values())
    await smart_edit(callback.message, f"💬 <b>Broadcast Message</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Total sessions: <b>{total}</b>\n\n"
        "Choose target:",
        reply_markup=kb, parse_mode="HTML",
    )
    await callback.answer()


# ── Target: ALL ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "bc:all", BroadcastState.choose_target)
async def cb_bc_all(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    await state.update_data(target="all", target_folder=None)
    await _ask_for_link(callback, state)


# ── Target: Country ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "bc:country", BroadcastState.choose_target)
async def cb_bc_country_list(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    all_sessions = get_all_sessions()
    await state.set_state(BroadcastState.choose_country)
    rows = []
    for folder, phones in sorted(all_sessions.items(), key=lambda x: len(x[1]), reverse=True):
        flag, name = get_country_display(folder)
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({len(phones)})",
            callback_data=f"bc:c:{folder[:40]}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")])
    await smart_edit(callback.message, "🌍 <b>Choose Country</b>\n\nSelect a country to broadcast to:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bc:c:"), BroadcastState.choose_country)
async def cb_bc_country_selected(callback: CallbackQuery, state: FSMContext):
    if not await is_write_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return
    folder = callback.data[5:]
    await state.update_data(target="country", target_folder=folder)
    await _ask_for_link(callback, state)


# ── Ask for Link ──────────────────────────────────────────────────────────────

async def _ask_for_link(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastState.waiting_link)
    data = await state.get_data()
    target = data.get("target")
    folder = data.get("target_folder")
    if target == "all":
        target_label = "All Sessions"
    else:
        flag, name = get_country_display(folder)
        target_label = f"{flag} {name}"
        
    await smart_edit(callback.message, f"🔗 <b>Broadcast → {target_label}</b>\n\n"
        "Send the <b>target link</b> or <b>username</b> where the message should be sent.\n\n"
        "<i>Examples:</i>\n"
        "• <code>@username</code>\n"
        "• <code>https://t.me/channel_name</code>\n"
        "• <code>https://t.me/joinchat/...</code>\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()

@router.message(BroadcastState.waiting_link)
async def msg_broadcast_link(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    target_link = message.text.strip()
    if not target_link:
        await message.answer("❌ Please send a valid link or username.")
        return

    await state.update_data(target_link=target_link)
    await state.set_state(BroadcastState.waiting_message)
    
    await message.answer(f"💬 <b>Target:</b> {target_link}\n\n"
        "Now send me the <b>message text</b> to broadcast.\n\n"
        "⚠️ A random delay of <b>3–8 seconds</b> will be added between sends.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ]),
        parse_mode="HTML",
    )

# ── Receive message and run broadcast ─────────────────────────────────────────

@router.message(BroadcastState.waiting_message)
async def msg_broadcast_text(message: Message, state: FSMContext):
    if not await is_write_admin_async(message.from_user.id):
        return

    text_to_send = message.text or message.caption or ""
    if not text_to_send.strip():
        await message.answer("❌ Please send a non-empty text message.")
        return

    data = await state.get_data()
    await state.clear()
    target = data.get("target", "all")
    folder_filter = data.get("target_folder")

    all_sessions = get_all_sessions()
    if target == "country" and folder_filter:
        sessions_to_use = {folder_filter: all_sessions.get(folder_filter, [])}
    else:
        sessions_to_use = all_sessions

    total_phones = sum(len(v) for v in sessions_to_use.values())
    if total_phones == 0:
        await message.answer("📭 No sessions found for selected target.")
        return

    status_msg = await message.answer(
        f"📡 <b>Broadcasting...</b>\n\n⏳ Processing <b>0/{total_phones}</b>...",
        parse_mode="HTML",
    )

    api_id, api_hash = await get_api_credentials()
    proxy = await get_proxy()

    success = 0
    failed = 0
    processed = 0

    target_link = data.get("target_link", "me")
    
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest

    for folder, phones in sessions_to_use.items():
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

                # Try joining if it's a joinchat link
                if "joinchat/" in target_link or "+" in target_link.split("/")[-1]:
                    hash_part = target_link.split("/")[-1].replace("+", "").replace("joinchat/", "")
                    try:
                        await client(ImportChatInviteRequest(hash_part))
                    except Exception as e:
                        pass # Might already be a member
                else:
                    # For normal usernames/channels, attempt to join first to be safe
                    # Only do this if it's not a direct user (JoinChannelRequest fails on users, but it's harmless)
                    try:
                        await client(JoinChannelRequest(target_link))
                    except Exception:
                        pass

                await client.send_message(target_link, text_to_send)
                await client.disconnect()
                success += 1
                logger.info(f"Broadcast sent via +{phone} to {target_link}")
            except Exception as e:
                failed += 1
                logger.error(f"Broadcast failed +{phone}: {e}")
                if client:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

            # Random delay to avoid bans
            await asyncio.sleep(random.uniform(3, 8))

            if processed % 5 == 0 or processed == total_phones:
                try:
                    status_msg = await smart_edit(status_msg, f"📡 <b>Broadcasting...</b>\n\n"
                        f"⏳ Processing <b>{processed}/{total_phones}</b>...\n"
                        f"✅ Success: {success}  ❌ Failed: {failed}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    await log_action(
        message.from_user.id, "BROADCAST",
        f"target={target} folder={folder_filter} total={total_phones} success={success} failed={failed}"
    )

    status_msg = await smart_edit(status_msg, f"✅ <b>Broadcast Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Total: <b>{total_phones}</b>\n"
        f"✅ Success: <b>{success}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu")]
        ]),
    )
