"""
Convert Utilities — Account format conversion (tdata / session / json / txt).

Pure conversion logic with no aiogram dependency, so it can be driven by the
bot handlers today and the web panel later.

Output layout — one folder per account, named with the '+' prefix:

    +48699585146/
        +48699585146.session
        +48699585146.json
        2FA.txt              (only when a 2FA password is known)
        tdata/

Every function is individually failure-tolerant: a dead or offline account
still produces a bundle, with the profile fields written as null rather than
aborting the batch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

from utils.device_profiles import DESKTOP_APP_VERSION

logger = logging.getLogger(__name__)

# Conversions are capped to keep a single batch bounded, matching the
# limit the import flow already enforces.
MAX_ACCOUNTS_PER_BATCH = 1000

# tdata is only valid against the official Telegram Desktop API pair, so every
# conversion is pinned to it rather than the bot's own configured credentials.
_DESKTOP_SYSTEM = "windows"


class ConvertError(Exception):
    """Raised when a single account cannot be converted, with a user-facing message."""


# ── Phone / path helpers ──────────────────────────────────────────────────────

def normalize_digits(value: str) -> str:
    """Reduce any phone-ish string to bare digits ('+48 699-585-146' -> '48699585146')."""
    return re.sub(r"\D", "", value or "")


def phone_from_filename(filename: str) -> str:
    """Extract the digits-only phone number from a '.session' filename."""
    name = os.path.basename(filename)
    if name.lower().endswith(".session"):
        name = name[:-8]
    return normalize_digits(name)


def bundle_dir_name(phone: str) -> str:
    """Folder name for an account bundle, e.g. '+48699585146'."""
    return "+" + normalize_digits(phone)


def build_bundle_dir(base_dir: str, phone: str) -> str:
    """Create (and return) the per-account folder inside `base_dir`."""
    path = os.path.join(base_dir, bundle_dir_name(phone))
    os.makedirs(path, exist_ok=True)
    return path


# ── Account data ──────────────────────────────────────────────────────────────

@dataclass
class AccountData:
    """Everything needed to render an account's json/txt output."""

    phone: str                       # digits only, no '+'
    api_id: int = 2040
    api_hash: str = "b18441a1ff607e10a989891a5462e627"
    device_model: str = ""
    system_version: str = ""
    app_version: str = ""
    lang_code: str = "en"
    system_lang_code: str = "en-US"

    # Profile fields — only available when the session authorizes successfully.
    user_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    twofa: str | None = None
    connected: bool = False


@dataclass
class BundleResult:
    """Outcome of converting one account."""

    phone: str
    ok: bool
    bundle_dir: str | None = None
    files: list[str] = field(default_factory=list)
    error: str | None = None
    connected: bool = False
    has_2fa: bool = False


# ── opentele / telethon bridges ───────────────────────────────────────────────

def _load_opentele():
    """Import opentele lazily so the bot still starts if it isn't installed."""
    try:
        from opentele.api import API, UseCurrentSession
        from opentele.td import TDesktop
        from opentele.tl import TelegramClient
    except ImportError as e:  # pragma: no cover - depends on install state
        raise ConvertError(
            "The 'opentele' library is not installed. "
            "Run: pip install -r requirements.txt"
        ) from e
    return API, UseCurrentSession, TDesktop, TelegramClient


def build_desktop_api(phone: str):
    """
    Build a Telegram Desktop API profile for an account.

    Seeded with the phone number so the same account always gets the same
    device_model / system_version — re-converting an account twice won't look
    like two different machines to Telegram.
    """
    API, _, _, _ = _load_opentele()
    api = API.TelegramDesktop.Generate(system=_DESKTOP_SYSTEM, unique_id=normalize_digits(phone))
    # opentele pins a years-old Telegram Desktop release; a stale app_version is
    # visible to Telegram and is one of the things that gets clients throttled.
    api.app_version = DESKTOP_APP_VERSION
    return api


