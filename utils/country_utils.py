"""
Country Utilities — Detect country from phone number.
Status functions now delegate to database.py (async).
"""

import os
import re

import phonenumbers

import database.database as db
from .countries_data import COUNTRIES
from .phone_utils import flag_emoji, region_name

BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS_DIR   = os.path.join(BASE_DIR, "sessions")

def load_countries() -> dict:
    """Return static countries dict from memory."""
    return COUNTRIES


def detect_country(phone: str) -> tuple[str, str, str]:
    """
    Detect (calling_code, country_name, flag) for a phone number, digits with
    or without a leading '+'.

    Uses `phonenumbers` for accurate, region-aware detection (correctly
    distinguishing shared calling codes like +1 between the US, Canada, and
    Caribbean nations). Falls back to prefix matching against the static
    table for numbers `phonenumbers` can't classify (e.g. legacy/malformed
    entries picked up during bulk import, which aren't strictly validated).
    """
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return "0", "Unknown", "🏳️"

    try:
        parsed = phonenumbers.parse("+" + digits, None)
        region_code = phonenumbers.region_code_for_number(parsed)
        if region_code:
            return str(parsed.country_code), region_name(region_code), flag_emoji(region_code)
    except phonenumbers.NumberParseException:
        pass

    countries    = load_countries()
    sorted_codes = sorted(countries.keys(), key=len, reverse=True)
    for code in sorted_codes:
        if digits.startswith(code):
            info = countries[code]
            return code, info["name"], info["flag"]
    return "0", "Unknown", "🏳️"


def get_session_dir(country_name: str) -> str:
    safe = country_name.replace(" ", "_").replace("/", "_")
    path = os.path.join(SESSIONS_DIR, safe)
    os.makedirs(path, exist_ok=True)
    return path



def get_all_sessions() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not os.path.exists(SESSIONS_DIR):
        return result
    try:
        for entry in os.scandir(SESSIONS_DIR):
            if entry.is_dir():
                sessions = []
                for sub_entry in os.scandir(entry.path):
                    if sub_entry.is_file() and sub_entry.name.endswith(".session"):
                        sessions.append(sub_entry.name[:-8])
                if sessions:
                    result[entry.name] = sorted(sessions)
    except OSError:
        pass
    return dict(sorted(result.items()))


def get_country_display(folder_name: str) -> tuple[str, str]:
    countries = load_countries()
    display   = folder_name.replace("_", " ")
    for _, info in countries.items():
        if info["name"].replace(" ", "_") == folder_name:
            return info["flag"], display
    return "🏳️", display


def get_total_stats() -> tuple[int, int]:
    all_sess = get_all_sessions()
    total    = sum(len(v) for v in all_sess.values())
    return total, len(all_sess)


# --- Account Status - async (DB) ---

async def get_all_account_statuses() -> dict[str, str]:
    return await db.get_all_account_statuses()

async def get_account_status(phone: str) -> str:
    return await db.get_account_status(phone)

async def set_account_status(phone: str, status: str) -> None:
    await db.set_account_status(phone, status)


# --- Contact Status - async (DB) ---

async def get_all_contact_statuses() -> dict[str, str]:
    return await db.get_all_contact_statuses()

async def get_contact_status(phone: str) -> str:
    return await db.get_contact_status(phone)

async def set_contact_status(phone: str, status: str) -> None:
    await db.set_contact_status(phone, status)


# --- Spam Status - async (DB) ---

async def get_all_spam_statuses() -> dict[str, str]:
    return await db.get_all_spam_statuses()

async def get_spam_status(phone: str) -> str:
    return await db.get_spam_status(phone)

async def set_spam_status(phone: str, status: str) -> None:
    await db.set_spam_status(phone, status)

