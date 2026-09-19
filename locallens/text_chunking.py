"""Structure-aware, lossless text splitting for independent translations."""

from __future__ import annotations

import re
from collections.abc import Iterator


_PARAGRAPH_BOUNDARY = re.compile(r"(?:\r\n|\r|\n)(?:[ \t]*(?:\r\n|\r|\n))+")
_LINE_BOUNDARY = re.compile(r"\r\n|\r|\n")
_SENTENCE_BOUNDARY = re.compile(
    r"(?:[。！？]+[ \t]*|[.!?]+(?:[\"'”’）)\]]*)(?:[ \t]+|(?=$)))"
)
_TOKEN_BOUNDARY = re.compile(r"[ \t]+")
_BOUNDARIES = (
    _PARAGRAPH_BOUNDARY,
    _LINE_BOUNDARY,
    _SENTENCE_BOUNDARY,
    _TOKEN_BOUNDARY,
)


def _segments_ending_at_matches(text: str, pattern: re.Pattern[str]) -> list[str]:
    segments: list[str] = []
    start = 0
    for match in pattern.finditer(text):
        end = match.end()
        if end > start:
            segments.append(text[start:end])
        start = end
    if start < len(text):
        segments.append(text[start:])
    return segments or [text]


def _split_unit(text: str, max_chars: int, level: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    if level >= len(_BOUNDARIES):
        return [
            text[index : index + max_chars]
            for index in range(0, len(text), max_chars)
        ]

    units = _segments_ending_at_matches(text, _BOUNDARIES[level])
    if len(units) == 1:
        return _split_unit(text, max_chars, level + 1)

    pieces: list[str] = []
    for unit in units:
        pieces.extend(_split_unit(unit, max_chars, level + 1))
    return _pack(pieces, max_chars)


def _pack(pieces: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not piece:
            continue
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current)
            current = ""
        if len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                piece[index : index + max_chars]
                for index in range(0, len(piece), max_chars)
            )
        else:
            current += piece
    if current:
        chunks.append(current)
    return chunks


def split_text(text: str, max_chars: int) -> list[str]:
    """Split losslessly by paragraph, line, sentence, token, then character."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    if not text:
        return []
    return _split_unit(text, max_chars, 0)


def translatable_parts(chunk: str) -> tuple[str, str, str]:
    """Separate outer whitespace so translated chunks retain source structure."""

    if not chunk:
        return "", "", ""
    leading_match = re.match(r"\s*", chunk)
    trailing_match = re.search(r"\s*$", chunk)
    leading_end = leading_match.end() if leading_match is not None else 0
    trailing_start = trailing_match.start() if trailing_match is not None else len(chunk)
    if leading_end >= trailing_start:
        return chunk, "", ""
    return chunk[:leading_end], chunk[leading_end:trailing_start], chunk[trailing_start:]


def iter_translatable_parts(
    text: str,
    max_chars: int,
) -> Iterator[tuple[str, str, str]]:
    for chunk in split_text(text, max_chars):
        yield translatable_parts(chunk)
