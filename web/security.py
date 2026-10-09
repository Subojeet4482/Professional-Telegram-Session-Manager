"""
Web Panel Security — DDoS protection, rate limiting, IP banning, security headers.
"""

import os
import time
import logging
from collections import OrderedDict
import asyncio

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from web.csrf import verify_csrf_token, CSRF_HEADER
from web.auth import SESSION_COOKIE

logger = logging.getLogger("web.security")

_CSRF_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# ── In-memory stores ──────────────────────────────────────────────────────────
# All three are OrderedDicts used as bounded LRU caches: every read/write
# moves its key to the end, and insertion evicts the oldest entry once the
# cap is hit. A flood of requests from many distinct/spoofed source IPs can
# therefore no longer grow these dicts without bound — the previous plain
# dicts only shrank via a cleanup pass that (see below) almost never ran.
_MAX_TRACKED_IPS = 20_000
_MAX_BANNED_IPS = 5_000

_banned_ips: OrderedDict[str, float] = OrderedDict()  # ip -> ban_expiry_timestamp
# Token bucket for rate limiting: ip -> [tokens, last_update_time]
_rate_limits: OrderedDict[str, list[float]] = OrderedDict()
# Token bucket for login attempts: ip -> [tokens, last_update_time]
_login_limits: OrderedDict[str, list[float]] = OrderedDict()

# ── Config ────────────────────────────────────────────────────────────────────
RATE_LIMIT_REQUESTS = 60        # requests per window
RATE_LIMIT_WINDOW   = 60        # seconds (fill rate = RATE_LIMIT_REQUESTS / RATE_LIMIT_WINDOW tokens per sec)
BAN_THRESHOLD       = 300       # requests per window before ban (burst capacity)
BAN_DURATION        = 3600      # 1 hour ban
LOGIN_RATE_LIMIT    = 10        # max login attempts
LOGIN_RATE_WINDOW   = 600       # per 10 minutes

TRUST_FORWARDED_IP = os.getenv("TRUST_FORWARDED_IP", "false").lower() == "true"

_request_counter = 0
_CLEANUP_EVERY = 500  # deterministic — see _cleanup_stale_ips


def _touch_lru(store: OrderedDict, key, value, max_size: int) -> None:
    """Insert/refresh `key`, marking it most-recently-used, evicting the LRU entry if over cap."""
    store[key] = value
    store.move_to_end(key)
    while len(store) > max_size:
        store.popitem(last=False)


def _get_real_ip(request: Request) -> str:
    """
    Get the real client IP.

    When TRUST_FORWARDED_IP is set, the operator is asserting that this app
    sits behind their own reverse proxy which appends to X-Forwarded-For. In
    that topology the LAST entry is the one the trusted proxy itself observed
    and wrote — the client cannot control it. The FIRST entry is whatever the
    client's own request claimed and is fully attacker-controlled, so trusting
    it (the previous behavior) let any client rate-limit/ban an arbitrary IP
    of their choosing by spoofing that header.
    """
    if TRUST_FORWARDED_IP:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
            if parts:
                return parts[-1]
    return request.client.host if request.client else "unknown"

def _cleanup_stale_ips():
    """
    Sweep expired entries out of all three stores.

    Previously gated behind `int(now * 1000) % 1000 == 0` — that condition
    depends on `time.monotonic()` landing on an exact millisecond boundary
    modulo 1000, which real request arrival times essentially never do, so
    this almost never ran and the stores grew unbounded between restarts. It
    now runs deterministically every _CLEANUP_EVERY requests (see dispatch),
    with the LRU cap above as a hard backstop regardless of cadence.
    """
    now = time.monotonic()
    for ip in list(_rate_limits.keys()):
        if now - _rate_limits[ip][1] > RATE_LIMIT_WINDOW:
            del _rate_limits[ip]
    for ip in list(_login_limits.keys()):
        if now - _login_limits[ip][1] > LOGIN_RATE_WINDOW:
            del _login_limits[ip]
    current_time = time.time()
    for ip in list(_banned_ips.keys()):
        if current_time > _banned_ips[ip]:
            del _banned_ips[ip]


