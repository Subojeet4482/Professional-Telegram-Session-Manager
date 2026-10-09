"""
Keyboards — All bot keyboards defined here.
Includes Reply Keyboard (persistent bottom menu) and Inline Keyboards.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Reply Keyboard (Persistent Bottom Menu)
# ═══════════════════════════════════════════════════════════════════════════════

# Button text constants — used for both keyboard creation and message filtering
BTN_SESSION_GEN = "➕ New Session"
BTN_ADVANCED_GEN = "⚙️ Advanced Gen"
BTN_CHECK = "🔍 Check Status"
BTN_READ_OTP = "📨 Read OTPs"
BTN_SESSIONS = "📂 Manage Sessions"
BTN_EXPORT_ALL = "📤 Export All"
BTN_IMPORT = "📥 Import Sessions"
BTN_CONVERT = "🔄 Convert"
BTN_STATISTICS = "📊 System Stats"
BTN_MAIN_MENU = "🛠️ Admin Tools"

ALL_REPLY_BUTTONS = [
    BTN_SESSION_GEN, BTN_ADVANCED_GEN, BTN_CHECK, BTN_READ_OTP,
    BTN_SESSIONS, BTN_EXPORT_ALL, BTN_IMPORT, BTN_CONVERT, BTN_STATISTICS,
    BTN_MAIN_MENU,
]


def main_reply_kb() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard that stays at the bottom of the chat."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SESSION_GEN), KeyboardButton(text=BTN_ADVANCED_GEN)],
            [KeyboardButton(text=BTN_CHECK), KeyboardButton(text=BTN_READ_OTP)],
            [KeyboardButton(text=BTN_SESSIONS), KeyboardButton(text=BTN_EXPORT_ALL)],
            [KeyboardButton(text=BTN_IMPORT), KeyboardButton(text=BTN_CONVERT)],
            [KeyboardButton(text=BTN_STATISTICS), KeyboardButton(text=BTN_MAIN_MENU)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Inline Keyboards — Main Menu & Admin Tools
# ═══════════════════════════════════════════════════════════════════════════════

def main_menu_kb() -> InlineKeyboardMarkup:
    """Compact admin tools menu — core actions are in Reply Keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Profile Defaults", callback_data="prf_apply"),
            InlineKeyboardButton(text="⏰ Scheduled Tasks", callback_data="scheduler"),
        ],
        [
            InlineKeyboardButton(text="👥 Manage Admins", callback_data="adm_menu"),
            InlineKeyboardButton(text="📈 Analytics Charts", callback_data="stats_chart"),
        ],
        [
            InlineKeyboardButton(text="🌐 Web Panel", callback_data="panel"),
            InlineKeyboardButton(text="⚙️ Global Settings", callback_data="set"),
        ],
    ])


