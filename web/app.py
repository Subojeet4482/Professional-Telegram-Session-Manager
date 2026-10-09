"""
Web Panel — FastAPI application factory.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import HTTPException

import database.database as db
from web.security import SecurityMiddleware, check_login_rate_limit, _get_real_ip
from web.auth import PanelUser, validate_token_and_login, require_auth, SESSION_COOKIE
from web.csrf import generate_csrf_token, CSRF_COOKIE

logger = logging.getLogger("web")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Session cookie is Secure unless explicitly disabled for local http:// dev.
COOKIE_SECURE = os.getenv("PANEL_COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no")
if not COOKIE_SECURE:
    logger.warning(
        "⚠️  PANEL_COOKIE_SECURE is disabled — the session cookie will be sent "
        "over plaintext HTTP. Use this for local development ONLY."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup/shutdown lifecycle.
    - Initialises DB
    - Starts the Telegram bot + scheduler as background tasks
    - Runs periodic cleanup of expired tokens/sessions
    This allows Railway to run everything via `uvicorn main:app`.
    """
    import sys
    import os
    from dotenv import load_dotenv

    # Load .env (Railway injects env vars anyway, but safe to call)
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    # ADMIN_ID is mandatory — see handlers.common. This path previously
    # swallowed a bad/missing value silently, which combined with the old
    # fail-open checks meant the panel booted with no effective admin gate.
    from handlers.common import require_admin_id_configured

    primary_id = require_admin_id_configured()  # raises -> uvicorn refuses to start

    # Init DB
    await db.init_db()

    # Seed primary admin
    existing = await db.get_admin_ids()
    if primary_id not in existing:
        await db.add_admin(primary_id, "superadmin")
        logger.info("Seeded primary admin %s", primary_id)

    # Start Telegram bot as background task if not running from main.py
    bot_task = None
    if os.getenv("BOT_RUNNING_FROM_MAIN") != "true":
        bot_task = asyncio.create_task(_run_bot(), name="telegram_bot")
    else:
        logger.info("Bot is running from main.py, skipping duplicated bot startup in FastAPI lifespan")

    # Periodic cleanup
    async def cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                await db.cleanup_expired_tokens()
                await db.cleanup_expired_web_sessions()
            except Exception as e:
                logger.error("Cleanup error: %s", e)

    cleanup_task = asyncio.create_task(cleanup_loop())

    yield

    # Shutdown
    logger.info("Shutting down bot and cleanup tasks...")
    if bot_task:
        bot_task.cancel()
    cleanup_task.cancel()
    if bot_task:
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await db.close_db()