class SecurityMiddleware(BaseHTTPMiddleware):
    """Combined DDoS protection, rate limiting, and security headers."""

    async def dispatch(self, request: Request, call_next):
        global _request_counter

        client_ip = _get_real_ip(request)

        # 1. Check if IP is banned
        if client_ip in _banned_ips:
            if time.time() < _banned_ips[client_ip]:
                _banned_ips.move_to_end(client_ip)
                return JSONResponse({"error": "Forbidden"}, status_code=403)
            else:
                del _banned_ips[client_ip]

        # 2. Token Bucket Rate Limit check
        now = time.monotonic()
        bucket = _rate_limits.get(client_ip)

        # Fill rate: capacity / window
        fill_rate = BAN_THRESHOLD / RATE_LIMIT_WINDOW

        if bucket is None:
            # [tokens, last_update]
            bucket = [float(BAN_THRESHOLD - 1), now]
        else:
            elapsed = now - bucket[1]
            bucket[0] = min(float(BAN_THRESHOLD), bucket[0] + elapsed * fill_rate)
            bucket[1] = now
            bucket[0] -= 1
        _touch_lru(_rate_limits, client_ip, bucket, _MAX_TRACKED_IPS)

        if bucket[0] < 0:
            _touch_lru(_banned_ips, client_ip, time.time() + BAN_DURATION, _MAX_BANNED_IPS)
            logger.warning("DDoS detected - IP banned: %s", client_ip)
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        elif bucket[0] < (BAN_THRESHOLD - RATE_LIMIT_REQUESTS):
            return JSONResponse(
                {"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
            )

        # Deterministic periodic cleanup — see _cleanup_stale_ips.
        _request_counter += 1
        if _request_counter >= _CLEANUP_EVERY:
            _request_counter = 0
            _cleanup_stale_ips()

        # 3. CSRF check for state-changing API calls made by an authenticated
        # browser session. SameSite=Strict on the session cookie already
        # blocks the classic cross-site-form-post attack in modern browsers;
        # this is the fallback layer and only engages when a session cookie
        # is actually present — an unauthenticated request still falls
        # through to the normal 401 from require_auth.
        if request.method in _CSRF_METHODS and request.url.path.startswith("/api/"):
            session_id = request.cookies.get(SESSION_COOKIE)
            if session_id and not verify_csrf_token(session_id, request.headers.get(CSRF_HEADER)):
                return JSONResponse({"error": "Missing or invalid CSRF token"}, status_code=403)

        # 4. Process request
        response = await call_next(request)

        # 5. Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # script-src is now strict: no 'unsafe-inline', no third-party CDN.
        #   - every handler is bound via addEventListener in app.js
        #   - template data crosses over in a type="application/json" island
        #   - Chart.js is vendored at /static/chart.umd.min.js
        # This is the control that turns an injected on*= handler from
        # "executes" into "inert", so do not re-add 'unsafe-inline' here.
        #
        # style-src keeps 'unsafe-inline' because the templates rely on inline
        # style="" attributes for layout. Inline styles are not a script
        # execution vector; tightening this is a separate, cosmetic refactor.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'; "
            "object-src 'none'"
        )

        return response


def check_login_rate_limit(ip: str) -> None:
    """Raise 429 if too many login attempts from this IP. Uses Token Bucket."""
    now = time.monotonic()
    bucket = _login_limits.get(ip)

    fill_rate = LOGIN_RATE_LIMIT / LOGIN_RATE_WINDOW

    if bucket is None:
        bucket = [float(LOGIN_RATE_LIMIT - 1), now]
    else:
        elapsed = now - bucket[1]
        bucket[0] = min(float(LOGIN_RATE_LIMIT), bucket[0] + elapsed * fill_rate)
        bucket[1] = now
        bucket[0] -= 1
    _touch_lru(_login_limits, ip, bucket, _MAX_TRACKED_IPS)

    if bucket[0] < 0:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
