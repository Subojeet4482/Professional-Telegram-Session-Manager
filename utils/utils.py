"""
utils.py — Shared helper functions for handlers.

smart_edit: Edits the original message in place.
            Falls back to sending a new message if editing fails.
"""

import logging

logger = logging.getLogger("utils")


async def smart_edit(message, text: str, **kwargs):
    """
    Edit the message in place using edit_text().

    Falls back to message.answer() if editing is not possible
    (e.g. message is too old, contains media, or not owned by bot).

    Args:
        message: The aiogram Message object to edit.
        text:    New message text.
        **kwargs: Any keyword args accepted by message.edit_text()
                  (reply_markup, parse_mode, etc.)

    Returns:
        The edited or new Message object.
    """
    try:
        return await message.edit_text(text, **kwargs)
    except Exception:
        # Fallback: send a new message if edit fails
        return await message.answer(text, **kwargs)
