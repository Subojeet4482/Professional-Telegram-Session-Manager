"""
Telegram Session Manager Bot
=============================
Entry point — loads .env, registers handlers, starts polling.

Technical improvements:
  - SQLiteStorage (persists FSM state across restarts)
  - Database init on startup
  - Rate-limit middleware registered globally
  - Scheduler loop as background task
  - Graceful shutdown on SIGINT / SIGTERM
"""

import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# Load .env before anything else
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
os.environ["BOT_RUNNING_FROM_MAIN"] = "true"

import database.database as db
from handlers import get_all_routers
from middlewares.middleware import RateLimitMiddleware
from handlers.scheduler_handler import scheduler_loop
import uvicorn
from web.app import app as web_app

# Expose FastAPI app at module level so `uvicorn main:app` works on Railway
app = web_app

# Try to import SQLite storage (aiogram-sqlite-storage optional dep)
try:
    from aiogram.fsm.storage.redis import RedisStorage  # type: ignore
    _USE_REDIS = True
except ImportError:
    _USE_REDIS = False

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot")

# Reduce Telethon network noise — reconnections and keep-alive drops are normal
logging.getLogger("telethon.network").setLevel(logging.WARNING)
logging.getLogger("telethon.network.mtprotosender").setLevel(logging.WARNING)
logging.getLogger("telethon.network.connection.connection").setLevel(logging.ERROR)


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_env():
    """Validate required environment variables."""
    token = os.getenv("BOT_TOKEN", "")

    if not token or token == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌  BOT_TOKEN not set in .env file!")
        logger.error("    Get a token from @BotFather on Telegram")
        sys.exit(1)

    # ADMIN_ID is MANDATORY. It used to be optional, and an unset value made
    # every authorization check return True — i.e. the bot handed full admin
    # rights to any Telegram user who found it. Refuse to start instead.
    from handlers.common import require_admin_id_configured, AdminConfigError

    try:
        primary_id = require_admin_id_configured()
    except AdminConfigError as e:
        logger.error("❌  %s", e)
        logger.error("    Find your numeric ID by messaging @userinfobot on Telegram.")
        sys.exit(1)

    logger.info("🛡️  Admin ID: %s", primary_id)
    return token


# ─── Graceful Shutdown ────────────────────────────────────────────────────────

_shutdown_event: asyncio.Event | None = None


def _handle_shutdown_signal():
    logger.info("🛑 Shutdown signal received — stopping gracefully...")
    if _shutdown_event:
        _shutdown_event.set()


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    token = validate_env()

    # Initialise database
    await db.init_db()

    # Seed primary admin from ADMIN_ID env into DB (idempotent)
    try:
        admin_id_str = os.getenv("ADMIN_ID", "0")
        primary_id = int(admin_id_str)
        if primary_id:
            existing = await db.get_admin_ids()
            if primary_id not in existing:
                await db.add_admin(primary_id, "superadmin")
                logger.info(f"✅ Seeded primary admin {primary_id} into DB")
    except (ValueError, TypeError):
        pass

    from aiogram.client.session.aiohttp import AiohttpSession
    from aiohttp import ThreadedResolver

    session = AiohttpSession()
    session._connector_init["resolver"] = ThreadedResolver()

    bot = Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    dp = Dispatcher(storage=MemoryStorage())

    # ── Rate-limit middleware ────────────────────────────────────────────────
    dp.message.middleware(RateLimitMiddleware(max_requests=10, window=5))
    dp.callback_query.middleware(RateLimitMiddleware(max_requests=15, window=5))

    # Register all routers
    for router in get_all_routers():
        dp.include_router(router)

    # ── Global error handler ─────────────────────────────────────────────────
    from aiogram.exceptions import TelegramBadRequest

    @dp.error()
    async def global_error_handler(event, exception):
        if isinstance(exception, TelegramBadRequest):
            if "query is too old" in str(exception).lower():
                logger.debug("Stale callback query expired (normal during long operations)")
                return True  # Suppress — not a real error
            if "message is not modified" in str(exception).lower():
                return True  # Suppress — harmless duplicate edit
        logger.exception("Unhandled error in update handler: %s", exception)
        return True

    logger.info("=" * 55)
    logger.info("  📱 Session Manager Bot — Starting...")
    logger.info("  🔄 Scheduler  | 📊 Daily Reports | 💾 Auto-Backup")
    logger.info("  👥 Multi-Admin | 📋 Action Logs  | 🛡️ Rate-Limit")
    logger.info("  🌐 Web Panel  | Port %s", os.getenv("PANEL_PORT", "8080"))
    logger.info("=" * 55)

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_shutdown_signal)
        except NotImplementedError:
            # Windows does not support loop.add_signal_handler for SIGTERM
            pass

    # Start scheduler as background task
    sched_task = asyncio.create_task(scheduler_loop(bot), name="scheduler")

    # Start web panel server as background task
    panel_port = int(os.getenv("PANEL_PORT", "8080"))

    async def start_web_server():
        config = uvicorn.Config(
            web_app,
            host="0.0.0.0",
            port=panel_port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        await server.serve()

    web_task = asyncio.create_task(start_web_server(), name="web_panel")
    logger.info(f"🌐 Web panel starting on port {panel_port}")

    # Delete webhook & start polling
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        # Graceful cleanup
        logger.info("🛑 Stopping web panel...")
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass

        logger.info("🛑 Stopping scheduler...")
        sched_task.cancel()
        try:
            await sched_task
        except asyncio.CancelledError:
            pass

        logger.info("🛑 Closing database...")
        await db.close_db()

        logger.info("🛑 Closing bot session...")
        await bot.session.close()

        logger.info("👋 Bot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped.")
