"""
Session Worker — Telethon client handling for registration and proxy testing.
"""

import socks
import asyncio
import re
import logging
from telethon import TelegramClient, events, functions
from telethon.errors import (
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberBannedError,
    RPCError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
)
from utils.device_profiles import (
    apply_lang_pack,
    build_profile,
    resolve_platform,
    seed_from_session_path,
)

# Re-export errors for handlers
__all__ = [
    "get_proxy_tuple",
    "create_client",
    "client_profile",
    "test_proxy_connection",
    "check_spam",
    "check_contact_limit",
    "check_session_alive",
    "check_frozen",
    "PhoneNumberInvalidError",
    "PhoneCodeInvalidError",
    "PhoneCodeExpiredError",
    "SessionPasswordNeededError",
    "FloodWaitError",
    "PhoneNumberBannedError",
    "UserDeactivatedBanError",
    "AuthKeyUnregisteredError",
]


def get_proxy_tuple(proxy_config: dict) -> tuple | None:
    """Convert proxy config dict to Telethon proxy tuple (PySocks format)."""
    if not proxy_config.get("enabled", True):
        return None

    type_map = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }

    proxy_type = type_map.get(proxy_config.get("type", "socks5"), socks.SOCKS5)
    addr = proxy_config["host"]
    port = int(proxy_config["port"])
    rdns = proxy_config.get("rdns", True)
    username = proxy_config.get("username", None) or None
    password = proxy_config.get("password", None) or None

    return (proxy_type, addr, port, rdns, username, password)




def client_profile(session_path: str, api_id: int, phone: str | None = None, platform: str | None = None):
    """Resolve the device identity a client for this account will present.

    The platform is derived from the api_id, so a client connecting with the
    official Desktop credentials (2040) always gets the matching lang_pack,
    and a custom api_id never gets one it isn't entitled to.
    """
    return build_profile(
        platform or resolve_platform(api_id),
        seed=phone or seed_from_session_path(session_path),
    )


def create_client(
    session_path: str,
    api_id: int,
    api_hash: str,
    proxy_config: dict,
    phone: str | None = None,
    platform: str | None = None,
) -> TelegramClient:
    """Create a TelegramClient with the given proxy and a realistic device identity.

    ``phone`` only needs to be passed while a session still lives under its
    temporary registration path; once the file is named after the number, the
    path itself is enough to keep the fingerprint stable across connections.
    """
    proxy_tuple = get_proxy_tuple(proxy_config)
    profile = client_profile(session_path, api_id, phone=phone, platform=platform)

    client = TelegramClient(
        session_path,
        api_id, api_hash,
        proxy=proxy_tuple,
        connection_retries=3,
        retry_delay=1,
        **profile.as_client_kwargs(),
    )
    apply_lang_pack(client, profile)
    return client


async def test_proxy_connection(api_id: int, api_hash: str, proxy_config: dict) -> tuple[bool, str]:
    """
    Test proxy connection by connecting to Telegram.
    Returns (success, message).
    """
    import tempfile
    import shutil
    import os

    tmp_dir = tempfile.mkdtemp(prefix="proxy_test_")
    tmp_session = os.path.join(tmp_dir, "_proxy_test_session")
    client = create_client(tmp_session, api_id, api_hash, proxy_config)

    try:
        await client.connect()
        await client.disconnect()
        return True, "✅ Proxy connection successful!"
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        return False, f"❌ Connection failed: {type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)




