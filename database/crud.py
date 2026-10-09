"""
Database CRUD Operations.
"""
import json
import time
from typing import Any
from sqlalchemy import select, delete, update
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert

from database.engine import SessionLocal
from database.models import (
    Config, PhoneStatus, Admin, ActionLog,
    Setup, Scheduler, WebToken, WebSession, TwoFactor
)
from utils.crypto import encrypt_secret, decrypt_secret, is_encrypted
from utils.device_profiles import CUSTOM, DESKTOP, OFFICIAL_CREDENTIALS

def _encode(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)

def _decode(s: str) -> Any:
    return json.loads(s)

_CONFIG_DEFAULTS: dict[str, Any] = {
    # Which app identity the bot presents. "desktop" ignores the api_id/api_hash
    # below and uses the official Telegram Desktop pair; "custom" uses them
    # verbatim. See utils/device_profiles.py.
    "api_platform": DESKTOP,
    "api_id":   2040,
    "api_hash": "b18441a1ff607e10a989891a5462e627",
    "proxy": {
        "enabled":  False,
        "type":     "socks5",
        "host":     "",
        "port":     0,
        "username": "",
        "password": "",
        "rdns":     True,
    },
    "profile": {
        "auto_username": False,
        "auto_name":     False,
        "auto_photo":    False,
        "auto_bio":      False,
    },
    "auto_2fa": {
        "enabled": False,
        "password": ""
    },
    "auto_logout": {
        "enabled": False,
    },
}

async def cfg_get(key: str) -> Any:
    async with SessionLocal() as session:
        result = await session.execute(select(Config).where(Config.key == key))
        obj = result.scalars().first()
        if obj is None:
            return _CONFIG_DEFAULTS.get(key)
        return _decode(obj.value)

async def cfg_set(key: str, value: Any) -> None:
    async with SessionLocal() as session:
        stmt = sqlite_upsert(Config).values(key=key, value=_encode(value))
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=dict(value=stmt.excluded.value))
        await session.execute(stmt)
        await session.commit()

async def get_proxy() -> dict:
    return await cfg_get("proxy") or _CONFIG_DEFAULTS["proxy"]

async def set_proxy(proxy: dict) -> None:
    await cfg_set("proxy", proxy)

async def get_api_platform() -> str:
    # Anything unrecognised falls back to desktop, which also migrates configs
    # left on the removed "android" platform.
    platform = await cfg_get("api_platform") or _CONFIG_DEFAULTS["api_platform"]
    return platform if platform in (DESKTOP, CUSTOM) else DESKTOP

async def set_api_platform(platform: str) -> None:
    """Switch between the official Desktop identity and custom credentials.

    Selecting the official platform also writes its api_id/api_hash back to
    config, so anything reading those keys directly stays consistent.
    """
    if platform not in (DESKTOP, CUSTOM):
        raise ValueError(f"Unknown API platform: {platform}")

    await cfg_set("api_platform", platform)

    official = OFFICIAL_CREDENTIALS.get(platform)
    if official:
        api_id, api_hash = official
        await cfg_set("api_id", api_id)
        await cfg_set("api_hash", api_hash)

async def get_api_credentials() -> tuple[int, str]:
    """Return the (api_id, api_hash) every client should connect with.

    On the official platform the credentials come from the platform itself
    rather than storage, so a stale api_id left over from an earlier custom
    setup can never be paired with the official Desktop device profile.
    """
    platform = await get_api_platform()
    official = OFFICIAL_CREDENTIALS.get(platform)
    if official:
        return official

    api_id   = await cfg_get("api_id")   or _CONFIG_DEFAULTS["api_id"]
    api_hash = await cfg_get("api_hash") or _CONFIG_DEFAULTS["api_hash"]
    return int(api_id), str(api_hash)

async def set_api_id(api_id: int) -> None:
    """Set a custom api_id — moves the bot off the official platform."""
    await cfg_set("api_id", api_id)
    await cfg_set("api_platform", CUSTOM)

async def set_api_hash(api_hash: str) -> None:
    """Set a custom api_hash — moves the bot off the official platform."""
    await cfg_set("api_hash", api_hash)
    await cfg_set("api_platform", CUSTOM)

async def get_profile_settings() -> dict:
    return await cfg_get("profile") or _CONFIG_DEFAULTS["profile"]

async def set_profile_settings(profile: dict) -> None:
    await cfg_set("profile", profile)