def _proxy_tuple(proxy_config: dict | None):
    """Convert the bot's stored proxy dict into a Telethon proxy tuple."""
    if not proxy_config:
        return None
    try:
        from workers.session_worker import get_proxy_tuple
        return get_proxy_tuple(proxy_config)
    except Exception as e:
        logger.warning(f"Proxy config unusable, continuing without proxy: {e}")
        return None


async def _safe_disconnect(client) -> None:
    if client is None:
        return
    try:
        result = client.disconnect()
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        logger.debug(f"Disconnect failed (ignored): {e}")


# ── Conversion: tdata -> session ──────────────────────────────────────────────

async def tdata_to_session(
    tdata_dir: str,
    out_session: str,
    proxy_config: dict | None = None,
) -> tuple[str, AccountData]:
    """
    Convert a tdata folder into a .session file at `out_session`.

    Returns (phone_digits, AccountData). Uses UseCurrentSession so the existing
    authorization key is reused — no new login is performed against the account.
    """
    _, UseCurrentSession, TDesktop, _ = _load_opentele()

    tdesk = TDesktop(tdata_dir)
    if not tdesk.isLoaded():
        raise ConvertError("tdata folder is empty or could not be read.")

    api = build_desktop_api("0")  # replaced below once the phone is known

    client = None
    try:
        client = await tdesk.ToTelethon(
            session=out_session,
            flag=UseCurrentSession,
            api=api,
            proxy=_proxy_tuple(proxy_config),
        )
        await client.connect()

        if not await client.is_user_authorized():
            raise ConvertError("tdata is not authorized (account logged out or banned).")

        me = await client.get_me()
        phone = normalize_digits(getattr(me, "phone", "") or "")
        if not phone:
            raise ConvertError("Could not read the phone number from this tdata.")

        account = AccountData(
            phone=phone,
            api_id=api.api_id,
            api_hash=api.api_hash,
            device_model=api.device_model,
            system_version=api.system_version,
            app_version=api.app_version,
            lang_code=api.lang_code,
            system_lang_code=api.system_lang_code,
            user_id=getattr(me, "id", None),
            username=getattr(me, "username", None) or None,
            first_name=getattr(me, "first_name", None) or None,
            last_name=getattr(me, "last_name", None) or None,
            connected=True,
        )
        return phone, account
    finally:
        await _safe_disconnect(client)


# ── Conversion: session -> tdata ──────────────────────────────────────────────

async def session_to_tdata(
    session_path: str,
    out_tdata_dir: str,
    account: AccountData,
    proxy_config: dict | None = None,
) -> None:
    """
    Convert a .session file into a tdata folder at `out_tdata_dir`.

    `session_path` is the full path including the .session extension.
    Requires the session to authorize — tdata cannot be produced from a dead
    session because the auth key must be exported from a live connection.
    """
    _, UseCurrentSession, _, OTClient = _load_opentele()

    api = build_desktop_api(account.phone)
    base = session_path[:-8] if session_path.lower().endswith(".session") else session_path

    client = None
    try:
        client = OTClient(base, api=api, proxy=_proxy_tuple(proxy_config))
        await client.connect()

        if not await client.is_user_authorized():
            raise ConvertError("Session is not authorized — cannot build tdata.")

        tdesk = await client.ToTDesktop(flag=UseCurrentSession, api=api)
        os.makedirs(out_tdata_dir, exist_ok=True)
        tdesk.SaveTData(out_tdata_dir)
    finally:
        await _safe_disconnect(client)


# ── Collecting account metadata from an existing session ──────────────────────