async def check_spam(client: TelegramClient) -> str:
    """
    Sends /start to @SpamBot and processes the reply immediately upon arrival.
    """
    spam_bot = "@SpamBot"
    result = "UNKNOWN"
    
    # Define patterns
    spam_patterns = [
    r"\bsorry\b", r"\bhet spijt me\b", r"\bes tut mir wirklich leid\b",
    r"\bsaya meminta maaf\b", r"\bsono davvero dispiaciuto\b",
    r"بسيار متأسفم", r"\bsinto muito\b", r"очень жаль",
    r"siento mucho", r"juda afsusdaman", r"نعتذر بشدة",
    
    # New additions (Arabic, English, and Persian)
    r"للأسف وجد بعض مستخدمي تيليجرام رسائلك مزعجة",
    r"I’m afraid some Telegram users found your messages annoying",
    r"به اطلاعتان می‌رسانیم برخی از کاربران تلگرام پیام‌های شما را آزاردهنده دانسته"
]

    ok_patterns = [
        r"رائع", r"goed nieuws", r"good news", r"gute nachrichten",
        r"kabar baik", r"buone notizie", r"berita baik", r"مژده",
        r"boas notícias", r"ваш аккаунт свободен", r"buenas noticias", r"sizga xushxabar"
    ]
    banned_patterns = [
        r"\byour account was blocked\b", r"\baccount was banned\b",
        r"\byou are banned\b", r"\bpermanently blocked\b"
    ]
    warning_texts = [
        "unfortunately, some phone numbers may trigger a harsh response",
        "للأسف، قد تسبب بعض أرقام الهواتف", "helaas reageert ons anti-spamsysteem harder",
        "leider können einige Telefonnummern", "sayang sekali, beberapa nomor ponsel",
        "sfortunatamente, alcuni numeri di telefono", "malangnya, setengah nombor fon",
        "بسيار متأسفم كه گاهى بعضى از شمارههاى تلفن", "infelizmente, algunos números de telefone",
        "к сожалению, иногда наша антиспам-система", "lamentablemente, algunos números de teléfono",
        "afsuski, ayrim telefon raqamlari"
    ]

    # Event to wait for response
    event_received = asyncio.Event()

    @client.on(events.NewMessage(from_users=spam_bot))
    async def handler(event):
        nonlocal result
        text = event.raw_text.lower()
        
        if any(re.search(p, text) for p in spam_patterns):
            result = "SPAM"
        elif any(w.lower() in text for w in warning_texts):
            result = "NEW_REGISTERED"
        elif any(re.search(p, text) for p in ok_patterns):
            result = "FREE"
        elif any(re.search(p, text) for p in banned_patterns):
            result = "BANNED"
            await client.log_out()
        
        event_received.set()

    try:
        # Send message to start
        await client.send_message(spam_bot, "/start")
        
        # Wait for response for a maximum of 10 seconds (timeout) to avoid hanging
        try:
            await asyncio.wait_for(event_received.wait(), timeout=10)
        except asyncio.TimeoutError:
            result = "UNKNOWN"

    except asyncio.TimeoutError:
        result = "UNKNOWN"
    except RPCError as e:
        if "FROZEN" in str(e).upper():
            result = "FROZEN"
        else:
            logging.error(f"check_spam RPC error: {e}")
    except Exception as e:
        logging.exception(f"check_spam unexpected error: {e}")
    finally:
        # Remove handler immediately
        client.remove_event_handler(handler)
        # Delete dialog
        try:
            await client.delete_dialog(spam_bot)
        except:
            pass

    return result


async def check_contact_limit(client: TelegramClient) -> str:
    """
    Check if the account is limited from adding new contacts.
    Uses ImportContactsRequest with a test number.
    Returns: "NoLimit", "Limited", or "UNKNOWN".
    """
    try:
        from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
        from telethon.tl.types import InputPhoneContact

        contact = InputPhoneContact(
            client_id=0,
            phone="+970568502325",
            first_name="TEST",
            last_name="Ali"
        )

        result = await client(ImportContactsRequest([contact]))

        if result.users:
            await client(DeleteContactsRequest(id=result.users))
            return "NoLimit"
        else:
            await client(DeleteContactsRequest(id=result.users))
            return "Limited"

    except RPCError as e:
        if "FROZEN" in str(e).upper():
            return "FROZEN"
        logging.error(f"check_contact_limit RPC error: {e}")
        return "UNKNOWN"
    except Exception as e:
        logging.exception(f"check_contact_limit unexpected error: {e}")
        return "UNKNOWN"


async def check_session_alive(client: TelegramClient) -> str:
    """
    Check if a session is still authorized (alive).
    Returns: "Live" or "Die".
    """
    try:
        if await client.is_user_authorized():
            return "Live"
        else:
            return "Die"
    except RPCError as e:
        logging.error(f"check_session_alive RPC error: {e}")
        return "Die"
    except Exception as e:
        logging.exception(f"check_session_alive unexpected error: {e}")
        return "Die"


async def check_frozen(client: TelegramClient) -> bool:
    """
    Check if the account is frozen by attempting an active operation.
    Passive methods (get_me, is_user_authorized) work on frozen accounts,
    so we use UpdateProfileRequest (no-op with empty args) as an active probe.
    Returns True if frozen, False if not.
    """
    try:
        await client(functions.account.UpdateProfileRequest())
        return False
    except RPCError as e:
        if "FROZEN" in str(e).upper():
            return True
        # Other RPC errors are not frozen-related, re-raise
        raise
    except Exception:
        raise


async def get_last_otp(client: TelegramClient) -> tuple[bool, str]:
    """
    Fetch the last OTP/verification code from Telegram service messages.
    Looks in messages from user 777000 (Telegram) for codes.
    Returns (found: bool, code_or_message: str).
    """
    try:
        # Try getting messages from Telegram service (user ID 777000)
        codes_found = []
        try:
            async for msg in client.iter_messages(777000, limit=15):
                if msg.message:
                    # Look for numeric codes (4-8 digits)
                    import re
                    matches = re.findall(r'\b(\d{4,8})\b', msg.message)
                    if matches:
                        codes_found.append({
                            "code": matches[0],
                            "date": msg.date.strftime("%Y-%m-%d %H:%M:%S") if msg.date else "Unknown"
                        })
        except Exception:
            pass

        if codes_found:
            latest = codes_found[0]
            return True, (
                f"🔑 <b>Code:</b> <code>{latest['code']}</code>\n"
                f"📅 <b>Date:</b> {latest['date']}\n\n"
            )

        return False, "📭 No verification codes found."

    except Exception as e:
        logging.exception("get_last_otp error")
        return False, f"❌ Error: {type(e).__name__}: {e}"