async def get_auto_2fa() -> dict:
    return await cfg_get("auto_2fa") or _CONFIG_DEFAULTS["auto_2fa"]

async def set_auto_2fa(auto_2fa: dict) -> None:
    await cfg_set("auto_2fa", auto_2fa)

async def get_auto_logout() -> dict:
    return await cfg_get("auto_logout") or _CONFIG_DEFAULTS["auto_logout"]

async def set_auto_logout(auto_logout: dict) -> None:
    await cfg_set("auto_logout", auto_logout)


# ACCOUNT STATUS

async def get_all_account_statuses() -> dict[str, str]:
    async with SessionLocal() as session:
        result = await session.execute(select(PhoneStatus))
        return {r.phone: r.account_status for r in result.scalars()}

async def get_account_status(phone: str) -> str:
    async with SessionLocal() as session:
        result = await session.execute(select(PhoneStatus).where(PhoneStatus.phone == phone))
        obj = result.scalars().first()
        return obj.account_status if obj else "Unknown"

async def set_account_status(phone: str, status: str) -> None:
    async with SessionLocal() as session:
        stmt = sqlite_upsert(PhoneStatus).values(phone=phone, account_status=status, updated_at=int(time.time()))
        stmt = stmt.on_conflict_do_update(index_elements=['phone'], set_=dict(account_status=stmt.excluded.account_status, updated_at=stmt.excluded.updated_at))
        await session.execute(stmt)
        await session.commit()

async def bulk_set_account_status(records: list[tuple[str, str]]) -> None:
    now = int(time.time())
    async with SessionLocal() as session:
        for chunk in [records[i:i + 500] for i in range(0, len(records), 500)]:
            values = [{"phone": p, "account_status": s, "updated_at": now} for p, s in chunk]
            stmt = sqlite_upsert(PhoneStatus).values(values)
            stmt = stmt.on_conflict_do_update(index_elements=['phone'], set_=dict(account_status=stmt.excluded.account_status, updated_at=stmt.excluded.updated_at))
            await session.execute(stmt)
        await session.commit()


# CONTACT STATUS

async def get_all_contact_statuses() -> dict[str, str]:
    async with SessionLocal() as session:
        result = await session.execute(select(PhoneStatus))
        return {r.phone: r.contact_status for r in result.scalars()}

async def get_contact_status(phone: str) -> str:
    async with SessionLocal() as session:
        result = await session.execute(select(PhoneStatus).where(PhoneStatus.phone == phone))
        obj = result.scalars().first()
        return obj.contact_status if obj else "Unknown"

async def set_contact_status(phone: str, status: str) -> None:
    async with SessionLocal() as session:
        stmt = sqlite_upsert(PhoneStatus).values(phone=phone, contact_status=status, updated_at=int(time.time()))
        stmt = stmt.on_conflict_do_update(index_elements=['phone'], set_=dict(contact_status=stmt.excluded.contact_status, updated_at=stmt.excluded.updated_at))
        await session.execute(stmt)
        await session.commit()

async def bulk_set_contact_status(records: list[tuple[str, str]]) -> None:
    now = int(time.time())
    async with SessionLocal() as session:
        for chunk in [records[i:i + 500] for i in range(0, len(records), 500)]:
            values = [{"phone": p, "contact_status": s, "updated_at": now} for p, s in chunk]
            stmt = sqlite_upsert(PhoneStatus).values(values)
            stmt = stmt.on_conflict_do_update(index_elements=['phone'], set_=dict(contact_status=stmt.excluded.contact_status, updated_at=stmt.excluded.updated_at))
            await session.execute(stmt)
        await session.commit()


# SPAM STATUS

async def get_all_spam_statuses() -> dict[str, str]:
    async with SessionLocal() as session:
        result = await session.execute(select(PhoneStatus))
        return {r.phone: r.spam_status for r in result.scalars()}

async def get_spam_status(phone: str) -> str:
    async with SessionLocal() as session:
        result = await session.execute(select(PhoneStatus).where(PhoneStatus.phone == phone))
        obj = result.scalars().first()
        return obj.spam_status if obj else "Unknown"

async def set_spam_status(phone: str, status: str) -> None:
    async with SessionLocal() as session:
        stmt = sqlite_upsert(PhoneStatus).values(phone=phone, spam_status=status, updated_at=int(time.time()))
        stmt = stmt.on_conflict_do_update(index_elements=['phone'], set_=dict(spam_status=stmt.excluded.spam_status, updated_at=stmt.excluded.updated_at))
        await session.execute(stmt)
        await session.commit()