def import_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Import Standard", callback_data="imp_standard")],
        [InlineKeyboardButton(text="❌ Close Menu", callback_data="cancel")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Convert Keyboards
# ═══════════════════════════════════════════════════════════════════════════════

CONVERT_MODES: dict[str, str] = {
    "tdata2session": "📦 TData → Session",
    "session2tdata": "🖥 Session → TData",
    "session2json":  "🧾 Session → JSON",
    "session2all":   "🎁 Session → All (TData + JSON + 2FA)",
}


def convert_menu_kb() -> InlineKeyboardMarkup:
    """Top-level Convert menu — one row per conversion direction."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"cv_mode:{mode}")]
        for mode, label in CONVERT_MODES.items()
    ]
    rows.append([InlineKeyboardButton(text="❌ Close Menu", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def convert_source_kb(mode: str) -> InlineKeyboardMarkup:
    """
    Choose what to convert: an uploaded file, every stored session, or one country.

    TData → Session is upload-only: the bot stores no tdata folders, so
    country selection would have nothing to offer.
    """
    rows = [[InlineKeyboardButton(text="📤 Upload File", callback_data=f"cv_up:{mode}")]]
    if mode != "tdata2session":
        rows.append([InlineKeyboardButton(text="📋 All Countries", callback_data=f"cv_all:{mode}")])
        rows.append([InlineKeyboardButton(text="🌍 Pick Country", callback_data=f"cv_country:{mode}:0")])
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="cv_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def convert_countries_kb(mode: str, countries: dict, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Paginated country picker for Convert. countries: {folder: (flag, name, count)}"""
    items = list(countries.items())
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    page_items = items[page * per_page: page * per_page + per_page]

    rows = []
    for folder, (flag, name, count) in page_items:
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({count})",
            callback_data=f"cv_c:{mode}:{folder[:40]}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"cv_country:{mode}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"cv_country:{mode}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"cv_mode:{mode}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def convert_password_kb(saved_count: int | None = None) -> InlineKeyboardMarkup:
    """
    Ask where the 2FA password should come from.

    Three distinct sources — a typed password, the bot's saved database, or
    none at all. `saved_count` annotates the database button with how many of
    the selected accounts already have a stored password.
    """
    db_label = "🗄 Use saved passwords (database)"
    if saved_count is not None:
        db_label += f" — {saved_count}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Enter a password manually", callback_data="cv_pw:yes")],
        [InlineKeyboardButton(text=db_label, callback_data="cv_pw:db")],
        [InlineKeyboardButton(text="🚫 No password at all", callback_data="cv_pw:no")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="cv_menu")],
    ])


def back_to_convert_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Convert", callback_data="cv_menu")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Sessions Keyboards
# ═══════════════════════════════════════════════════════════════════════════════

def sessions_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌍 Browse by Country", callback_data="sl:0"),
            InlineKeyboardButton(text="🔍 Search Phone", callback_data="search"),
        ],
        [
            InlineKeyboardButton(text="📤 Export Everything", callback_data="se"),
            InlineKeyboardButton(text="📊 Export by Status", callback_data="ses_exp_status"),
        ],
        [
            InlineKeyboardButton(text="🔐 Bulk 2FA Setup", callback_data="bulk2fa"),
            InlineKeyboardButton(text="🧹 Clean Invalid Folders", callback_data="clean_all"),
        ],
        [
            InlineKeyboardButton(text="🚪 Log Out Everyone", callback_data="loa"),
            InlineKeyboardButton(text="🗑 Delete Dead Sessions", callback_data="del_dead"),
        ],
        [InlineKeyboardButton(text="❌ Close Menu", callback_data="cancel")],
    ])


def countries_list_kb(countries: dict[str, int], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """
    Build paginated country list.
    countries: {folder_name: (flag, display_name, count)}
    """
    items = list(countries.items())
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    rows = []
    for folder, (flag, name, count) in page_items:
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({count})",
            callback_data=f"sc:{folder[:50]}",
        )])

    # Pagination row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"sl:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"sl:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="ses")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_STATUS_ICONS: dict[str, str] = {
    "FREE":             "🟢",
    "SPAM":             "🟡",
    "BANNED":           "🔴",
    "NEW_REGISTERED":   "🔵",
    "died":             "🔴",
    "Die":              "🔴",
    "Dead":             "🔴",
    "live":             "🟢",
    "Live":             "🟢",
}


