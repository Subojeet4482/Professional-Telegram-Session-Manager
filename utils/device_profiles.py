"""
Device fingerprints for Telethon clients.

Telegram decides how — and whether — to deliver a login code partly from the
InitConnection payload a client sends on connect. A device_model that no real
machine reports, an app_version years behind the current release, or a
system_lang_code that contradicts lang_code all read as "not an official
Telegram app", and the usual symptom is a code that simply never arrives.
Every client the bot builds goes through this module so that payload matches
what a real Telegram build actually sends.

One official app identity is supported:

    desktop   api_id 2040   Telegram Desktop (Windows)

The Telegram for Android identity (api_id 4) was removed: Telegram gates
account creation on it behind a reCAPTCHA that only the official app can
answer, so SendCodeRequest fails with
``RECAPTCHA_CHECK_signup__<sitekey>`` for any number without an account.

A fingerprint is derived from the account's phone number, not re-rolled on
every connection: the same account always presents the same machine. The
previous code generated a fresh ``device_model`` inside ``create_client``,
which is called on every check, broadcast and 2FA pass — so a single auth_key
appeared to hop between machines several times a day.

The device pools below are APPEND-ONLY. Reordering or removing an entry
changes the fingerprint of every already-registered account that mapped to it,
which is exactly the anomaly this module exists to avoid.
"""

import hashlib
import os
from dataclasses import dataclass

# ── Platform keys ─────────────────────────────────────────────────────────────

DESKTOP = "desktop"
CUSTOM = "custom"

PLATFORM_LABELS = {
    DESKTOP: "🖥 Telegram Desktop",
    CUSTOM: "🛠 Custom API",
}

# ── Official app credentials ──────────────────────────────────────────────────
# The api_id/api_hash pair Telegram Desktop itself uses. Telegram treats it
# differently from a self-registered api_id: only official app IDs get app-code
# delivery into other logged-in Telegram clients.

OFFICIAL_CREDENTIALS: dict[str, tuple[int, str]] = {
    DESKTOP: (2040, "b18441a1ff607e10a989891a5462e627"),
}

# lang_pack is reserved for official apps; it must agree with the api_id above.
LANG_PACKS = {DESKTOP: "tdesktop", CUSTOM: ""}

_PLATFORM_BY_API_ID = {2040: DESKTOP}

# ── Current app version ───────────────────────────────────────────────────────
# Bump this when Telegram Desktop ships a new release. A version far behind the
# live one is the single most common reason codes stop being delivered.

DESKTOP_APP_VERSION = "7.0.6 x64"

# ── Language ──────────────────────────────────────────────────────────────────
# system_lang_code is the OS locale and is region-qualified on every real
# install; lang_code is the app language and is not. Telethon defaults both to
# "en", which no real client ever sends.

DEFAULT_LANG_CODE = "en"
DEFAULT_SYSTEM_LANG_CODE = "en-US"


# ── Desktop device pool ───────────────────────────────────────────────────────
# Telegram Desktop on Windows sends the SMBIOS system identity (manufacturer +
# product name from HARDWARE\DESCRIPTION\System\BIOS), not the computer name.
# So it reports "Dell Inc. OptiPlex 7090" or "ASUS System Product Name" — never
# "DESKTOP-8F3KD9A", which is the hostname and is never transmitted.
#
# Each entry pairs a machine with the Windows releases it plausibly runs:
# Windows 11 requires TPM 2.0 and an 8th-gen/Ryzen-2000-or-newer CPU, so older
# hardware reporting Windows 11 is itself an inconsistency.

_WIN10 = ("Windows 10",)
_WIN10_11 = ("Windows 10", "Windows 11")

_DESKTOP_DEVICES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # OEM prebuilts — the bulk of real Windows installs.
    ("Dell Inc. OptiPlex 7010", _WIN10),
    ("Dell Inc. OptiPlex 7090", _WIN10_11),
    ("Dell Inc. Inspiron 3891", _WIN10_11),
    ("Dell Inc. Latitude 5420", _WIN10_11),
    ("Dell Inc. XPS 8950", _WIN10_11),
    ("HP Pavilion Desktop TP01-2xxx", _WIN10_11),
    ("HP Laptop 15-dw3xxx", _WIN10_11),
    ("HP EliteDesk 800 G6", _WIN10_11),
    ("HP ProDesk 400 G7", _WIN10_11),
    ("LENOVO 10SES02U00", _WIN10),          # ThinkCentre M720t
    ("LENOVO 11DTS0BF00", _WIN10_11),       # ThinkCentre M70q
    ("LENOVO 82K2", _WIN10_11),             # IdeaPad Gaming 3
    ("LENOVO 21CB", _WIN10_11),             # ThinkPad L14
    ("Acer Aspire TC-895", _WIN10_11),
    ("Acer Nitro AN515-45", _WIN10_11),
    # DIY builds — the board reports itself; "System Product Name" is the
    # literal default ASUS ships and is extremely common in the wild.
    ("ASUS System Product Name", _WIN10_11),
    ("ASUSTeK COMPUTER INC. ROG STRIX B550-F GAMING", _WIN10_11),
    ("ASUSTeK COMPUTER INC. TUF GAMING X570-PLUS", _WIN10_11),
    ("ASUSTeK COMPUTER INC. PRIME B450M-A", _WIN10),
    ("ASUSTeK COMPUTER INC. PRIME H610M-K", _WIN10_11),
    ("Micro-Star International Co., Ltd. MS-7C56", _WIN10_11),   # B550-A PRO
    ("Micro-Star International Co., Ltd. MS-7C02", _WIN10),      # B450 TOMAHAWK
    ("Micro-Star International Co., Ltd. MS-7D25", _WIN10_11),   # PRO Z690-A
    ("Gigabyte Technology Co., Ltd. B550M DS3H", _WIN10_11),
    ("Gigabyte Technology Co., Ltd. B450M DS3H", _WIN10),
    ("Gigabyte Technology Co., Ltd. Z690 AORUS ELITE", _WIN10_11),
    ("ASRock B450M Pro4", _WIN10),
    ("ASRock B550 Steel Legend", _WIN10_11),
    # Telegram Desktop's own fallback when the BIOS exposes nothing readable.
    ("PC 64bit", _WIN10_11),
)


