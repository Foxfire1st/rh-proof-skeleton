"""Neutral low-level Markdown code/math/directive masking primitives.

This module sits below both `rh_pipeline.validate` and `rh_pipeline.site` in the
import graph and preserves exact character offsets and line numbers (same-length
masking across LF and CRLF inputs).
"""

from __future__ import annotations

import re

__all__ = [
    "_BACKTICK_RUN_RE",
    "_DISPLAY_MATH_END_RE",
    "_FENCE_CLOSE_RE",
    "_FENCE_OPEN_RE",
    "_INLINE_MATH_END_RE",
    "blank_span",
    "find_directive_close",
    "find_math_end",
    "mask_code_spans",
    "mask_fenced_code",
    "mask_inline_code",
]

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*\r?$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_DISPLAY_MATH_END_RE = re.compile(r"\\.|(\$\$)", re.DOTALL)
_INLINE_MATH_END_RE = re.compile(r"\\.|(\r?\n\r?\n|\$)", re.DOTALL)
_DIR_TOK_RE = re.compile(r"\\.|(\$|[{}])", re.DOTALL)
_PARA_BREAK_RE = re.compile(r"\r?\n\r?\n")


def blank_span(buf: list[str], start: int, end: int) -> None:
    """Replace non-newline characters in `buf[start:end]` with spaces in place."""
    buf[start:end] = [ch if ch in ("\n", "\r") else " " for ch in buf[start:end]]


def _blank_line(line: str) -> str:
    if line.endswith("\r"):
        return " " * (len(line) - 1) + "\r"
    return " " * len(line)


def mask_fenced_code(text: str) -> str:
    """Return a same-length copy of `text` with fenced code blocks replaced by spaces."""
    out: list[str] = []
    fence_char, fence_len = "", 0
    for line in text.split("\n"):
        if fence_char:
            out.append(_blank_line(line))
            cm = _FENCE_CLOSE_RE.match(line)
            if cm and cm.group(1)[0] == fence_char and len(cm.group(1)) >= fence_len:
                fence_char, fence_len = "", 0
            continue
        if (m := _FENCE_OPEN_RE.match(line)) and (m.group(1)[0] == "~" or "`" not in m.group(2)):
            fence_char, fence_len = m.group(1)[0], len(m.group(1))
            out.append(_blank_line(line))
        else:
            out.append(line)
    return "\n".join(out)


def mask_inline_code(text: str) -> str:
    """Return a same-length copy of `text` with balanced inline backticks replaced by spaces."""
    buf, runs, idx = list(text), list(_BACKTICK_RUN_RE.finditer(text)), 0
    while idx < len(runs):
        opener = runs[idx]
        count = opener.end() - opener.start()
        for j in range(idx + 1, len(runs)):
            if _PARA_BREAK_RE.search(text, opener.end(), runs[j].start()) is not None:
                break
            if runs[j].end() - runs[j].start() == count:
                blank_span(buf, opener.start(), runs[j].end())
                idx = j
                break
        idx += 1
    return "".join(buf)


def mask_code_spans(text: str) -> str:
    """Return a same-length copy of `text` with fenced and inline code replaced by spaces."""
    return mask_inline_code(mask_fenced_code(text))


def find_math_end(text: str, start: int) -> int | None:
    """Return the end offset of a `$...$` or `$$...$$` span starting at `start`, or `None`."""
    is_display = text.startswith("$$", start)
    pattern = _DISPLAY_MATH_END_RE if is_display else _INLINE_MATH_END_RE
    close = "$$" if is_display else "$"
    for match in pattern.finditer(text, start + len(close)):
        tok = match.group(1)
        if tok == close:
            return match.end()
        if tok is not None:
            return None
    return None


def find_directive_close(text: str, start: int) -> int | None:
    """Return the index of the closing `}}` for a directive body starting at `start`, or `None`."""
    pos, brace_depth = start, 0
    while (match := _DIR_TOK_RE.search(text, pos)) is not None:
        tok = match.group(1)
        if tok == "$":
            pos = find_math_end(text, match.start()) or match.end()
        elif tok == "{":
            brace_depth, pos = brace_depth + 1, match.end()
        elif tok == "}" and brace_depth > 0:
            brace_depth, pos = brace_depth - 1, match.end()
        elif tok == "}" and text.startswith("}}", match.start()):
            return match.start()
        else:
            pos = match.end()
    return None