def country_sessions_kb(
    folder: str,
    phones: list[str],
    page: int = 0,
    per_page: int = 5,
    statuses: dict[str, str] | None = None,
) -> InlineKeyboardMarkup:
    """Show phones for a country with delete/logout buttons (paginated)."""
    total_pages = max(1, (len(phones) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_phones = phones[start:end]

    rows = []
    for phone in page_phones:
        status = statuses.get(phone, "") if statuses else ""
        icon = _STATUS_ICONS.get(status, "📱")
        rows.append([
            InlineKeyboardButton(text=f"{icon} +{phone}", callback_data=f"sp:{phone}"),
        ])

    # Pagination row (3 buttons: Previous, Page Info, Next)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"scp:{folder[:40]}:{page - 1}"))
    else:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data="noop"))
    nav.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"scp:{folder[:40]}:{page + 1}"))
    else:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data="noop"))
    rows.append(nav)

    rows.append([
        InlineKeyboardButton(text="🚪 Log Out All", callback_data=f"loac:{folder[:40]}"),
        InlineKeyboardButton(text="🗑 Delete All", callback_data=f"dac:{folder[:40]}"),
    ])
    rows.append([
        InlineKeyboardButton(text="🧹 Clean Country", callback_data=f"clean_country:{folder[:40]}"),
    ])
    rows.append([
        InlineKeyboardButton(text="📨 Batch OTP", callback_data=f"batch_otp:{folder[:45]}"),
    ])
    rows.append([
        InlineKeyboardButton(text="📦 Export Country", callback_data=f"sec:{folder[:50]}"),
        InlineKeyboardButton(text="✂️ Split", callback_data=f"spl:{folder[:45]}"),
    ])
    rows.append([
        InlineKeyboardButton(text="📦 Export by Status", callback_data=f"c_es:{folder[:40]}"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="sl:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_DEAD_STATUSES = ("died", "Die", "BANNED", "Dead")


def phone_detail_kb(phone: str, folder: str, account_status: str | None = None, has_2fa: bool = False) -> InlineKeyboardMarkup:
    """Show detail view for a single phone with log out, delete, get otp, and back."""
    is_dead = account_status in _DEAD_STATUSES
    rows = []
    if not is_dead:
        rows.append([InlineKeyboardButton(text="📨 Get OTP", callback_data=f"otp:{phone}")])
    
    mid_row = []
    if has_2fa:
        mid_row.append(InlineKeyboardButton(text="🔑 2FA", callback_data=f"manage2fa:{phone}"))
    mid_row.append(InlineKeyboardButton(text="📋 Other Sessions", callback_data=f"other_sess:{phone}"))
    rows.append(mid_row)
    action_row = []
    if not is_dead:
        action_row.append(InlineKeyboardButton(text="🚪 Log Out", callback_data=f"lo:{phone}"))
    action_row.append(InlineKeyboardButton(text="🗑 Delete", callback_data=f"sd:{phone}"))
    rows.append(action_row)
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"sc:{folder[:50]}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def manage_2fa_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 View 2FA", callback_data=f"view2fa:{phone}")],
        [InlineKeyboardButton(text="➕ Set 2FA", callback_data=f"set2fa:{phone}")],
        [InlineKeyboardButton(text="🔄 Update 2FA", callback_data=f"update2fa:{phone}")],
        [InlineKeyboardButton(text="🗑 Delete 2FA", callback_data=f"delete2fa:{phone}")],
        [InlineKeyboardButton(text="🔙 Back to Session", callback_data=f"sp:{phone}")],
    ])


