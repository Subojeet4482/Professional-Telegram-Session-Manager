"""
CSRF protection — double-submit token bound to the session cookie.

The session cookie is SameSite=Strict already, which blocks the browser from
attaching it to cross-site requests in every modern browser. This is a second,
independent layer for older/misconfigured browsers and any future relaxation
of that attribute: a state-changing request must also present a token that
proves the caller can read cookies set for this origin, which a cross-site
attacker page cannot do.

The token is not random/stored — it's an HMAC of the session_id under a
server secret, so it can be verified with no DB lookup and no extra state:
`token == HMAC(secret, session_id)`. An attacker who cannot read the
session_id (HttpOnly) or the derived token (different origin, Same-Origin
Policy) cannot forge a valid pair.

Key management mirrors utils/crypto.py's TWOFA_ENC_KEY / .2fa_key pattern.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import stat

logger = logging.getLogger("web.csrf")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_KEY_ENV = "PANEL_CSRF_SECRET"
_KEY_FILE = os.path.join(BASE_DIR, ".csrf_key")

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

_secret: bytes | None = None


def _load_or_create_secret() -> bytes:
    env_key = (os.getenv(_KEY_ENV) or "").strip()
    if env_key:
        return env_key.encode()

    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            key = f.read().strip()
        if key:
            return key

    key = os.urandom(32)
    with open(_KEY_FILE, "wb") as f:
        f.write(key)
    try:
        os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # Windows/other filesystems may not support this; not fatal.

    logger.warning(
        "Generated a new CSRF secret at %s. For better separation, move it "
        "into the %s environment variable.", _KEY_FILE, _KEY_ENV,
    )
    return key


def _get_secret() -> bytes:
    global _secret
    if _secret is None:
        _secret = _load_or_create_secret()
    return _secret


def generate_csrf_token(session_id: str) -> str:
    """Derive the CSRF token for a given session_id. Deterministic, not stored."""
    return hmac.new(_get_secret(), session_id.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_csrf_token(session_id: str | None, token: str | None) -> bool:
    """True if `token` is the correct CSRF token for `session_id`."""
    if not session_id or not token:
        return False
    expected = generate_csrf_token(session_id)
    return hmac.compare_digest(expected, token)
