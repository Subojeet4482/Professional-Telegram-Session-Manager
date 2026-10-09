#!/usr/bin/env bash
# Interactive setup + launcher for the Session Manager Bot.
# Collects the required config, writes .env, then builds and starts
# the bot (+ web panel) in Docker.
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env"

echo "======================================================"
echo "  Telegram Session Manager Bot — Docker setup"
echo "======================================================"
echo

# ── Reuse an existing .env if present ─────────────────────────────────────
KEEP_EXISTING=false
if [ -f "$ENV_FILE" ]; then
    existing_token=$(grep -m1 '^BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)
    existing_admin=$(grep -m1 '^ADMIN_ID=' "$ENV_FILE" | cut -d= -f2- || true)
    echo "An existing .env was found:"
    echo "  BOT_TOKEN=${existing_token:0:8}... (masked)"
    echo "  ADMIN_ID=${existing_admin}"
    read -rp "Keep this configuration? [Y/n] " keep
    case "${keep:-Y}" in
        [nN]*) KEEP_EXISTING=false ;;
        *) KEEP_EXISTING=true ;;
    esac
fi

if [ "$KEEP_EXISTING" = false ]; then
    # ── BOT_TOKEN ──────────────────────────────────────────────────────────
    BOT_TOKEN=""
    while [ -z "$BOT_TOKEN" ]; do
        read -rp "Bot token (from @BotFather): " BOT_TOKEN
        [ -z "$BOT_TOKEN" ] && echo "  BOT_TOKEN is required."
    done

    # ── ADMIN_ID ───────────────────────────────────────────────────────────
    ADMIN_ID=""
    while ! [[ "$ADMIN_ID" =~ ^[0-9]+$ ]]; do
        read -rp "Your numeric Telegram ID (from @userinfobot): " ADMIN_ID
        [[ "$ADMIN_ID" =~ ^[0-9]+$ ]] || echo "  ADMIN_ID must be numeric."
    done

    # ── PANEL_PORT ─────────────────────────────────────────────────────────
    read -rp "Web panel port [8080]: " PANEL_PORT
    PANEL_PORT="${PANEL_PORT:-8080}"
    while ! [[ "$PANEL_PORT" =~ ^[0-9]+$ ]]; do
        read -rp "  Invalid port, enter a number [8080]: " PANEL_PORT
        PANEL_PORT="${PANEL_PORT:-8080}"
    done

    cat > "$ENV_FILE" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_ID=${ADMIN_ID}
PANEL_PORT=${PANEL_PORT}
EOF
    echo "✅ Wrote $ENV_FILE"
fi

# ── Persistent files/dirs the container bind-mounts ───────────────────────
# Docker auto-creates a bind-mount source as a DIRECTORY if it doesn't exist
# yet when the container first starts. If that happened here on an earlier
# run (e.g. before this guard existed), bot.db/.2fa_key/.csrf_key would be
# empty directories instead of files — `touch` alone can't fix that, since
# touch on an existing directory just updates its timestamp.
for f in bot.db .2fa_key .csrf_key; do
    if [ -d "$f" ]; then
        if [ -z "$(ls -A "$f" 2>/dev/null)" ]; then
            echo "⚠️  $f was created as an empty directory by Docker — removing it so it can be a file."
            rmdir "$f"
        else
            echo "❌ $f is a non-empty directory, expected a file. Remove or rename it manually and re-run."
            exit 1
        fi
    fi
    [ -e "$f" ] || touch "$f"
done
mkdir -p sessions temp_sessions

# ── Docker Compose (plugin or legacy binary) ──────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "❌ Docker is not installed. Install it first: https://docs.docker.com/get-docker/"
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    echo "❌ Docker Compose is not installed."
    exit 1
fi

echo
echo "🚀 Building and starting the bot..."
"${COMPOSE[@]}" up -d --build

port=$(grep -m1 '^PANEL_PORT=' "$ENV_FILE" | cut -d= -f2- || echo 8080)
echo
echo "======================================================"
echo "✅ Bot is running."
echo "   Web panel:  http://localhost:${port}"
echo "   Logs:       ${COMPOSE[*]} logs -f"
echo "   Stop:       ${COMPOSE[*]} down"
echo "======================================================"