def import_session_kb(phone: str, account_status: str | None = None) -> InlineKeyboardMarkup:
    """Keyboard for specifically imported floating sessions."""
    is_dead = account_status in _DEAD_STATUSES
    rows = []
    if not is_dead:
        rows.append([InlineKeyboardButton(text="📨 Get Code", callback_data=f"imp_otp:{phone}")])
    if not is_dead:
        rows.append([InlineKeyboardButton(text="🚪 Log Out", callback_data=f"imp_lo:{phone}")])
    rows.append([InlineKeyboardButton(text="🗑 Delete", callback_data=f"imp_del:{phone}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def country_export_status_kb(folder: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Export FREE", callback_data=f"ce:FREE:{folder[:40]}"),
            InlineKeyboardButton(text="✂️ Split", callback_data=f"spls:FREE:{folder[:30]}"),
        ],
        [
            InlineKeyboardButton(text="🟡 Export SPAM", callback_data=f"ce:SPAM:{folder[:40]}"),
            InlineKeyboardButton(text="✂️ Split", callback_data=f"spls:SPAM:{folder[:30]}"),
        ],
        [
            InlineKeyboardButton(text="🔴 Export BANNED", callback_data=f"ce:BANNED:{folder[:40]}"),
            InlineKeyboardButton(text="✂️ Split", callback_data=f"spls:BANNED:{folder[:30]}"),
        ],
        [
            InlineKeyboardButton(text="🔵 Export NEW_REGISTERED", callback_data=f"ce:NEW_REGISTERED:{folder[:40]}"),
            InlineKeyboardButton(text="✂️ Split", callback_data=f"spls:NEW_REGISTERED:{folder[:20]}"),
        ],
        [
            InlineKeyboardButton(text="✅ Contact NoLimit", callback_data=f"ce_c:NoLimit:{folder[:35]}"),
            InlineKeyboardButton(text="⚠️ Contact Limited", callback_data=f"ce_c:Limited:{folder[:35]}"),
        ],
        [InlineKeyboardButton(text="🔙 Back", callback_data=f"sc:{folder[:40]}")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Search & OTP
# ═══════════════════════════════════════════════════════════════════════════════

def search_result_kb(phone: str, folder: str, account_status: str | None = None) -> InlineKeyboardMarkup:
    """Post-search: actions for a found phone."""
    is_dead = account_status in _DEAD_STATUSES
    rows = []
    if not is_dead:
        rows.append([InlineKeyboardButton(text="📨 Get OTP", callback_data=f"otp:{phone}")])
    action_row = []
    if not is_dead:
        action_row.append(InlineKeyboardButton(text="🚪 Log Out", callback_data=f"lo:{phone}"))
    action_row.append(InlineKeyboardButton(text="🗑 Delete", callback_data=f"sd:{phone}"))
    rows.append(action_row)
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="ses")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def otp_result_kb(phone: str, found: bool, account_status: str | None = None) -> InlineKeyboardMarkup:
    """OTP result: retry, back, and optionally keep/logout."""
    is_dead = account_status in _DEAD_STATUSES
    rows = []
    if found:
        rows.append([InlineKeyboardButton(text="🔄 Fetch Again", callback_data=f"otp:{phone}")])
        rows.append([InlineKeyboardButton(text="📋 Other Sessions", callback_data=f"other_sess:{phone}")])
        action_row = [InlineKeyboardButton(text="✅ Keep Session", callback_data=f"otp_keep:{phone}")]
        if not is_dead:
            action_row.append(InlineKeyboardButton(text="🚪 Log Out Session", callback_data=f"otp_lo:{phone}"))
        rows.append(action_row)
    else:
        rows.append([InlineKeyboardButton(text="🔄 Fetch Again", callback_data=f"otp:{phone}")])
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="ses")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Read OTP (Quick Access)
# ═══════════════════════════════════════════════════════════════════════════════

def read_otp_countries_kb(countries: dict, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Paginated country list for Read OTP quick access."""
    items = list(countries.items())
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    rows = []
    for folder, (flag, name, count) in page_items:
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({count})",
            callback_data=f"rotp_c:{folder[:45]}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"rotp_p:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"rotp_p:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔍 Search by Phone", callback_data="rotp_search")])
    rows.append([InlineKeyboardButton(text="❌ Close Menu", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def read_otp_phones_kb(folder: str, phones: list[str], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Paginated phone list for Read OTP — click a phone to read OTP instantly."""
    total_pages = max(1, (len(phones) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_phones = phones[start:end]

    rows = []
    for phone in page_phones:
        rows.append([InlineKeyboardButton(
            text=f"📨 +{phone}",
            callback_data=f"otp:{phone}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"rotp_cp:{folder[:35]}:{page - 1}"))
    else:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data="noop"))
    nav.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"rotp_cp:{folder[:35]}:{page + 1}"))
    else:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data="noop"))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="rotp_p:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Advanced Gen
# ═══════════════════════════════════════════════════════════════════════════════

