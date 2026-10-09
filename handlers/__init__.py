"""
Handlers package — registers all routers.
"""

from aiogram import Router

from .start import router as start_router
from .register import router as register_router
from .sessions import router as sessions_router
from .settings import router as settings_router
from .import_handler import router as import_router
from .convert_handler import router as convert_router
from .check_handler import router as check_router
from .profile_apply_handler import router as profile_apply_router
from .scheduler_handler import router as scheduler_router
from .admin_handler import router as admin_router
from .stats_chart_handler import router as stats_chart_router
from .panel_handler import router as panel_router
from .bulk_2fa_handler import router as bulk_2fa_router


def get_all_routers() -> list[Router]:
    """Return all handler routers."""
    return [
        start_router,
        register_router,
        sessions_router,
        settings_router,
        import_router,
        convert_router,
        check_router,
        profile_apply_router,
        scheduler_router,
        admin_router,
        stats_chart_router,
        panel_router,
        bulk_2fa_router,
    ]