async def bulk_set_spam_status(records: list[tuple[str, str]]) -> None:
    now = int(time.time())
    async with SessionLocal() as session:
        for chunk in [records[i:i + 500] for i in range(0, len(records), 500)]:
            values = [{"phone": p, "spam_status": s, "updated_at": now} for p, s in chunk]
            stmt = sqlite_upsert(PhoneStatus).values(values)
            stmt = stmt.on_conflict_do_update(index_elements=['phone'], set_=dict(spam_status=stmt.excluded.spam_status, updated_at=stmt.excluded.updated_at))
            await session.execute(stmt)
        await session.commit()


# DELETE PHONE ENTRY

async def delete_phone_status(phone: str) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(PhoneStatus).where(PhoneStatus.phone == phone))
        await session.commit()


# ADMINS

async def get_all_admins() -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(select(Admin))
        return [{"user_id": r.user_id, "role": r.role, "added_at": r.added_at} for r in result.scalars()]

async def get_admin_ids() -> set[int]:
    async with SessionLocal() as session:
        result = await session.execute(select(Admin.user_id))
        return set(result.scalars().all())

async def add_admin(user_id: int, role: str = "admin") -> None:
    async with SessionLocal() as session:
        stmt = sqlite_upsert(Admin).values(user_id=user_id, role=role)
        stmt = stmt.on_conflict_do_update(index_elements=['user_id'], set_=dict(role=stmt.excluded.role))
        await session.execute(stmt)
        await session.commit()

async def remove_admin(user_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Admin).where(Admin.user_id == user_id))
        await session.commit()

async def is_admin_db(user_id: int) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(select(Admin.user_id).where(Admin.user_id == user_id).limit(1))
        return result.scalars().first() is not None

async def get_admin_role(user_id: int) -> str | None:
    async with SessionLocal() as session:
        result = await session.execute(select(Admin.role).where(Admin.user_id == user_id).limit(1))
        return result.scalars().first()


# ACTION LOG

async def log_action(user_id: int, action: str, detail: str = "") -> None:
    async with SessionLocal() as session:
        session.add(ActionLog(user_id=user_id, action=action, detail=detail, ts=int(time.time())))
        await session.commit()

async def get_recent_logs(limit: int = 50) -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(select(ActionLog).order_by(ActionLog.ts.desc()).limit(limit))
        return [{"user_id": r.user_id, "action": r.action, "detail": r.detail, "ts": r.ts} for r in result.scalars()]


# SCHEDULER SETTINGS

async def sched_get(key: str, default: Any = None) -> Any:
    async with SessionLocal() as session:
        result = await session.execute(select(Scheduler).where(Scheduler.key == key))
        obj = result.scalars().first()
        if obj is None:
            return default
        return _decode(obj.value)

async def sched_set(key: str, value: Any) -> None:
    async with SessionLocal() as session:
        stmt = sqlite_upsert(Scheduler).values(key=key, value=_encode(value))
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=dict(value=stmt.excluded.value))
        await session.execute(stmt)
        await session.commit()


# SETUP

async def get_all_setups() -> dict:
    async with SessionLocal() as session:
        result = await session.execute(select(Setup))
        return {r.id: {"proxy": r.proxy, "password": r.password, "setup_type": r.setup_type} for r in result.scalars()}

async def save_setup(setup_id: str, proxy: str, password: str, setup_type: str) -> None:
    async with SessionLocal() as session:
        stmt = sqlite_upsert(Setup).values(id=setup_id, proxy=proxy, password=password, setup_type=setup_type)
        stmt = stmt.on_conflict_do_update(index_elements=['id'], set_=dict(proxy=stmt.excluded.proxy, password=stmt.excluded.password, setup_type=stmt.excluded.setup_type))
        await session.execute(stmt)
        await session.commit()

async def delete_setup(setup_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Setup).where(Setup.id == setup_id))
        await session.commit()


# WEB TOKENS

async def create_web_token(token: str, expires_at: int, user_id: int) -> None:
    """Create a one-time panel login token bound to the issuing Telegram user."""
    async with SessionLocal() as session:
        session.add(WebToken(
            token=token,
            user_id=int(user_id),
            created_at=int(time.time()),
            expires_at=expires_at,
            used=0,
        ))
        await session.commit()