def advanced_gen_menu_kb(options: dict) -> InlineKeyboardMarkup:
    """Advanced gen settings before registration."""
    auto2fa_icon = "✅" if options.get("auto_2fa") else "❌"
    profile_icon = "✅" if options.get("auto_profile") else "❌"
    spam_icon = "✅" if options.get("auto_check") else "❌"
    logout_icon = "✅" if options.get("auto_logout") else "❌"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{auto2fa_icon} Auto 2FA",
            callback_data="advgen_toggle:auto_2fa",
        )],
        [InlineKeyboardButton(
            text=f"{profile_icon} Auto Profile",
            callback_data="advgen_toggle:auto_profile",
        )],
        [InlineKeyboardButton(
            text=f"{spam_icon} Auto Spam Check",
            callback_data="advgen_toggle:auto_check",
        )],
        [InlineKeyboardButton(
            text=f"{logout_icon} Auto Log Out",
            callback_data="advgen_toggle:auto_logout",
        )],
        [InlineKeyboardButton(
            text="📱 Start Registration",
            callback_data="advgen_start",
        )],
        [InlineKeyboardButton(text="❌ Close Menu", callback_data="cancel")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Bulk Action Choice
# ═══════════════════════════════════════════════════════════════════════════════

def bulk_action_choice_kb(action: str, folder: str) -> InlineKeyboardMarkup:
    """Choose between all numbers or specific numbers for log out / delete."""
    # action: "lo" (log out) or "da" (delete)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 All Numbers", callback_data=f"bulk_all_{action}:{folder[:35]}")],
        [InlineKeyboardButton(text="📝 Specific Numbers", callback_data=f"bulk_sp_{action}:{folder[:35]}")],
        [InlineKeyboardButton(text="🔙 Cancel", callback_data=f"sc:{folder[:50]}")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Confirm / Delete
# ═══════════════════════════════════════════════════════════════════════════════

def confirm_delete_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, Delete", callback_data=f"sdc:{phone}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="ses"),
        ],
    ])


def confirm_logout_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, Log Out", callback_data=f"loc:{phone}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="ses"),
        ],
    ])


def confirm_logout_all_kb(step: int = 1) -> InlineKeyboardMarkup:
    """3-step confirmation for global log out all."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"✅ Yes, Confirm ({step}/3)", callback_data=f"loag:{step}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="ses"),
        ],
    ])


def confirm_logout_all_country_kb(folder: str, step: int = 1) -> InlineKeyboardMarkup:
    """3-step confirmation for per-country log out all."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"✅ Yes, Confirm ({step}/3)", callback_data=f"loacc:{folder[:35]}:{step}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"sc:{folder[:50]}"),
        ],
    ])


