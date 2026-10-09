"""
Stats Chart Handler — Generate matplotlib charts and send as images.
Charts:
  - Pie chart: session distribution by country (top 10 + Others)
  - Bar chart: spam/contact status ratios
  - Bar chart: dead vs alive sessions
"""

import asyncio
import io
import logging
import os
import tempfile
from datetime import datetime, timezone

from aiogram import Router, F
from utils.utils import smart_edit
from aiogram.types import CallbackQuery, BufferedInputFile
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import database.database as db
from utils.country_utils import get_all_sessions, get_country_display
from handlers.common import is_admin_async, is_write_admin_async

router = Router()
logger = logging.getLogger(__name__)


def _try_import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        return None


async def _generate_charts() -> list[tuple[bytes, str]]:
    """Generate all charts with professional styling, return list of (png_bytes, caption)."""
    plt = _try_import_matplotlib()
    if plt is None:
        return []

    # General settings for professional appearance
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 10,
        "figure.facecolor": "#0f172a", # Very dark background (Slate 900)
        "axes.facecolor": "#1e293b",   # Axes background (Slate 800)
        "axes.edgecolor": "#334155",
        "axes.labelcolor": "#94a3b8",
        "xtick.color": "#94a3b8",
        "ytick.color": "#94a3b8",
        "grid.color": "#334155",
        "text.color": "white"
    })

    all_sessions = get_all_sessions()
    all_statuses = await db.get_all_account_statuses()
    all_contact  = await db.get_all_contact_statuses()
    charts = []

    # ── 1. Country distribution (Donut Chart) ──────────────────────────────────
    if all_sessions:
        sorted_countries = sorted(all_sessions.items(), key=lambda x: len(x[1]), reverse=True)
        top10 = sorted_countries[:10]
        others = sum(len(v) for _, v in sorted_countries[10:])

        labels = [get_country_display(f)[1] for f, _ in top10]
        sizes  = [len(v) for _, v in top10]
        if others > 0:
            labels.append("Others")
            sizes.append(others)

        # Vibrant color palette
        colors = ["#38bdf8", "#818cf8", "#c084fc", "#fb7185", "#fb923c", "#fbbf24", "#34d399", "#2dd4bf", "#a78bfa", "#f472b6", "#64748b"]

        fig, ax = plt.subplots(figsize=(10, 8), dpi=200)
        # Draw donut chart
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, autopct="%1.1f%%", colors=colors,
            startangle=140, pctdistance=0.75,
            wedgeprops={'width': 0.4, 'edgecolor': '#0f172a', 'linewidth': 2} 
        )
        
        plt.setp(autotexts, size=9, weight="bold", color="white")
        plt.setp(texts, size=10, color="#cbd5e1")

        total = sum(sizes)
        ax.set_title(f"Sessions by Country\nTotal: {total}", pad=20, fontsize=16, weight="bold", color="white")
        
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches='tight', transparent=False)
        plt.close(fig)
        charts.append((buf.getvalue(), f"Country Distribution — {total} sessions"))

    # ── 2. Spam status (Vertical Bars) ──────────────────────────────────────────
    if all_statuses:
        status_counts = {}
        for s in all_statuses.values():
            status_counts[s] = status_counts.get(s, 0) + 1

        labels2 = list(status_counts.keys())
        values2 = [status_counts[k] for k in labels2]
        bar_colors = {
            "FREE": "#10b981", "SPAM": "#f43f5e", "NEW_REGISTERED": "#0ea5e9",
            "BANNED": "#991b1b", "UNKNOWN": "#64748b"
        }
        colors2 = [bar_colors.get(lbl, "#475569") for lbl in labels2]

        fig2, ax2 = plt.subplots(figsize=(9, 6), dpi=200)
        ax2.grid(axis='y', linestyle='--', alpha=0.3)
        bars = ax2.bar(labels2, values2, color=colors2, width=0.6, zorder=3)
        
        ax2.set_title("Spam Status Breakdown", fontsize=15, weight="bold", pad=15)
        
        # Add values on top of bars
        for bar in bars:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{int(height)}', ha='center', va='bottom', weight='bold', color='white')

        plt.tight_layout()
        buf2 = io.BytesIO()
        fig2.savefig(buf2, format="png", bbox_inches='tight')
        plt.close(fig2)
        charts.append((buf2.getvalue(), "Spam Status Distribution"))

    # ── 3. Alive vs Dead (Comparison Bars) ────────────────────────────────────
    if all_sessions:
        total_sessions = sum(len(v) for v in all_sessions.values())
        dead_count = sum(1 for s in all_statuses.values() if s in ("BANNED", "Dead", "Die"))
        alive_count = total_sessions - dead_count

        labels4 = ["Alive", "Dead/Banned"]
        values4 = [alive_count, dead_count]
        colors4 = ["#22c55e", "#ef4444"]

        fig4, ax4 = plt.subplots(figsize=(7, 5), dpi=200)
        bars4 = ax4.barh(labels4, values4, color=colors4, height=0.5, zorder=3)
        ax4.grid(axis='x', linestyle='--', alpha=0.2)
        
        ax4.set_title("Session Health Overview", fontsize=14, weight="bold")
        
        # Add numbers next to horizontal bars
        for bar in bars4:
            width = bar.get_width()
            ax4.text(width + 0.1, bar.get_y() + bar.get_height()/2,
                    f' {int(width)}', va='center', weight='bold', color='white')

        plt.tight_layout()
        buf4 = io.BytesIO()
        fig4.savefig(buf4, format="png", bbox_inches='tight')
        plt.close(fig4)
        charts.append((buf4.getvalue(), "Session Validity Health-Check"))

    return charts



# ──────────────────────────────────────────────────────────────────────────────
#  HANDLER
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "stats_chart")
async def cb_stats_chart(callback: CallbackQuery):
    if not await is_admin_async(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    plt = _try_import_matplotlib()
    if plt is None:
        await callback.answer(
            "❌ matplotlib not installed. Run: pip install matplotlib",
            show_alert=True,
        )
        return

    await smart_edit(callback.message, "📈 <b>Generating Charts...</b>\n\n⏳ Please wait...",
        parse_mode="HTML",
    )
    await callback.answer()

    try:
        charts = await asyncio.get_event_loop().run_in_executor(
            None, lambda: asyncio.run(_generate_charts())
        )
    except Exception:
        charts = await _generate_charts()

    if not charts:
        await smart_edit(callback.message, "📭 <b>No data available for charts.</b>\n\nRun a check first to collect status data.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back", callback_data="menu")]
            ]),
            parse_mode="HTML",
        )
        return

    await smart_edit(callback.message, f"📈 <b>Sending {len(charts)} chart(s)...</b>",
        parse_mode="HTML",
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for png_bytes, caption in charts:
        photo = BufferedInputFile(png_bytes, filename="chart.png")
        await callback.message.answer_photo(
            photo,
            caption=f"📊 {caption}\n🕐 {now}",
        )
        await asyncio.sleep(0.5)

    await smart_edit(callback.message, f"✅ <b>{len(charts)} chart(s) sent above!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu")]
        ]),
        parse_mode="HTML",
    )
