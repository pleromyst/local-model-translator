"""Local rules that can resolve a translation action without Ollama."""

from __future__ import annotations

import unicodedata


def _language_flags(text: str) -> tuple[bool, bool]:
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    has_chinese = False
    has_english = False
    for character in text:
        codepoint = ord(character)
        if (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
        ):
            has_chinese = True
        elif character.isalpha() and "LATIN" in unicodedata.name(character, ""):
            has_english = True

    return has_chinese, has_english


def is_mixed_chinese_english(text: str) -> bool:
    """Return whether text contains both a Han ideograph and a Latin letter."""

    has_chinese, has_english = _language_flags(text)
    return has_chinese and has_english


def translation_target_for_source(
    text: str,
    *,
    fallback: str,
) -> str | None:
    """Choose a deterministic direction, or ``None`` for mixed-text bypass."""

    if not isinstance(fallback, str):
        raise TypeError("fallback must be a string")
    has_chinese, has_english = _language_flags(text)
    if has_chinese and has_english:
        return None
    if has_chinese:
        return "English"
    if has_english:
        return "Simplified Chinese"
    return fallback