def confirm_delete_all_country_kb(folder: str, step: int = 1) -> InlineKeyboardMarkup:
    """3-step confirmation for per-country delete all."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"✅ Yes, Confirm ({step}/3)", callback_data=f"dacc:{folder[:35]}:{step}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"sc:{folder[:50]}"),
        ],
    ])


def export_status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Export FREE", callback_data="exp_status:FREE")],
        [InlineKeyboardButton(text="🟡 Export SPAM", callback_data="exp_status:SPAM")],
        [InlineKeyboardButton(text="🔴 Export BANNED", callback_data="exp_status:BANNED")],
        [InlineKeyboardButton(text="🔵 Export NEW_REGISTERED", callback_data="exp_status:NEW_REGISTERED")],
        [
            InlineKeyboardButton(text="✅ Contact NoLimit", callback_data="exp_cstatus:NoLimit"),
            InlineKeyboardButton(text="⚠️ Contact Limited", callback_data="exp_cstatus:Limited"),
        ],
        [InlineKeyboardButton(text="🔙 Back", callback_data="ses")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Settings
# ═══════════════════════════════════════════════════════════════════════════════

def settings_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Proxy Settings", callback_data="prx")],
        [InlineKeyboardButton(text="🔑 API Settings", callback_data="api")],
        [InlineKeyboardButton(text="👤 Profile", callback_data="profile")],
        [InlineKeyboardButton(text="🔐 Auto 2FA", callback_data="auto2fa")],
        [InlineKeyboardButton(text="🚪 Auto Log Out", callback_data="autologout")],
        [InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu")],
    ])


def proxy_kb(is_enabled: bool = True) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Disable Proxy" if is_enabled else "🟢 Enable Proxy"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data="ptgl")],
        [InlineKeyboardButton(text="✏️ Change Proxy", callback_data="pe")],
        [InlineKeyboardButton(text="🧪 Test Connection", callback_data="pt")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="set")],
    ])


def api_kb(platform: str = "desktop") -> InlineKeyboardMarkup:
    """API settings keyboard. The platform button restores the official Telegram
    Desktop identity — api_id, api_hash and the generated device fingerprint."""
    on_official = platform == "desktop"
    label = "✅ Telegram Desktop (2040)" if on_official else "🖥 Restore Desktop (2040)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="apl_desktop")],
        [InlineKeyboardButton(text="✏️ Change API ID", callback_data="ai")],
        [InlineKeyboardButton(text="✏️ Change API Hash", callback_data="ah")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="set")],
    ])


def profile_kb(p: dict) -> InlineKeyboardMarkup:
    """Profile auto-fill settings keyboard showing current status for each option."""
    u_icon = "✅" if p.get("auto_username") else "❌"
    n_icon = "✅" if p.get("auto_name") else "❌"
    ph_icon = "✅" if p.get("auto_photo") else "❌"
    b_icon = "✅" if p.get("auto_bio") else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{u_icon} Username", callback_data="prf_username")],
        [InlineKeyboardButton(text=f"{n_icon} Name", callback_data="prf_name")],
        [InlineKeyboardButton(text=f"{ph_icon} Profile Photo", callback_data="prf_photo")],
        [InlineKeyboardButton(text=f"{b_icon} Bio", callback_data="prf_bio")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="set")],
    ])


def auto_2fa_kb(a2fa: dict) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Disable Auto 2FA" if a2fa.get("enabled") else "🟢 Enable Auto 2FA"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data="a2fa_toggle")],
        [InlineKeyboardButton(text="✏️ Change Password", callback_data="a2fa_edit")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="set")],
    ])


def auto_logout_kb(alo: dict) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Disable Auto Log Out" if alo.get("enabled") else "🟢 Enable Auto Log Out"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data="alo_toggle")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="set")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Common
# ═══════════════════════════════════════════════════════════════════════════════

def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Close", callback_data="cancel")],
    ])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Check
# ═══════════════════════════════════════════════════════════════════════════════

def check_menu_kb() -> InlineKeyboardMarkup:
    """Menu with 3 check types + check all."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Comprehensive Check (All)", callback_data="chk_all")],
        [InlineKeyboardButton(text="🚫 Spam & Restrictions", callback_data="chk_type:spam")],
        [InlineKeyboardButton(text="📇 Contact Limit Status", callback_data="chk_type:contact")],
        [InlineKeyboardButton(text="✅ Alive Session Check", callback_data="chk_type:alive")],
        [InlineKeyboardButton(text="❌ Close Menu", callback_data="cancel")],
    ])


def check_countries_list_kb(countries: dict[str, int], check_type: str, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """
    Build paginated country list for check feature.
    countries: {folder_name: (flag, display_name, count)}
    check_type: 'spam', 'contact', or 'alive'
    """
    items = list(countries.items())
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    rows = []
    for folder, (flag, name, count) in page_items:
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({count})",
            callback_data=f"chkc:{check_type}:{folder[:40]}",
        )])

    # Pagination row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"chkl:{check_type}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"chkl:{check_type}:{page + 1}"))
    if nav:
        rows.append(nav)

    # Check all countries for this specific type
    rows.append([InlineKeyboardButton(text="✅ Check All Countries", callback_data=f"chk_run_all:{check_type}")])
    # Upload ZIP button
    rows.append([InlineKeyboardButton(text="📤 Upload ZIP to Check", callback_data=f"chk_zip:{check_type}")])
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="chk")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_check_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Check Menu", callback_data="chk")],
    ])


