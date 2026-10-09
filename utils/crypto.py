"""
Secret encryption helpers — Fernet (AES-128-CBC + HMAC-SHA256) at rest.

Used for 2FA passwords, which are account-recovery credentials. They are
encrypted in the database so that a stolen `bot.db`, a backup, or a stray
`git add -f` does not hand over every account's recovery password in one query.

They are DECRYPTED on demand for display: an authenticated admin asking for a
password in the bot or in the web panel still sees plaintext. The threat model
here is offline access to the datastore, not the admin themselves.

Key management
--------------
The key is taken from the TWOFA_ENC_KEY environment variable. If unset, one is
generated on first use and written to `.2fa_key` (mode 0600, gitignored) next
to the database.

  ⚠️  A key file living beside the database it protects only helps against
      *offline* disclosure — a DB dump, a backup tarball, an accidental commit.
      An attacker with filesystem access to the deployment gets both. For real
      separation set TWOFA_ENC_KEY in the environment / secret store and delete
      the key file.

Losing the key makes stored passwords unrecoverable, so back it up alongside
(but not inside) your database backups.
"""

from __future__ import annotations

import logging
import os
import stat

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("crypto")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_KEY_ENV = "TWOFA_ENC_KEY"
_KEY_FILE = os.path.join(BASE_DIR, ".2fa_key")

# Marker distinguishing ciphertext from legacy plaintext rows written before
# encryption existed. Versioned so the scheme can be rotated later.
_PREFIX = "enc:v1:"

_fernet: Fernet | None = None


def _load_or_create_key() -> bytes:
    """Resolve the encryption key from env, else from/into the key file."""
    env_key = (os.getenv(_KEY_ENV) or "").strip()
    if env_key:
        return env_key.encode()

    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            key = f.read().strip()
        if key:
            return key

    key = Fernet.generate_key()
    with open(_KEY_FILE, "wb") as f:
        f.write(key)
    try:
        os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        # Windows/other filesystems may not support this; not fatal.
        pass

    logger.warning(
        "🔑 Generated a new 2FA encryption key at %s. "
        "Back it up — losing it makes stored 2FA passwords unrecoverable. "
        "For better separation, move it into the %s environment variable.",
        _KEY_FILE, _KEY_ENV,
    )
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def is_encrypted(value: str | None) -> bool:
    """True if `value` is a ciphertext produced by encrypt_secret()."""
    return bool(value) and value.startswith(_PREFIX)


def encrypt_secret(plaintext: str | None) -> str | None:
    """
    Encrypt a secret for storage.

    Empty/None passes through unchanged so "no password" stays falsy rather
    than becoming an encrypted empty string.
    """
    if not plaintext:
        return plaintext
    if is_encrypted(plaintext):
        return plaintext  # already encrypted; don't double-wrap
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_secret(stored: str | None) -> str | None:
    """
    Decrypt a stored secret back to plaintext for display to an admin.

    Rows written before encryption was introduced have no prefix and are
    returned as-is, so the feature keeps working during/after migration.
    """
    if not stored:
        return stored
    if not is_encrypted(stored):
        return stored  # legacy plaintext row

    try:
        return _get_fernet().decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Wrong or rotated key — do not crash the caller, and never leak the
        # ciphertext into the UI as if it were the password.
        logger.error(
            "Failed to decrypt a stored 2FA password: the encryption key does "
            "not match the data. Check %s / %s.", _KEY_ENV, _KEY_FILE,
        )
        return None