async def _run_bot():
    """Run the Telegram bot + scheduler inside the uvicorn event loop."""
    import os
    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.client.default import DefaultBotProperties
    from handlers import get_all_routers
    from middlewares.middleware import RateLimitMiddleware
    from handlers.scheduler_handler import scheduler_loop

    token = os.getenv("BOT_TOKEN", "")
    if not token:
        logger.error("BOT_TOKEN not set — Telegram bot will NOT start")
        return

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(RateLimitMiddleware(max_requests=10, window=5))
    dp.callback_query.middleware(RateLimitMiddleware(max_requests=15, window=5))
    for router in get_all_routers():
        dp.include_router(router)

    sched_task = asyncio.create_task(scheduler_loop(bot), name="scheduler")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🤖 Telegram bot started")
        await dp.start_polling(bot)
    finally:
        sched_task.cancel()
        try:
            await sched_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("🤖 Telegram bot stopped")


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Session Manager Panel",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # Security middleware
    app.add_middleware(SecurityMiddleware)

    # Templates & static files
    templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
    app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

    # ── Custom exception handler for 401 → redirect to login ─────────────
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # API callers always get JSON. Returning an HTML login page to a fetch()
        # caller made role denials surface as an unparseable response instead of
        # a readable "Insufficient privileges" error.
        is_api = request.url.path.startswith("/api/")

        if exc.status_code == 401:
            if is_api:
                return JSONResponse({"error": exc.detail or "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/login", status_code=303)

        if exc.status_code == 403:
            if is_api:
                return JSONResponse({"error": exc.detail or "Access denied"}, status_code=403)
            return templates.TemplateResponse(
                request, "login.html",
                {"token": "", "error": exc.detail or "Access denied"},
                status_code=403,
            )

        return JSONResponse({"error": exc.detail or "Error"}, status_code=exc.status_code)

    # ══════════════════════════════════════════════════════════════════════
    #  AUTH ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        # No `token` query parameter is accepted. Tokens are pasted into the
        # form and travel only in the POST body — never in a URL, where they
        # would land in history, Referer headers and access logs.
        return templates.TemplateResponse(
            request, "login.html", {"error": error},
        )

    @app.post("/login")
    async def do_login(request: Request):
        check_login_rate_limit(_get_real_ip(request))
        form = await request.form()
        token = form.get("token", "").strip()
        if not token:
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Token is required"},
                status_code=400,
            )

        try:
            session_id = await validate_token_and_login(token, request)
        except HTTPException as e:
            return templates.TemplateResponse(
                request, "login.html",
                {"error": e.detail},
                status_code=e.status_code,
            )

        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_id,
            httponly=True,
            # Secure by default: the cookie authenticates every privileged
            # action, so it must never traverse plaintext HTTP. Only set
            # PANEL_COOKIE_SECURE=false for local development over http://.
            secure=COOKIE_SECURE,
            samesite="strict",
            max_age=86400,
        )
        # NOT HttpOnly — app.js reads this to echo it back as X-CSRF-Token.
        # A cross-site page cannot read it (Same-Origin Policy) even though
        # JS on THIS origin can, which is exactly the property CSRF defense
        # needs.
        response.set_cookie(
            key=CSRF_COOKIE,
            value=generate_csrf_token(session_id),
            httponly=False,
            secure=COOKIE_SECURE,
            samesite="strict",
            max_age=86400,
        )
        return response

    @app.get("/logout")
    async def logout(request: Request):
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            try:
                await db.delete_web_session(session_id)
            except Exception:
                pass
        response = RedirectResponse(url="/login", status_code=303)
        # Attributes must match those used when setting it, or some browsers
        # will not clear the cookie.
        response.delete_cookie(SESSION_COOKIE, httponly=True, secure=COOKIE_SECURE, samesite="strict")
        response.delete_cookie(CSRF_COOKIE, httponly=False, secure=COOKIE_SECURE, samesite="strict")
        return response

    # ══════════════════════════════════════════════════════════════════════
    #  ROOT REDIRECT
    # ══════════════════════════════════════════════════════════════════════

    @app.get("/", response_class=RedirectResponse)
    async def root():
        return RedirectResponse(url="/dashboard")

    # ══════════════════════════════════════════════════════════════════════
    #  DASHBOARD
    # ══════════════════════════════════════════════════════════════════════

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request, user: PanelUser = Depends(require_auth)):
        from utils.country_utils import get_all_sessions, get_country_display

        all_sessions = get_all_sessions()
        all_account = await db.get_all_account_statuses()
        all_contact = await db.get_all_contact_statuses()
        all_spam = await db.get_all_spam_statuses()
        total = sum(len(v) for v in all_sessions.values())
        countries = len(all_sessions)

        # Account status counts
        account_counts = {}
        for s in all_account.values():
            account_counts[s] = account_counts.get(s, 0) + 1

        # Spam status counts
        spam_counts = {}
        for p, s in all_spam.items():
            if all_account.get(p) in ("died", "Die", "BANNED", "Dead"):
                s = "DEAD"
            spam_counts[s] = spam_counts.get(s, 0) + 1

        # Contact status counts
        contact_counts = {}
        for s in all_contact.values():
            contact_counts[s] = contact_counts.get(s, 0) + 1

        admins = await db.get_all_admins()
        recent_logs = await db.get_recent_logs(20)

        # Scheduler settings
        sched_info = {
            "auto_check": await db.sched_get("auto_check_enabled", False),
            "daily_report": await db.sched_get("daily_report_enabled", False),
            "auto_backup": await db.sched_get("auto_backup_enabled", False),
        }

        # Country breakdown
        country_data = {}
        for folder, phones in all_sessions.items():
            flag, name = get_country_display(folder)
            country_data[folder] = {
                "flag": flag, "name": name, "count": len(phones),
            }

        return templates.TemplateResponse(request, "dashboard.html", {
            # Exposed so the UI can hide controls the caller cannot use.
            # Server-side dependencies remain the enforcement point.
            "current_user_id": user.user_id,
            "current_role": user.role,
            "is_superadmin": user.is_superadmin,
            "can_write": user.can_write,
            "total_sessions": total,
            "countries": countries,
            "account_counts": account_counts,
            "spam_counts": spam_counts,
            "contact_counts": contact_counts,
            "admins": admins,
            "country_data": country_data,
            "recent_logs": recent_logs,
            "sched_info": sched_info,
        })

    # ══════════════════════════════════════════════════════════════════════
    #  INCLUDE API ROUTERS
    # ══════════════════════════════════════════════════════════════════════

    from web.routes.sessions import router as sessions_router
    from web.routes.admins import router as admins_router
    from web.routes.settings import router as settings_router
    from web.routes.scheduler import router as scheduler_router

    app.include_router(sessions_router)
    app.include_router(admins_router)
    app.include_router(settings_router)
    app.include_router(scheduler_router)

    return app


app = create_app()