def check_all_menu_kb() -> InlineKeyboardMarkup:
    """Sub-menu for 'Check All Types': choose target."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Select Country", callback_data="chk_all_p:0")],
        [InlineKeyboardButton(text="📤 Upload ZIP to Check", callback_data="chk_all_zip")],
        [InlineKeyboardButton(text="✅ Check All Countries", callback_data="chk_all_run")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="chk")],
    ])


def check_all_countries_kb(countries: dict[str, tuple], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Paginated country list for 'Check All Types'."""
    items = list(countries.items())
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    rows = []
    for folder, (flag, name, count) in page_items:
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({count})",
            callback_data=f"chk_all_c:{folder[:45]}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"chk_all_p:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"chk_all_p:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="chk_all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Profile Apply
# ═══════════════════════════════════════════════════════════════════════════════

def profile_apply_menu_kb() -> InlineKeyboardMarkup:
    """Menu to choose apply profile target: ZIP file or a country."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Upload ZIP", callback_data="prf_zip")],
        [InlineKeyboardButton(text="🌍 Apply to Country", callback_data="prf_country:0")],
        [InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu")],
    ])


def profile_apply_countries_kb(countries: dict, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Paginated country list for profile apply."""
    items = list(countries.items())
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    rows = []
    for folder, (flag, name, count) in page_items:
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({count})",
            callback_data=f"prf_c:{folder[:50]}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"prf_country:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"prf_country:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="prf_apply")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_profile_apply_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Profile Apply", callback_data="prf_apply")],
    ])

# ═══════════════════════════════════════════════════════════════════════════════
# Bulk 2FA
# ═══════════════════════════════════════════════════════════════════════════════

def bulk_2fa_action_kb() -> InlineKeyboardMarkup:
    """Menu to choose bulk 2FA action: Set, Update, Delete."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Set 2FA", callback_data="b2fa_action:set")],
        [InlineKeyboardButton(text="🔄 Update 2FA", callback_data="b2fa_action:update")],
        [InlineKeyboardButton(text="🗑 Delete 2FA", callback_data="b2fa_action:delete")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="ses")],
    ])

def bulk_2fa_menu_kb(action: str) -> InlineKeyboardMarkup:
    """Menu to choose apply 2FA target: ZIP file, All files, or a country."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Upload ZIP", callback_data=f"b2fa_zip:{action}")],
        [InlineKeyboardButton(text="📋 All Sessions", callback_data=f"b2fa_all:{action}")],
        [InlineKeyboardButton(text="🌍 Apply to Country", callback_data=f"b2fa_country:{action}:0")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="bulk2fa")],
    ])

def bulk_2fa_countries_kb(action: str, countries: dict, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Paginated country list for Bulk 2FA apply."""
    items = list(countries.items())
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    rows = []
    for folder, (flag, name, count) in page_items:
        rows.append([InlineKeyboardButton(
            text=f"{flag} {name} ({count})",
            callback_data=f"b2fa_c:{action}:{folder[:40]}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"b2fa_country:{action}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"b2fa_country:{action}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"b2fa_action:{action}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_to_bulk_2fa_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Bulk 2FA", callback_data="bulk2fa")],
    ])

def skip_fallback_kb(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Skip", callback_data=f"b2fa_skip:{phone}")],
        [InlineKeyboardButton(text="❌ Cancel Entire Process", callback_data="ses")]
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Delete Dead Confirm
# ═══════════════════════════════════════════════════════════════════════════════

def confirm_delete_dead_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, Delete All Dead", callback_data="del_dead_confirm"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="ses"),
        ],
    ])