async def collect_account_data(
    session_path: str,
    phone: str,
    twofa: str | None = None,
    proxy_config: dict | None = None,
    connect: bool = True,
) -> AccountData:
    """
    Build an AccountData for an existing .session file.

    When `connect` is True the session is opened to read the real profile
    (id / username / first_name / last_name). If the session is dead or the
    network is unavailable those fields stay None and are written as null —
    the bundle is still produced.
    """
    phone = normalize_digits(phone)
    api = build_desktop_api(phone)

    account = AccountData(
        phone=phone,
        api_id=api.api_id,
        api_hash=api.api_hash,
        device_model=api.device_model,
        system_version=api.system_version,
        app_version=api.app_version,
        lang_code=api.lang_code,
        system_lang_code=api.system_lang_code,
        twofa=twofa or None,
    )

    if not connect:
        return account

    base = session_path[:-8] if session_path.lower().endswith(".session") else session_path
    client = None
    try:
        from telethon import TelegramClient

        client = TelegramClient(
            base,
            api.api_id,
            api.api_hash,
            proxy=_proxy_tuple(proxy_config),
            device_model=api.device_model,
            system_version=api.system_version,
            app_version=api.app_version,
            lang_code=api.lang_code,
            # Without this Telethon sends "en", which no real client does — and
            # it would contradict the system_lang_code written into the bundle.
            system_lang_code=api.system_lang_code,
        )
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            account.user_id = getattr(me, "id", None)
            account.username = getattr(me, "username", None) or None
            account.first_name = getattr(me, "first_name", None) or None
            account.last_name = getattr(me, "last_name", None) or None
            account.connected = True
            if not account.phone:
                account.phone = normalize_digits(getattr(me, "phone", "") or "")
    except Exception as e:
        logger.info(f"Could not read profile for {phone}, writing nulls: {type(e).__name__}: {e}")
    finally:
        await _safe_disconnect(client)

    return account


# ── Writers: json / txt ───────────────────────────────────────────────────────

def build_json_payload(account: AccountData) -> dict[str, Any]:
    """
    Build the account JSON payload.

    Key order matches the reference format exactly (the 'avatar' field is
    intentionally omitted). Missing 2FA is written as null across all four
    of the password aliases.
    """
    now = int(time.time())
    twofa = account.twofa or None

    return {
        "session_file": account.phone,
        "phone": account.phone,
        "api_id": account.api_id,
        "app_id": account.api_id,
        "api_hash": account.api_hash,
        "app_hash": account.api_hash,
        "sdk": account.system_version,
        "system_version": account.system_version,
        "app_version": account.app_version,
        "device": account.device_model,
        "device_model": account.device_model,
        "lang_pack": "",
        "system_lang_pack": account.system_lang_code,
        "username": account.username,
        "ipv6": False,
        "first_name": account.first_name,
        "last_name": account.last_name,
        "register_time": now,
        "sex": None,
        "last_check_time": now,
        "lang_code": account.lang_code,
        "proxy": None,
        "twoFA": twofa,
        "password": twofa,
        "2FA": twofa,
        "2fa": twofa,
        "block": False,
        "system_lang_code": account.system_lang_code,
        "id": account.user_id,
    }


def write_json(account: AccountData, out_path: str) -> str:
    """Write the account JSON file and return its path."""
    payload = build_json_payload(account)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