async def get_web_token(token: str) -> dict | None:
    async with SessionLocal() as session:
        result = await session.execute(select(WebToken).where(WebToken.token == token))
        obj = result.scalars().first()
        if obj:
            return {
                "token": obj.token,
                "user_id": obj.user_id,
                "created_at": obj.created_at,
                "expires_at": obj.expires_at,
                "used": obj.used,
            }
        return None

async def mark_token_used(token: str) -> None:
    async with SessionLocal() as session:
        await session.execute(update(WebToken).where(WebToken.token == token).values(used=1))
        await session.commit()

async def cleanup_expired_tokens() -> None:
    now = int(time.time())
    async with SessionLocal() as session:
        await session.execute(delete(WebToken).where((WebToken.expires_at < now) | (WebToken.used == 1)))
        await session.commit()


# WEB SESSIONS

async def create_web_session(session_id: str, expires_at: int, user_agent: str, user_id: int) -> None:
    """Create a browser session bound to the Telegram user that authenticated."""
    async with SessionLocal() as session:
        session.add(WebSession(
            session_id=session_id,
            user_id=int(user_id),
            created_at=int(time.time()),
            expires_at=expires_at,
            user_agent=user_agent,
        ))
        await session.commit()

async def get_web_session(session_id: str) -> dict | None:
    async with SessionLocal() as session:
        result = await session.execute(select(WebSession).where(WebSession.session_id == session_id))
        obj = result.scalars().first()
        if obj:
            return {
                "session_id": obj.session_id,
                "user_id": obj.user_id,
                "created_at": obj.created_at,
                "expires_at": obj.expires_at,
                "user_agent": obj.user_agent,
            }
        return None

async def delete_web_sessions_for_user(user_id: int) -> int:
    """
    Revoke every active panel session belonging to a user.

    Called when an admin is removed so their browser tab cannot keep operating
    for the remainder of the 24h session lifetime.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            delete(WebSession).where(WebSession.user_id == int(user_id))
        )
        await session.commit()
        return result.rowcount or 0

async def delete_web_tokens_for_user(user_id: int) -> int:
    """Invalidate any unused login tokens issued to a user."""
    async with SessionLocal() as session:
        result = await session.execute(
            delete(WebToken).where(WebToken.user_id == int(user_id))
        )
        await session.commit()
        return result.rowcount or 0

async def delete_web_session(session_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(WebSession).where(WebSession.session_id == session_id))
        await session.commit()

async def cleanup_expired_web_sessions() -> None:
    now = int(time.time())
    async with SessionLocal() as session:
        await session.execute(delete(WebSession).where(WebSession.expires_at < now))
        await session.commit()


# TWO FACTOR

async def get_2fa(phone: str) -> str | None:
    """
    Return the account's 2FA password as PLAINTEXT.

    Stored encrypted (see utils.crypto); decrypted here so admins asking for it
    in the bot or the panel get the real password. Legacy unencrypted rows are
    returned unchanged.
    """
    async with SessionLocal() as session:
        result = await session.execute(select(TwoFactor.password).where(TwoFactor.phone == phone))
        return decrypt_secret(result.scalars().first())

async def get_all_2fa() -> set[str]:
    async with SessionLocal() as session:
        result = await session.execute(select(TwoFactor.phone))
        return set(result.scalars().all())

async def set_2fa(phone: str, password: str) -> None:
    """Store a 2FA password, encrypted at rest."""
    stored = encrypt_secret(password)
    async with SessionLocal() as session:
        stmt = sqlite_upsert(TwoFactor).values(phone=phone, password=stored, updated_at=int(time.time()))
        stmt = stmt.on_conflict_do_update(index_elements=['phone'], set_=dict(password=stmt.excluded.password, updated_at=stmt.excluded.updated_at))
        await session.execute(stmt)
        await session.commit()

async def encrypt_legacy_2fa_rows() -> int:
    """
    One-time migration: encrypt any 2FA passwords still stored as plaintext.

    Idempotent — already-encrypted rows are skipped, so it is safe to run on
    every startup. Returns the number of rows converted.
    """
    converted = 0
    async with SessionLocal() as session:
        result = await session.execute(select(TwoFactor))
        rows = result.scalars().all()
        for row in rows:
            if row.password and not is_encrypted(row.password):
                row.password = encrypt_secret(row.password)
                converted += 1
        if converted:
            await session.commit()
    return converted

async def delete_2fa(phone: str) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(TwoFactor).where(TwoFactor.phone == phone))
        await session.commit()

