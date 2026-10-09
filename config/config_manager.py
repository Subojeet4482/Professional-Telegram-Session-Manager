"""
Config Manager — thin async wrapper around database.py.
All functions are now async and delegate to the DB.
Kept as a separate module so existing handler imports don't break.
"""

import database.database as db


# ── proxy ─────────────────────────────────────────────────────────────────────

async def get_proxy() -> dict:
    return await db.get_proxy()


async def set_proxy(proxy: dict) -> None:
    await db.set_proxy(proxy)


# ── api credentials ────────────────────────────────────────────────────────────

async def get_api_credentials() -> tuple[int, str]:
    return await db.get_api_credentials()


async def get_api_platform() -> str:
    return await db.get_api_platform()


async def set_api_platform(platform: str) -> None:
    await db.set_api_platform(platform)


async def set_api_id(api_id: int) -> None:
    await db.set_api_id(api_id)


async def set_api_hash(api_hash: str) -> None:
    await db.set_api_hash(api_hash)


# ── profile settings ───────────────────────────────────────────────────────────

async def get_profile_settings() -> dict:
    return await db.get_profile_settings()


async def set_profile_settings(profile: dict) -> None:
    await db.set_profile_settings(profile)


# ── auto 2fa settings ─────────────────────────────────────────────────────────

async def get_auto_2fa() -> dict:
    return await db.get_auto_2fa()

async def set_auto_2fa(auto_2fa: dict) -> None:
    await db.set_auto_2fa(auto_2fa)


# ── auto log out settings ─────────────────────────────────────────────────────

async def get_auto_logout() -> dict:
    return await db.get_auto_logout()


async def set_auto_logout(auto_logout: dict) -> None:
    await db.set_auto_logout(auto_logout)
