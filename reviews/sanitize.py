"""Shared text hygiene helpers for user-supplied content.

Review bodies are rendered through Django's autoescaping, but control
characters are still stripped before they reach storage so they can never
corrupt logs, exports or the moderation UI.  Length limits are re-exported
from ``constants`` so the form, the model and the repositories agree.
"""
import re

from .constants import MAX_COMMENT_LENGTH as MAX_COMMENT_LENGTH
from .constants import MIN_COMMENT_LENGTH as MIN_COMMENT_LENGTH

# C0/C1 control characters, keeping tab (\x09), LF (\x0a) and CR (\x0d).
_CONTROL_CHARS_RE: re.Pattern[str] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def strip_control_chars(value: str) -> str:
    """Remove C0/C1 control characters while preserving tab and newlines."""
    if not value:
        return ""
    return _CONTROL_CHARS_RE.sub("", value)


def clean_text(value: str) -> str:
    """Normalize newlines, drop control characters, trim surrounding space."""
    if not value:
        return ""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return strip_control_chars(normalized).strip()
