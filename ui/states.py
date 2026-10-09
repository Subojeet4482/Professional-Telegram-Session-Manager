"""
FSM States for the bot conversations.
"""
from aiogram.fsm.state import State, StatesGroup


class RegState(StatesGroup):
    """Registration flow states."""
    phone = State()       # Waiting for phone number
    code = State()        # Waiting for verification code
    password = State()    # Waiting for 2FA password


class ProxyState(StatesGroup):
    """Proxy editing states."""
    waiting = State()     # Waiting for new proxy string


class ApiState(StatesGroup):
    """API settings editing states."""
    api_id = State()      # Waiting for new API ID
    api_hash = State()    # Waiting for new API Hash


class SplitState(StatesGroup):
    """Split export states."""
    count = State()       # Waiting for number of sessions to extract


class SearchState(StatesGroup):
    """Search phone states."""
    phone = State()       # Waiting for phone number to search


class BulkActionState(StatesGroup):
    """Bulk action (specific phones) states."""
    phones = State()      # Waiting for phone list (message or .txt file)


class ImportState(StatesGroup):
    """Import sessions state."""
    waiting_for_file = State()
    action_choice = State()  # Choose between save only or fetch OTPs


class ConvertState(StatesGroup):
    """Convert sessions/tdata state."""
    waiting_file = State()      # Waiting for the uploaded .zip / .session
    waiting_password = State()  # Waiting for the 2FA password to attach
    running = State()           # Batch in progress (blocks re-entry)


class CheckState(StatesGroup):
    """Check sessions state."""
    waiting_for_zip = State()      # Waiting for ZIP file to check (single type)
    waiting_for_all_zip = State()  # Waiting for ZIP file to check (all 3 types)


class ProfileApplyState(StatesGroup):
    """Apply profile to sessions state."""
    waiting_for_zip = State()  # Waiting for ZIP file to apply profile on

class TwoFactorState(StatesGroup):
    """Setting 2FA password state."""
    waiting_password = State()  # Waiting for new 2FA password (Set)
    waiting_update_password = State() # Waiting for current and new password (Update)
    waiting_delete_password = State() # Waiting for current password (Delete)

class Bulk2FAState(StatesGroup):
    """Bulk 2FA password state."""
    waiting_action = State() # Set, Update, Delete
    waiting_zip = State()
    waiting_password = State()
    waiting_fallback_password = State()

class Auto2FAState(StatesGroup):
    """Auto 2FA state."""
    waiting_password = State()

class ReadOTPState(StatesGroup):
    """Read OTP quick access states."""
    waiting_phone = State()  # Waiting for phone number to search & read OTP


# ProfileState removed — profile options are now pure toggles (no user input needed)