def write_2fa_txt(account: AccountData, out_dir: str) -> str | None:
    """
    Write '2FA.txt' containing '2fa <password>'.

    Returns None and writes nothing when the account has no 2FA password —
    absent 2FA must never produce an empty file.
    """
    if not account.twofa:
        return None

    path = os.path.join(out_dir, "2FA.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"2fa {account.twofa}\n")
    return path


# ── Orchestration ─────────────────────────────────────────────────────────────

async def convert_account(
    mode: str,
    out_base_dir: str,
    phone: str,
    session_path: str | None = None,
    tdata_dir: str | None = None,
    twofa: str | None = None,
    proxy_config: dict | None = None,
) -> BundleResult:
    """
    Convert a single account and write its bundle under `out_base_dir`.

    mode:
        'tdata2session' — tdata folder -> .session + .json + 2FA.txt
        'session2tdata' — .session     -> tdata/
        'session2json'  — .session     -> .json
        'session2all'   — .session     -> tdata/ + .json + 2FA.txt

    Every mode also emits the .session file so each bundle is self-contained.
    """
    phone_digits = normalize_digits(phone)
    result = BundleResult(phone=phone_digits, ok=False)
    # Tracked so a partially-written bundle can be removed on failure — the
    # packager ships every folder it finds, so a failed account must leave none.
    partial_bundle: str | None = None

    try:
        if mode == "tdata2session":
            if not tdata_dir:
                raise ConvertError("No tdata folder supplied.")
            # Convert first — the real phone number comes from the tdata itself.
            tmp_session = os.path.join(out_base_dir, f"_tmp_{abs(hash(tdata_dir))}.session")
            real_phone, account = await tdata_to_session(tdata_dir, tmp_session, proxy_config)

            account.twofa = twofa or None
            result.phone = real_phone

            bundle = build_bundle_dir(out_base_dir, real_phone)
            partial_bundle = bundle
            final_session = os.path.join(bundle, f"{bundle_dir_name(real_phone)}.session")
            shutil.move(tmp_session, final_session)
            result.files.append(final_session)

            json_path = os.path.join(bundle, f"{bundle_dir_name(real_phone)}.json")
            write_json(account, json_path)
            result.files.append(json_path)

            if txt := write_2fa_txt(account, bundle):
                result.files.append(txt)

            result.bundle_dir = bundle
            result.connected = account.connected
            result.has_2fa = bool(account.twofa)
            result.ok = True
            return result

        # ── All remaining modes start from a .session file ──
        if not session_path or not os.path.exists(session_path):
            raise ConvertError("Session file not found.")

        # Each mode emits exactly the outputs its name promises.
        want_json = mode in ("session2json", "session2all")
        want_tdata = mode in ("session2tdata", "session2all")
        want_txt = mode == "session2all"
        if not (want_json or want_tdata):
            raise ConvertError(f"Unknown conversion mode '{mode}'.")

        bundle = build_bundle_dir(out_base_dir, phone_digits)
        partial_bundle = bundle
        name = bundle_dir_name(phone_digits)

        # Only the JSON needs live profile fields; skip the connection otherwise.
        account = await collect_account_data(
            session_path, phone_digits, twofa=twofa,
            proxy_config=proxy_config, connect=want_json,
        )

        session_copy = os.path.join(bundle, f"{name}.session")
        shutil.copy2(session_path, session_copy)
        result.files.append(session_copy)

        if want_json:
            json_path = os.path.join(bundle, f"{name}.json")
            write_json(account, json_path)
            result.files.append(json_path)

        if want_txt:
            if txt := write_2fa_txt(account, bundle):
                result.files.append(txt)
                result.has_2fa = True

        if want_tdata:
            tdata_out = os.path.join(bundle, "tdata")
            await session_to_tdata(session_path, tdata_out, account, proxy_config)
            result.files.append(tdata_out)

        result.bundle_dir = bundle
        result.connected = account.connected
        result.ok = True
        partial_bundle = None  # completed — keep it
        return result

    except ConvertError as e:
        result.error = str(e)
        return result
    except Exception as e:
        logger.error(f"Convert failed for {phone_digits} ({mode}): {type(e).__name__}: {e}")
        result.error = f"{type(e).__name__}: {e}"
        return result
    finally:
        # Drop any half-written bundle so the delivered ZIP matches the report.
        if partial_bundle and not result.ok:
            shutil.rmtree(partial_bundle, ignore_errors=True)


# ── tdata discovery ───────────────────────────────────────────────────────────

def find_tdata_folders(root: str) -> list[str]:
    """
    Locate every tdata folder beneath `root`.

    Matches folders literally named 'tdata', plus folders that merely look like
    one (they contain the 'D877F783D5D3EF8C' key file) so archives that were
    zipped from inside the tdata directory still work.
    """
    found: set[str] = set()

    for current, dirs, files in os.walk(root):
        if "__MACOSX" in current:
            continue

        for d in dirs:
            if d.lower() == "tdata":
                found.add(os.path.join(current, d))

        if any("D877F783" in f for f in files) and os.path.basename(current).lower() != "tdata":
            found.add(current)

    return sorted(found)
