"""
Database Engine Configuration.
"""
import logging
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from database.models import Base

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(BASE_DIR, "bot.db")

logger = logging.getLogger("database")

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Columns added after the original schema shipped. SQLite's create_all() only
# creates missing TABLES — it never adds a column to a table that already
# exists — so an existing bot.db needs an explicit ALTER.
#   (table, column, column definition)
_ADDED_COLUMNS = (
    ("web_tokens",   "user_id", "INTEGER NOT NULL DEFAULT 0"),
    ("web_sessions", "user_id", "INTEGER NOT NULL DEFAULT 0"),
)


async def _apply_migrations(conn) -> None:
    """Idempotently add columns introduced after the initial schema."""
    for table, column, ddl in _ADDED_COLUMNS:
        rows = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        existing = {row[1] for row in rows}
        if not existing:
            continue  # table not created yet; create_all() will build it correctly
        if column not in existing:
            await conn.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
            )
            logger.info("🔧 Migration: added %s.%s", table, column)


async def init_db() -> None:
    from sqlalchemy import select
    async with engine.begin() as conn:
        await conn.execute(select(1)) # Just a ping
        await conn.run_sync(Base.metadata.create_all)
        await _apply_migrations(conn)

    # Encrypt any 2FA passwords still stored in plaintext. Imported late to
    # avoid a circular import (crud imports this module).
    from database.crud import encrypt_legacy_2fa_rows

    converted = await encrypt_legacy_2fa_rows()
    if converted:
        logger.info("🔐 Encrypted %d legacy plaintext 2FA password(s)", converted)

    logger.info("✅ Database initialised at %s", DB_PATH)

async def close_db() -> None:
    await engine.dispose()