@dataclass(frozen=True)
class DeviceProfile:
    """One coherent client identity, ready to hand to Telethon."""

    platform: str
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str
    lang_pack: str

    def as_client_kwargs(self) -> dict:
        """Keyword arguments accepted by ``TelegramClient.__init__``.

        ``lang_pack`` is not among them — Telethon hardcodes it to "" — so it
        is applied separately by :func:`apply_lang_pack`.
        """
        return {
            "device_model": self.device_model,
            "system_version": self.system_version,
            "app_version": self.app_version,
            "lang_code": self.lang_code,
            "system_lang_code": self.system_lang_code,
        }


# ── Seeded selection ──────────────────────────────────────────────────────────
# SHA-256 rather than random.seed(): the mapping is fixed by the standard, so a
# Python upgrade can never silently re-roll every account's device.


def _pick_index(seed: str, salt: str, size: int) -> int:
    digest = hashlib.sha256(f"{salt}:{seed}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def _pick(items, seed: str, salt: str):
    return items[_pick_index(seed, salt, len(items))]


def normalize_seed(seed: str | None) -> str:
    """Reduce a phone number or session name to stable seed material.

    Registration writes to ``temp_sessions/<uid>_<phone>`` before the session is
    moved to its final ``<phone>.session`` path, so the raw basename is not
    stable across an account's lifetime — the phone digits within it are.
    Falls back to fresh randomness when there is nothing to key on, which keeps
    throwaway clients (the proxy tester) from all sharing one identity.
    """
    if seed:
        digits = "".join(ch for ch in str(seed) if ch.isdigit())
        # "<uid>_<phone>" — keep the phone, drop the Telegram user id prefix.
        raw = str(seed)
        if "_" in raw:
            tail = "".join(ch for ch in raw.rsplit("_", 1)[-1] if ch.isdigit())
            if len(tail) >= 7:
                return tail
        if digits:
            return digits
    return os.urandom(16).hex()


def seed_from_session_path(session_path: str) -> str:
    """Derive seed material from a session file path.

    Sessions in this bot are named after the account's phone number, so the
    basename is the right key and every existing ``create_client`` call site
    gets a stable fingerprint without being touched.
    """
    base = os.path.basename(str(session_path or ""))
    if base.endswith(".session"):
        base = base[: -len(".session")]
    return normalize_seed(base)


# ── Platform resolution ───────────────────────────────────────────────────────


def resolve_platform(api_id: int | None) -> str:
    """Map an api_id back to the app identity it belongs to.

    Lets ``create_client`` stay in sync with the configured platform without
    every call site having to pass it: the api_id already travels everywhere.
    """
    try:
        return _PLATFORM_BY_API_ID.get(int(api_id), CUSTOM)
    except (TypeError, ValueError):
        return CUSTOM


def credentials_for(platform: str) -> tuple[int, str] | None:
    """Official (api_id, api_hash) for a platform, or None if it has none."""
    return OFFICIAL_CREDENTIALS.get(platform)


# ── Profile construction ──────────────────────────────────────────────────────


def build_profile(
    platform: str | None,
    seed: str | None = None,
    lang_code: str = DEFAULT_LANG_CODE,
    system_lang_code: str = DEFAULT_SYSTEM_LANG_CODE,
) -> DeviceProfile:
    """Build the device identity for one account on one platform."""
    platform = platform or DESKTOP
    key = normalize_seed(seed)

    # Custom api_ids get a desktop-shaped identity too — still far more
    # plausible than Telethon's uname()-derived default — but with an empty
    # lang_pack, since lang_pack only belongs to official app IDs.
    device_model, windows_versions = _pick(_DESKTOP_DEVICES, key, "desktop.device")
    system_version = _pick(windows_versions, key, "desktop.os")

    return DeviceProfile(
        platform=platform,
        device_model=device_model,
        system_version=system_version,
        app_version=DESKTOP_APP_VERSION,
        lang_code=lang_code,
        system_lang_code=system_lang_code,
        lang_pack=LANG_PACKS.get(platform, ""),
    )


def apply_lang_pack(client, profile: DeviceProfile) -> None:
    """Set lang_pack on an already-constructed client.

    Telethon hardcodes ``lang_pack=''`` in the InitConnection it builds, but a
    real official client always sends one ("tdesktop") and the field is visible
    to Telegram alongside the api_id it is supposed to match.
    Touching a private attribute, so failure is non-fatal: a mismatched
    lang_pack is a weaker signal than no client at all.
    """
    if not profile.lang_pack:
        return
    try:
        client._init_request.lang_pack = profile.lang_pack
    except Exception:  # pragma: no cover - depends on Telethon internals
        pass


def describe(profile: DeviceProfile) -> str:
    """One-line human summary, for settings screens and logs."""
    return (
        f"{profile.device_model} · {profile.system_version} · "
        f"{profile.app_version} · {profile.system_lang_code}"
    )
