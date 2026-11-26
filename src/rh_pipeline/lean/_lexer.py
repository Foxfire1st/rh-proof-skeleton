"""Delimiter- and comment-aware Lean 4 lexer for closed-world extraction."""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass
from typing import NoReturn

_OPEN_DELIMS = {"(": ")", "[": "]", "{": "}", "⦃": "⦄"}
CLOSE_DELIMS = frozenset(_OPEN_DELIMS.values())

_WS_RE = re.compile(r"\s+")
_STR_RE = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_RAW_STR_HEAD_RE = re.compile(r'r(#+)"')
_CHAR_LIT_RE = re.compile(
    r"'(?:[^'\\\n]|\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{[0-9a-fA-F]+\}|[^\n]))'"
)


class UnsupportedLeanFormError(ValueError):
    """Raised when a Lean source file uses syntax outside the closed-world grammar."""

    def __init__(self, rel_path: str, line: int, form: str) -> None:
        self.rel_path = rel_path
        self.line = line
        self.form = form
        super().__init__(f"{rel_path}:{line}:{form}")


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    text: str
    start: int
    end: int
    line: int
    end_line: int
    col: int
    depth: int


def _is_id_first(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_id_rest(ch: str) -> bool:
    return ch in "_'?!" or ch.isalnum() or unicodedata.category(ch) in {"No", "Mn", "Mc"}


def split_dotted_name(name: str) -> list[str]:
    parts: list[str] = []
    start, in_guil = 0, False
    for idx, ch in enumerate(name):
        if ch in "«»":
            in_guil = ch == "«"
        elif ch == "." and not in_guil:
            parts.append(name[start:idx])
            start = idx + 1
    parts.append(name[start:])
    return parts


def _find_block_comment_end(src: str, pos: int) -> int:
    nest, end = 1, len(src)
    while pos < end and nest > 0:
        pair = src[pos : pos + 2]
        if pair in {"/-", "-/"}:
            nest += 1 if pair == "/-" else -1
            pos += 2
        else:
            pos += 1
    return pos if nest == 0 else -1


class LeanLexer:
    """Delimiter- and comment-aware Lean 4 lexer."""

    def __init__(self, src: str, path: str) -> None:
        self._src, self._n, self._path = src, len(src), path
        self._lines = [0, *(m.end() for m in re.finditer("\n", src))]
        self._pos = 0
        self._delims: list[tuple[str, int]] = []
        self._toks: list[Token] = []

    def _line_col(self, pos: int) -> tuple[int, int]:
        line = bisect.bisect_right(self._lines, pos)
        return line, pos - self._lines[line - 1]

    def _fail(self, pos: int, form: str) -> NoReturn:
        raise UnsupportedLeanFormError(self._path, self._line_col(pos)[0], form)

    def _finish(self, kind: str, target: int) -> None:
        pos, depth = self._pos, len(self._delims)
        line, col = self._line_col(pos)
        end_line = self._line_col(target - 1)[0]
        self._toks.append(
            Token(kind, self._src[pos:target], pos, target, line, end_line, col, depth)
        )
        self._pos = target

    def tokenize(self) -> list[Token]:
        while self._pos < self._n:
            if ws := _WS_RE.match(self._src, self._pos):
                self._pos = ws.end()
                continue
            self._scan_next()
        if self._delims:
            raise UnsupportedLeanFormError(self._path, self._delims[-1][1], "unclosed-delimiter")
        return self._toks

    def _scan_next(self) -> None:
        ch = self._src[self._pos]
        if self._scan_comment() or self._scan_literal(ch):
            return
        if ch == "«" or _is_id_first(ch):
            self._scan_ident()
        elif ch in _OPEN_DELIMS or ch in CLOSE_DELIMS:
            self._scan_delim(ch)
        elif self._src[self._pos : self._pos + 2] in {":=", "=>"}:
            self._finish("op", self._pos + 2)
        else:
            self._finish("op" if ch in {":", "|", "@", ","} else "other", self._pos + 1)

    def _scan_comment(self) -> bool:
        pos = self._pos
        pair = self._src[pos : pos + 2]
        if pair == "--":
            nl = self._src.find("\n", pos + 2)
            self._pos = self._n if nl == -1 else nl
            return True
        if pair != "/-":
            return False
        is_doc = (
            self._src[pos : pos + 3] == "/--"
            and pos + 3 < self._n
            and self._src[pos + 3] not in "-/"
        )
        end = _find_block_comment_end(self._src, pos + (3 if is_doc else 2))
        if end == -1:
            self._fail(pos, "unclosed-block-comment")
        if is_doc:
            self._finish("doc", end)
        else:
            self._pos = end
        return True

    def _scan_literal(self, ch: str) -> bool:
        if ch == '"':
            match = _STR_RE.match(self._src, self._pos)
            self._finish("lit", match.end() if match else self._fail(self._pos, "unclosed-string"))
            return True
        if ch == "r" and (head := _RAW_STR_HEAD_RE.match(self._src, self._pos)):
            closer = '"' + head.group(1)
            end = self._src.find(closer, head.end())
            target = (
                end + len(closer) if end != -1 else self._fail(self._pos, "unclosed-raw-string")
            )
            self._finish("lit", target)
            return True
        if ch == "'" and (char_match := _CHAR_LIT_RE.match(self._src, self._pos)):
            self._finish("lit", char_match.end())
            return True
        return False

    def _scan_ident(self) -> None:
        pos = self._scan_ident_part(self._pos)
        while pos + 1 < self._n and self._src[pos] == ".":
            nxt = self._src[pos + 1]
            if nxt != "«" and not _is_id_first(nxt):
                break
            pos = self._scan_ident_part(pos + 1)
        self._finish("ident", pos)

    def _scan_ident_part(self, pos: int) -> int:
        if self._src[pos] == "«":
            end = self._src.find("»", pos + 1)
            return end + 1 if end != -1 else self._fail(pos, "unclosed-guillemet")
        pos += 1
        while pos < self._n and _is_id_rest(self._src[pos]):
            pos += 1
        return pos

    def _scan_delim(self, ch: str) -> None:
        if ch in _OPEN_DELIMS:
            self._finish("delim", self._pos + 1)
            self._delims.append((_OPEN_DELIMS[ch], self._toks[-1].line))
            return
        if not self._delims or self._delims[-1][0] != ch:
            self._fail(self._pos, "unbalanced-delimiter")
        self._delims.pop()
        self._finish("delim", self._pos + 1)
