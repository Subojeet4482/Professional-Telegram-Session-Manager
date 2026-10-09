<div align="center">

<h1>🛡️ Telegram Session Manager Pro</h1>

<p>
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/aiogram-3.4%2B-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" alt="aiogram"/>
  <img src="https://img.shields.io/badge/FastAPI-0.110%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/Telethon-1.34%2B-FF6B35?style=for-the-badge" alt="Telethon"/>
  <img src="https://img.shields.io/badge/SQLAlchemy-2.0%2B-D71F00?style=for-the-badge" alt="SQLAlchemy"/>
  <img src="https://img.shields.io/badge/License-MIT-22C55E?style=for-the-badge" alt="License"/>
  <img src="https://img.shields.io/badge/Status-Active-brightgreen?style=for-the-badge" alt="Status"/>
</p>

<p><strong>A production-grade, fully asynchronous Telegram session management platform.<br/>Built with aiogram 3.x, Telethon, and FastAPI — engineered for reliability at scale.</strong></p>

</div>

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Web Panel](#web-panel)
- [Project Structure](#project-structure)
- [Database Schema](#database-schema)
- [Multi-Admin System](#multi-admin-system)
- [Scheduler & Automation](#scheduler--automation)
- [Security](#security)
- [Disclaimer](#disclaimer)
- [License](#license)
- [Developer](#developer)

---

## Overview

**Telegram Session Manager Pro** is a comprehensive, fully asynchronous backend system for managing Telegram accounts at scale. It combines a powerful Telegram bot interface (built on `aiogram 3.x` and `Telethon`) with an integrated web dashboard (built on `FastAPI`) to give you complete control over hundreds of sessions from a single platform.

The system handles the complete session lifecycle: registration with OTP, automated 2FA setup, spam checking, country-based organization, bulk operations, export/import, and scheduled maintenance — all persisted in a SQLite database via SQLAlchemy.

---

## Key Features

### Session Management
| Feature | Description |
|---------|-------------|
| **Session Registration** | Add new Telegram accounts via an interactive OTP flow directly inside the bot |
| **Advanced Registration** | Automatically set 2FA passwords, apply profile pictures, and run spam checks during the registration flow |
| **Country Organization** | Sessions are automatically organized into country-based folders for easy browsing and filtering |
| **Search & Filter** | Search sessions by phone number, country, or status across your entire collection |
| **TData Import** | Import existing Telegram desktop data (`TData`) folders and convert them to `.session` files |
| **Session Export** | Export individual sessions or batches as `.session` files or compressed `.zip` archives |

### Account Operations
| Feature | Description |
|---------|-------------|
| **Status Checking** | Run comprehensive checks via SpamBot to detect spam flags, contact limits, and account validity |
| **OTP Reader** | Read incoming OTP codes (from Telegram, WhatsApp, SMS services) from any active session in real time |
| **Bulk 2FA Management** | Set, update, or remove Two-Factor Authentication passwords across hundreds of sessions simultaneously |
| **Profile Automation** | Apply names, bios, and profile pictures to sessions in bulk |

### System & Administration
| Feature | Description |
|---------|-------------|
| **Multi-Admin Access** | Delegate bot access to secondary admins with granular `read-only` or `read-write` permission levels |
| **Action Logging** | Every admin action is timestamped and stored in the database for full auditability |
| **Scheduled Tasks** | Configure automated daily reports, session status checks, and database backups |
| **Visual Statistics** | Real-time charts and analytics showing session health, country distribution, spam rates, and contact limits |
| **Proxy Support** | Route all Telethon connections through a configurable SOCKS5 proxy |
| **Rate Limiting** | Built-in per-user rate limiting middleware on all bot messages and callback queries |
| **Web Dashboard** | Secure FastAPI web panel with token-based authentication for managing everything via browser |
| **Graceful Shutdown** | Handles `SIGINT`/`SIGTERM` signals cleanly — closes database connections, cancels tasks, and shuts down the web server without data loss |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram Bot (aiogram 3.x)            │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│   │ Handlers │  │Middlewares│  │Keyboards │             │
│   └──────────┘  └──────────┘  └──────────┘             │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  Core Services Layer                      │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│   │ Telethon │  │Scheduler │  │  Workers │             │
│   │ (MTProto)│  │  Loop    │  │ (Async)  │             │
│   └──────────┘  └──────────┘  └──────────┘             │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              Data & Persistence Layer                     │
│   ┌──────────────────────────────────────────┐          │
│   │  SQLAlchemy 2.0 + aiosqlite (Async ORM)  │          │
│   │  Tables: config, admins, phone_status,   │          │
│   │          action_log, scheduler,          │          │
│   │          web_tokens, web_sessions, 2fa   │          │
│   └──────────────────────────────────────────┘          │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              Web Panel (FastAPI + Jinja2)                 │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│   │Dashboard │  │ Sessions │  │ Settings │             │
│   │  Stats   │  │  API     │  │  Admin   │             │
│   └──────────┘  └──────────┘  └──────────┘             │
└─────────────────────────────────────────────────────────┘
```

The bot and web panel run **concurrently in the same async event loop**. When launched via `python main.py`, the bot starts polling while `uvicorn` serves the FastAPI application on a configurable port — no separate processes needed.

---

## Requirements

### System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **Python** | 3.10 | 3.11+ |
| **OS** | Windows / Linux / macOS | Ubuntu 22.04 LTS |
| **RAM** | 512 MB | 1 GB+ |
| **Disk Space** | 300 MB | 1 GB+ (depends on session count) |
| **Internet** | Stable connection | Stable connection |

### Telegram Requirements

| Item | Where to Get |
|------|-------------|
| **Bot Token** | Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |
| **Your Telegram ID** | Message [@userinfobot](https://t.me/userinfobot) to get your numeric ID |
| **API ID & Hash** | Log in at [my.telegram.org](https://my.telegram.org) → API Development Tools |

> **Note:** The API ID and Hash can also be configured dynamically through the bot's settings menu after startup — you do not need to set them in `.env` from the beginning.

### Python Dependencies

All dependencies are pinned in `requirements.txt`. Key packages:

```
aiogram>=3.4.0          # Telegram Bot framework (async)
telethon>=1.34.0        # Telegram MTProto client
fastapi>=0.110.0        # Web panel framework
uvicorn[standard]>=0.27.0  # ASGI server
SQLAlchemy>=2.0.28      # Async ORM
aiosqlite>=0.19.0       # SQLite async driver
python-dotenv>=1.0.0    # Environment variable loader
Pillow>=10.0.0          # Image processing for profile photos
matplotlib>=3.8.0       # Chart generation for statistics
opentele>=1.15.1        # TData import support
python-socks[asyncio]>=2.4.0  # SOCKS5 proxy support
jinja2>=3.1.3           # Web panel templates
python-multipart>=0.0.9 # Form data parsing
greenlet>=3.0.3         # SQLAlchemy async bridge
```

---

## Installation

### Option A — Docker (Recommended)

The fastest way to run the bot. Requires only [Docker](https://docs.docker.com/get-docker/) and Docker Compose.

```bash
git clone https://github.com/OmarMoh12/Professional-Telegram-Session-Manager-Bot.git
cd Professional-Telegram-Session-Manager-Bot
./run.sh
```

`run.sh` interactively asks for your `BOT_TOKEN`, `ADMIN_ID`, and the web panel port, writes `.env`, and then builds and starts the bot + web panel with `docker compose up -d --build`. Re-running it later detects an existing `.env` and offers to reuse it.

The stack persists `bot.db`, `sessions/`, `temp_sessions/`, and the auto-generated encryption keys (`.2fa_key`, `.csrf_key`) on the host via bind mounts, so data survives container rebuilds.

```bash
docker compose logs -f   # follow logs
docker compose down      # stop
```

### Option B — Manual (Python)

### Step 1 — Clone the Repository

```bash
git clone https://github.com/OmarMoh12/Professional-Telegram-Session-Manager-Bot.git
cd Professional-Telegram-Session-Manager-Bot
```

### Step 2 — Create a Virtual Environment (Recommended)

```bash
# Linux / macOS
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### Step 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Configure Environment Variables

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Open `.env` and set the required fields:

```env
BOT_TOKEN=1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi
ADMIN_ID=123456789
```

> See the [Configuration](#configuration) section for all available options.

### Step 5 — Run the Bot

```bash
python main.py
```

---

## Configuration

All configuration is managed through the `.env` file in the project root.

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `BOT_TOKEN` | Your Telegram bot token from @BotFather | `123456:ABC-DEF...` |
| `ADMIN_ID` | Your Telegram numeric user ID (primary superadmin) | `123456789` |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PANEL_PORT` | `8080` | Port for the FastAPI web panel |

### Dynamic Configuration (via Bot Settings Menu)

The following settings are configured interactively through the bot and stored in the database — no `.env` changes needed:

- **API Platform** — use the official **Telegram Desktop** identity (`api_id 2040`), or supply your own credentials
- **API ID & API Hash** — Telegram application credentials from my.telegram.org (setting either one switches the platform to *Custom*)
- **Proxy Settings** — SOCKS5 proxy host, port, username, and password
- **Auto 2FA** — Default 2FA password to apply during session registration
- **Profile Settings** — Default first name, last name, and bio for new sessions
- **Scheduler** — Enable/disable automated daily reports, status checks, and backups

### Device Identity (`utils/device_profiles.py`)

Telegram decides how — and whether — to deliver a login code partly from the
`InitConnection` payload the client sends. Every client the bot creates gets a
full, coherent identity from this module instead of Telethon's defaults:

| Field | Value sent (`api_id 2040`) |
|-------|---------------------------|
| `device_model` | SMBIOS system identity, e.g. `Dell Inc. OptiPlex 7090` — the same thing Telegram Desktop reads from the BIOS, not the hostname |
| `system_version` | `Windows 10` / `Windows 11`, matched to hardware that could actually run it |
| `app_version` | `DESKTOP_APP_VERSION` |
| `system_lang_code` | `en-US` (Telethon's default `en` is region-less and no real client sends it) |
| `lang_pack` | `tdesktop` (Telethon hardcodes `""`) |

Each account's fingerprint is derived from its **phone number**, so it stays
identical across every reconnect rather than being re-rolled on each check,
broadcast or 2FA pass.

Two maintenance rules:

- **Bump `DESKTOP_APP_VERSION`** when Telegram Desktop releases. A version far
  behind the live one is the most common reason code delivery degrades.
- **The device pool is append-only.** Reordering or deleting an entry changes
  the fingerprint of every already-registered account that mapped to it.

> The Telegram for Android identity (`api_id 4`) is **not** supported. Telegram
> gates account creation on it behind a reCAPTCHA that only the official app can
> answer, so `SendCodeRequest` fails with `RECAPTCHA_CHECK_signup__<sitekey>`
> for any number that has no account yet.

---

## Running the Bot

### Standard Mode (Recommended)

Starts the Telegram bot + web panel in a single process:

```bash
python main.py
```

On startup you will see:

```
=======================================================
  📱 Session Manager Bot — Starting...
  🔄 Scheduler  | 📊 Daily Reports | 💾 Auto-Backup
  👥 Multi-Admin | 📋 Action Logs  | 🛡️ Rate-Limit
  🌐 Web Panel  | Port 8080
=======================================================
```

### Docker Mode

```bash
./run.sh
# or, once .env exists:
docker compose up -d --build
```

See [Option A — Docker](#installation) above for details.

### Deployment Mode (Railway)

The FastAPI app is exposed at module level as `app`, so you can deploy using:

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

When deployed this way, the bot starts automatically inside the FastAPI lifespan context.

### Stopping the Bot

Press `Ctrl+C` — the bot handles the shutdown signal gracefully:
1. Stops the web panel server
2. Cancels the scheduler background task
3. Closes all database connections
4. Closes the bot's HTTP session

---

## Web Panel

The integrated web dashboard is available at:

```
http://localhost:8080
```

### Authentication

The web panel uses a **token-based login system**. Generate a login token through the bot (via the Settings menu) and paste it into the web panel login page. Sessions are stored as secure HTTP-only cookies with a 24-hour expiry.

### Dashboard Features

- **Session Overview** — Total session count, country breakdown with flags, and per-country counts
- **Health Statistics** — Account status, spam status, and contact limit distributions as visual charts
- **Admin Management** — View all admins, their roles, and when they were added
- **Activity Log** — The 20 most recent admin actions with timestamps and details
- **Scheduler Status** — Live view of which automated tasks are enabled

### API Routes

The web panel exposes the following route groups:

| Route Prefix | Description |
|-------------|-------------|
| `/dashboard` | Main dashboard view |
| `/sessions/*` | Session list, download, and delete endpoints |
| `/admins/*` | Admin management endpoints |
| `/settings/*` | Bot configuration endpoints |
| `/scheduler/*` | Scheduler enable/disable endpoints |

---

## Project Structure

```
session-manager-bot/
│
├── main.py                     # Entry point — starts bot + web panel
│
├── .env                        # Your environment variables (not committed)
├── .env.example                # Template for environment variables
├── requirements.txt            # Python dependencies
├── requirements.lock           # Pinned dependency snapshot (Docker builds)
│
├── run.sh                      # Interactive Docker setup + launcher
├── Dockerfile                  # Container image definition
├── docker-compose.yml          # Container orchestration + persistent volumes
├── .dockerignore               # Build context exclusions
│
├── config/
│   ├── __init__.py
│   └── config_manager.py       # Async wrapper for DB-backed configuration
│
├── database/
│   ├── __init__.py
│   ├── engine.py               # SQLAlchemy async engine & session factory
│   ├── models.py               # ORM table definitions
│   ├── database.py             # All DB read/write operations
│   └── crud.py                 # Higher-level CRUD helpers
│
├── handlers/
│   ├── __init__.py             # Router aggregator (get_all_routers)
│   ├── start.py                # /start command and main menu
│   ├── register.py             # Session registration flow (OTP)
│   ├── sessions.py             # Session browsing and management
│   ├── check_handler.py        # Spam / contact / validity checks
│   ├── bulk_2fa_handler.py     # Bulk 2FA set / update / delete
│   ├── import_handler.py       # TData and .session import
│   ├── broadcast_handler.py    # Broadcast messages to sessions
│   ├── stats_chart_handler.py  # Statistics and chart generation
│   ├── profile_apply_handler.py# Bulk profile application
│   ├── admin_handler.py        # Admin management commands
│   ├── settings.py             # Bot settings (API, proxy, 2FA, profile)
│   ├── scheduler_handler.py    # Scheduler loop + task triggers
│   ├── panel_handler.py        # Web panel token generation
│   └── common.py               # Shared handler utilities
│
├── middlewares/
│   ├── __init__.py
│   └── middleware.py           # Rate-limiting middleware (msg + callbacks)
│
├── ui/
│   ├── __init__.py
│   ├── keyboards.py            # All inline and reply keyboard builders
│   └── states.py               # FSM state groups for all flows
│
├── utils/
│   ├── __init__.py
│   ├── utils.py                # General utility functions
│   ├── country_utils.py        # Country detection and session folder logic
│   ├── countries_data.py       # Country code → name/flag mapping data
│   └── fix_permissions.py      # File permission helpers
│
├── workers/
│   ├── __init__.py
│   └── session_worker.py       # Async Telethon session operations
│
├── web/
│   ├── __init__.py
│   ├── app.py                  # FastAPI application factory + lifespan
│   ├── auth.py                 # Token validation and session auth
│   ├── security.py             # Security middleware (rate limiting, headers)
│   ├── templates/              # Jinja2 HTML templates
│   │   ├── dashboard.html
│   │   └── login.html
│   ├── static/                 # CSS, JS, and static assets
│   └── routes/
│       ├── __init__.py
│       ├── sessions.py         # /sessions/* API endpoints
│       ├── admins.py           # /admins/* API endpoints
│       ├── settings.py         # /settings/* API endpoints
│       └── scheduler.py        # /scheduler/* API endpoints
│
└── sessions/                   # Session storage directory (auto-created)
    ├── US/                     # Country-based subfolders
    │   ├── +1xxxxxxxxxx.session
    │   └── ...
    ├── GB/
    └── ...
```

---

## Database Schema

The bot uses a fully async SQLite database managed by SQLAlchemy 2.0. All tables are created automatically on first run.

| Table | Purpose |
|-------|---------|
| `config` | Key-value store for all bot settings (API credentials, proxy, 2FA defaults, profile settings) |
| `phone_status` | Cached spam, contact, and account status for each phone number |
| `admins` | Admin user IDs and their roles (`superadmin` / `admin`) |
| `action_log` | Timestamped audit log of all admin actions |
| `scheduler` | Scheduler on/off flags and last-run timestamps |
| `two_factor` | Stored 2FA passwords per phone (for reference/management) |
| `setup` | Pending setup operations (proxy assignments, batch jobs) |
| `web_tokens` | One-time login tokens for the web panel |
| `web_sessions` | Active browser sessions for the web panel |

---

## Multi-Admin System

The bot supports a **hierarchical admin system** with two permission levels:

### Superadmin
- Set via the `ADMIN_ID` environment variable — automatically seeded into the database on first run
- Full access to all features including admin management and bot settings
- Cannot be removed from within the bot

### Admin
- Added by the superadmin through the bot interface
- Can be granted `read-only` (view statistics, check sessions) or `read-write` (all operations except admin management and settings) access
- All actions are recorded in the `action_log` table with the acting user's ID

### Adding an Admin

1. Open the bot as superadmin
2. Navigate to **Admin Management** → **Add Admin**
3. Forward a message from the user you want to add, or enter their Telegram ID
4. Select their permission level

---

## Scheduler & Automation

The bot includes a background scheduler loop (`scheduler_loop` in `handlers/scheduler_handler.py`) that runs automated tasks on configurable intervals.

### Available Automated Tasks

| Task | Description | Default Interval |
|------|-------------|-----------------|
| **Daily Report** | Sends a summary of session health statistics to the superadmin | Every 24 hours |
| **Auto Status Check** | Runs spam and account validity checks on all sessions | Every 24 hours |
| **Auto Backup** | Creates a compressed backup of all session files and sends it to the superadmin | Every 24 hours |

### Enabling Scheduler Tasks

Scheduler tasks are controlled via:
- **Bot:** Settings → Scheduler → toggle individual tasks on/off
- **Web Panel:** Dashboard → Scheduler section → toggle switches

Settings are stored in the `scheduler` database table and persist across restarts.

---

## Security

### Bot Security
- **Admin-Only Access:** All bot commands and callbacks check the requesting user's ID against the `admins` table before executing
- **Rate Limiting:** Built-in middleware limits each user to 10 messages per 5-second window and 15 callback queries per 5-second window to prevent flooding
- **Graceful Validation:** Environment variables are validated at startup — the bot refuses to start with an invalid or missing `BOT_TOKEN`

### Web Panel Security
- **Token Authentication:** Web panel access requires a one-time token generated by the bot; tokens are single-use and expire after a configurable period
- **HTTP-Only Session Cookies:** Browser sessions use `HttpOnly`, `SameSite=Strict` cookies to prevent XSS and CSRF attacks
- **Login Rate Limiting:** Failed login attempts are rate-limited per IP address via `SecurityMiddleware`
- **No Public API Docs:** FastAPI's `/docs` and `/redoc` endpoints are disabled in production
- **Automatic Session Cleanup:** Expired web tokens and browser sessions are cleaned up every hour via a background task

### Data Storage
- All session files are stored locally in the `sessions/` directory — no data is sent to any external service
- 2FA passwords stored in the database should be treated as sensitive — ensure your server and database file are properly secured

---

## Disclaimer

> This software is provided for **educational and personal account management purposes only**.
>
> The developer assumes no liability for any misuse of this software, including but not limited to: account bans, violations of Telegram's Terms of Service, or any damages arising from unauthorized use.
>
> You are solely responsible for ensuring that your use of this software complies with Telegram's [Terms of Service](https://telegram.org/tos) and all applicable laws in your jurisdiction.

---

## License

```
MIT License

Copyright (c) 2026 — All rights reserved by the original developer.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

REDISTRIBUTION OR RESALE of this software without explicit written permission
from the original developer is strictly prohibited.
```

---

## Developer

<div align="center">

<table>
  <tr>
    <td align="center"><strong>Role</strong></td>
    <td>Original Developer & Maintainer</td>
  </tr>
  <tr>
    <td align="center"><strong>Contact</strong></td>
    <td><a href="https://t.me/aa2222a">@aa2222a on Telegram</a></td>
  </tr>
  <tr>
    <td align="center"><strong>Rights</strong></td>
    <td>All Rights Reserved © 2026</td>
  </tr>
</table>

<br/>

[![Contact on Telegram](https://img.shields.io/badge/Contact%20Developer-%40aa2222a-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/aa2222a)

<br/>

> **For support, feature requests, or business inquiries — reach out directly via Telegram.**

<br/>

<sub>Reselling, rebranding, or claiming ownership of this project without explicit written permission from the original developer is strictly prohibited.</sub>

</div>
