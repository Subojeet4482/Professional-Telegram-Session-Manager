"""
Phone number normalization, validation, and detection.

Uses the `phonenumbers` library (Google's libphonenumber port) as the single
source of truth for parsing/validating international numbers, with `pycountry`
for ISO 3166-1 country names. Flags are derived programmatically from the
ISO region code, so every region phonenumbers knows about renders correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers
from phonenumbers import carrier as ph_carrier
from phonenumbers import PhoneNumberFormat, PhoneNumberType

try:
    import pycountry
except ImportError:  # pragma: no cover - pycountry is a hard dependency in requirements.txt
    pycountry = None

# Regions phonenumbers tracks that aren't assigned an ISO 3166-1 entry by pycountry.
_REGION_NAME_OVERRIDES = {
    "AC": "Ascension Island",
    "TA": "Tristan da Cunha",
    "XK": "Kosovo",
}

_NUMBER_TYPE_NAMES = {
    PhoneNumberType.FIXED_LINE: "Fixed line",
    PhoneNumberType.MOBILE: "Mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "Fixed line or mobile",
    PhoneNumberType.TOLL_FREE: "Toll-free",
    PhoneNumberType.PREMIUM_RATE: "Premium rate",
    PhoneNumberType.SHARED_COST: "Shared cost",
    PhoneNumberType.VOIP: "VoIP",
    PhoneNumberType.PERSONAL_NUMBER: "Personal number",
    PhoneNumberType.PAGER: "Pager",
    PhoneNumberType.UAN: "UAN",
    PhoneNumberType.VOICEMAIL: "Voicemail",
    PhoneNumberType.UNKNOWN: "Unknown",
}


class PhoneValidationError(ValueError):
    """Raised when a phone number fails normalization or validation, with a user-facing message."""


def flag_emoji(region_code: str | None) -> str:
    """Convert an ISO 3166-1 alpha-2 region code into its flag emoji (regional indicator symbols)."""
    if not region_code or len(region_code) != 2 or not region_code.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in region_code.upper())


def region_name(region_code: str | None) -> str:
    """Look up the display name for an ISO 3166-1 alpha-2 region code."""
    if not region_code:
        return "Unknown"
    if region_code in _REGION_NAME_OVERRIDES:
        return _REGION_NAME_OVERRIDES[region_code]
    if pycountry is not None:
        country = pycountry.countries.get(alpha_2=region_code)
        if country:
            return country.name
    return region_code


def normalize_phone(raw: str) -> str:
    """
    Normalize a raw, user-supplied phone number: require a leading '+' (E.164
    style input) and strip spaces/dashes/parens/dots while preserving it.

    Does not validate that the number is real — see `parse_phone` for that.
    Raises PhoneValidationError with a clear message on malformed input.
    """
    if raw is None:
        raise PhoneValidationError("Phone number is required.")

    text = raw.strip()
    if not text:
        raise PhoneValidationError("Phone number is required.")

    if not text.startswith("+"):
        raise PhoneValidationError(
            "Phone number must start with '+' followed by the country code, "
            "e.g. <code>+966512345678</code>."
        )

    digits = re.sub(r"[^\d]", "", text[1:])
    if not digits:
        raise PhoneValidationError("Phone number must contain digits after '+'.")

    return "+" + digits


@dataclass
class PhoneInfo:
    e164: str            # Normalized, validated number, e.g. "+966512345678"
    digits: str           # E164 without the leading '+' (legacy storage format used across the bot)
    calling_code: str     # Numeric country calling code, e.g. "966"
    region_code: str      # ISO 3166-1 alpha-2 region, e.g. "SA"
    country_name: str     # e.g. "Saudi Arabia"
    flag: str             # Flag emoji derived from region_code
    carrier: str          # Carrier name if the library can determine one, else ""
    number_type: str      # Human-readable line type, e.g. "Mobile"


def parse_phone(raw: str) -> PhoneInfo:
    """
    Normalize + validate a raw phone number and return full detection details
    (country, region, carrier, line type) with the highest accuracy available
    via the `phonenumbers` library.

    Raises PhoneValidationError with a clear, user-facing message if the
    number is malformed or not a valid, in-use number.
    """
    normalized = normalize_phone(raw)

    try:
        parsed = phonenumbers.parse(normalized, None)
    except phonenumbers.NumberParseException as e:
        raise PhoneValidationError(
            f"❌ Invalid phone number format ({e.error_type}). "
            "Make sure it starts with '+' and the correct country code, "
            "e.g. <code>+966512345678</code>."
        ) from e

    if not phonenumbers.is_valid_number(parsed):
        raise PhoneValidationError(
            "❌ This phone number isn't a valid, in-use number for its country. "
            "Double-check the country code and digit count and try again."
        )

    region_code = phonenumbers.region_code_for_number(parsed) or "001"
    e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    carrier_name = ph_carrier.name_for_number(parsed, "en") or ""
    type_name = _NUMBER_TYPE_NAMES.get(phonenumbers.number_type(parsed), "Unknown")

    return PhoneInfo(
        e164=e164,
        digits=e164.lstrip("+"),
        calling_code=str(parsed.country_code),
        region_code=region_code,
        country_name=region_name(region_code),
        flag=flag_emoji(region_code),
        carrier=carrier_name,
        number_type=type_name,
    )
