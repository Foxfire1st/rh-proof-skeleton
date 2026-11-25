"""Closed-world Lean 4 top-level declaration extractor."""

from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, NoReturn, cast

from rh_pipeline.models.lean import (
    VALID_DECLARATION_KINDS,
    ExtractedModule,
    LeanDeclaration,
    LeanDeclarationKind,
)

_OPEN_DELIMS = {"(": ")", "[": "]", "{": "}", "⦃": "⦄"}
_CLOSE_DELIMS = frozenset(_OPEN_DELIMS.values())
_MODIFIERS = frozenset({"public", "private", "protected", "noncomputable", "partial", "unsafe"})
_FORBIDDEN_MODS = frozenset({"local", "scoped"})
_DERIVING_KINDS = frozenset({"structure", "inductive", "class"})
_NO_BODY_KINDS = _DERIVING_KINDS | {"axiom"}
_FORBIDDEN_BODY = frozenset({"termination_by", "decreasing_by"})
_NON_NAME = frozenset({"where", "by", "deriving", "extends", "with", "in", "end"})

_WS_RE = re.compile(r"\s+")
_STR_RE = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_RAW_STR_HEAD_RE = re.compile(r'r(#+)"')
_CHAR_LIT_RE = re.compile(
    r"'(?:[^'\\\n]|\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{[0-9a-fA-F]+\}|[^\n]))'"
)
_FIRST_LINE_BREAK_RE = re.compile(r"^[ \t]*\n")


class UnsupportedLeanFormError(ValueError):
    """Raised when a Lean source file uses syntax outside the closed-world grammar."""

    def __init__(self, rel_path: str, line: int, form: str) -> None:
        self.rel_path = rel_path
        self.line = line
        self.form = form
        super().__init__(f"{rel_path}:{line}:{form}")


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    start: int
    end: int
    line: int
    end_line: int
    col: int
    depth: int


type _ScopeFrame = tuple[Literal["namespace", "section"], str | None, int]


def _is_id_first(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_id_rest(ch: str) -> bool:
    return ch in "_'?!" or ch.isalnum() or unicodedata.category(ch) in {"No", "Mn", "Mc"}


def _split_dotted_name(name: str) -> list[str]:
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


def _normalize_body(raw_body: str) -> str:
    match = _FIRST_LINE_BREAK_RE.match(raw_body)
    return raw_body[match.end() :].rstrip() if match else raw_body.lstrip(" \t").rstrip()


def _qualify_sig(src: str, start: int, end: int, name_tok: _Token, qualified: str) -> str:
    return (src[start : name_tok.start] + qualified + src[name_tok.end : end]).rstrip()


class _Lexer:
    """Delimiter- and comment-aware Lean 4 lexer."""

    def __init__(self, src: str, path: str) -> None:
        self._src, self._n, self._path = src, len(src), path
        self._lines = [0, *(m.end() for m in re.finditer("\n", src))]
        self._pos = 0
        self._delims: list[tuple[str, int]] = []
        self._toks: list[_Token] = []

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
            _Token(kind, self._src[pos:target], pos, target, line, end_line, col, depth)
        )
        self._pos = target

    def tokenize(self) -> list[_Token]:
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
        elif ch in _OPEN_DELIMS or ch in _CLOSE_DELIMS:
            self._scan_delim(ch)
        elif self._src[self._pos : self._pos + 2] == ":=":
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


class _Parser:
    """Closed-world top-level Lean 4 parser."""

    def __init__(self, src: str, toks: list[_Token], path: str, commit: str) -> None:
        self._src, self._toks, self._n = src, toks, len(toks)
        self._path, self._commit = path, commit
        self._idx = 0
        self._imports: set[str] = set()
        self._scopes: list[_ScopeFrame] = []
        self._decls: list[LeanDeclaration] = []

    def _fail(self, line: int, form: str) -> NoReturn:
        raise UnsupportedLeanFormError(self._path, line, form)

    def _peek_ident(self, expected: str) -> bool:
        nxt = self._idx + 1
        return (
            nxt < self._n and self._toks[nxt].kind == "ident" and self._toks[nxt].text == expected
        )

    def parse(self) -> ExtractedModule:
        while self._idx < self._n:
            self._parse_top_level()
        for kind, _, line in self._scopes:
            if kind == "namespace":
                self._fail(line, "unbalanced-namespace")
        return ExtractedModule(
            self._path, self._commit, tuple(sorted(self._imports)), tuple(self._decls)
        )

    def _parse_top_level(self) -> None:
        start_idx = self._idx
        has_doc = self._toks[self._idx].kind == "doc"
        self._idx += int(has_doc)
        attrs = self._consume_attributes()
        if self._idx >= self._n:
            self._fail(self._toks[start_idx].line, "orphan-doc-or-attribute")
        if not has_doc and self._try_structural(attrs):
            return
        self._parse_declaration(start_idx)

    def _try_structural(self, attrs: list[tuple[int, int]]) -> bool:
        cur = self._toks[self._idx]
        if cur.kind != "ident":
            return False
        if attrs:
            s, e = attrs[0]
            ok = len(attrs) == 1 and e - s == 4 and self._toks[s + 2].text == "expose"
            return ok and self._try_section(cur)
        return self._try_section(cur) or self._try_header(cur)

    def _try_section(self, cur: _Token) -> bool:
        if cur.text == "public" and self._peek_ident("section"):
            self._idx += 1
        elif cur.text != "section":
            return False
        self._parse_scope_open("section", opt=True)
        return True

    def _try_header(self, cur: _Token) -> bool:
        word = cur.text
        if word == "module":
            self._idx += 1
            if self._idx < self._n and self._toks[self._idx].line == cur.line:
                self._fail(cur.line, "module")
        elif word == "import" or (word == "public" and self._peek_ident("import")):
            self._idx += 2 if word == "public" else 1
            self._imports.add(self._take_line_ident(cur.line, "import"))
        elif word == "namespace":
            self._parse_scope_open("namespace", opt=False)
        elif word == "end":
            self._parse_end()
        else:
            return False
        return True

    def _at_attr_start(self) -> bool:
        if self._idx + 1 >= self._n:
            return False
        a, b = self._toks[self._idx], self._toks[self._idx + 1]
        return a.text == "@" and b.text == "[" and a.end == b.start

    def _consume_attributes(self) -> list[tuple[int, int]]:
        attrs: list[tuple[int, int]] = []
        while self._at_attr_start():
            attr_start = self._idx
            self._idx += 2
            while self._idx < self._n and self._toks[self._idx].depth > 0:
                tok = self._toks[self._idx]
                if tok.depth == 1 and tok.kind == "ident" and tok.text in _FORBIDDEN_MODS:
                    self._fail(tok.line, tok.text)
                self._idx += 1
            self._idx += 1
            attrs.append((attr_start, self._idx))
        return attrs

    def _take_line_ident(self, line: int, form: str) -> str:
        tok = self._toks[self._idx] if self._idx < self._n else None
        if tok is None or tok.line != line or tok.kind != "ident":
            self._fail(line, form)
        self._idx += 1
        if self._idx < self._n and self._toks[self._idx].line == line:
            self._fail(line, form)
        return tok.text

    def _parse_scope_open(self, kind: Literal["namespace", "section"], *, opt: bool) -> None:
        line = self._toks[self._idx].line
        self._idx += 1
        if opt and (self._idx >= self._n or self._toks[self._idx].line != line):
            self._scopes.append((kind, None, line))
            return
        names = _split_dotted_name(self._take_line_ident(line, kind))
        self._scopes.extend((kind, seg, line) for seg in names)

    def _parse_end(self) -> None:
        line = self._toks[self._idx].line
        self._idx += 1
        if self._idx >= self._n or self._toks[self._idx].line != line:
            if not self._scopes:
                self._fail(line, "stray-end")
            self._scopes.pop()
            return
        for seg in reversed(_split_dotted_name(self._take_line_ident(line, "mismatched-end"))):
            if not self._scopes or self._scopes[-1][1] != seg:
                self._fail(line, "mismatched-end")
            self._scopes.pop()

    def _parse_declaration(self, start_idx: int) -> None:
        while self._idx < self._n and self._toks[self._idx].kind == "ident":
            word = self._toks[self._idx].text
            if word in _FORBIDDEN_MODS:
                self._fail(self._toks[self._idx].line, word)
            if word not in _MODIFIERS:
                break
            self._idx += 1
        if self._idx >= self._n:
            self._fail(self._toks[start_idx].line, "orphan-modifier")
        kw = self._toks[self._idx]
        if kw.kind != "ident" or kw.text not in VALID_DECLARATION_KINDS:
            self._fail(kw.line, kw.text if kw.kind == "ident" else f"unexpected-{kw.text}")
        self._extract_declaration(start_idx, self._idx, cast(LeanDeclarationKind, kw.text))

    def _extract_declaration(self, start_idx: int, kw_idx: int, kind: LeanDeclarationKind) -> None:
        end_idx = self._find_decl_end(kw_idx, kind)
        name_tok = self._require_decl_name(kw_idx, end_idx, kind)
        for pos in range(kw_idx + 1, end_idx):
            tok = self._toks[pos]
            if tok.depth == 0 and tok.kind == "ident" and tok.text in _FORBIDDEN_BODY:
                self._fail(tok.line, tok.text)
        ns = [name for k, name, _ in self._scopes if k == "namespace" and name is not None]
        qualified = ".".join([*ns, name_tok.text]) if ns else name_tok.text
        sig, body = self._split_sig_body(start_idx, kw_idx, end_idx, name_tok, qualified)
        self._decls.append(
            LeanDeclaration(
                name=qualified,
                kind=kind,
                signature=sig,
                body=body,
                start_line=self._toks[start_idx].line,
                end_line=self._toks[end_idx - 1].end_line,
                upstream_path=self._path,
                upstream_commit=self._commit,
                body_lines=0 if body == "" else max(1, len(body.strip().splitlines())),
                body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            )
        )
        self._idx = end_idx

    def _find_decl_end(self, kw_idx: int, kind: LeanDeclarationKind) -> int:
        kw_line, pos = self._toks[kw_idx].line, kw_idx + 1
        while pos < self._n:
            tok = self._toks[pos]
            at_col0 = tok.depth == 0 and tok.col == 0 and tok.line > kw_line
            if at_col0 and not self._is_col0_continuation(pos, kind):
                break
            pos += 1
        return pos

    def _is_col0_continuation(self, pos: int, kind: LeanDeclarationKind) -> bool:
        tok = self._toks[pos]
        if tok.text in {":", ":="}:
            return True
        if kind not in _DERIVING_KINDS:
            return False
        if tok.text in {"|", "where"}:
            return True
        if tok.text != "deriving":
            return False
        nxt = self._toks[pos + 1] if pos + 1 < self._n else None
        return not (nxt and nxt.kind == "ident" and nxt.text == "instance")

    def _require_decl_name(self, kw_idx: int, end_idx: int, kind: LeanDeclarationKind) -> _Token:
        tok = self._toks[kw_idx + 1] if kw_idx + 1 < end_idx else None
        if tok is not None and tok.kind == "ident" and tok.text not in _NON_NAME:
            return tok
        form = "anonymous-instance" if kind == "instance" else f"unnamed-{kind}"
        self._fail(self._toks[kw_idx].line, form)

    def _split_sig_body(
        self, start_idx: int, kw_idx: int, end_idx: int, name_tok: _Token, qualified: str
    ) -> tuple[str, str]:
        kind = self._toks[kw_idx].text
        s_pos, e_pos = self._toks[start_idx].start, self._toks[end_idx - 1].end
        assign = (
            None if kind in _NO_BODY_KINDS else self._find_top_level_assign(kw_idx + 2, end_idx)
        )
        if assign is None:
            if kind in _NO_BODY_KINDS or kind == "opaque":
                return _qualify_sig(self._src, s_pos, e_pos, name_tok, qualified), ""
            self._fail(self._toks[kw_idx].line, f"missing-body-{kind}")
        sig = _qualify_sig(self._src, s_pos, assign.start, name_tok, qualified)
        body = _normalize_body(self._src[assign.end : e_pos])
        if not body:
            self._fail(self._toks[kw_idx].line, f"empty-body-{kind}")
        return sig, body

    def _find_top_level_assign(self, start_idx: int, end_idx: int) -> _Token | None:
        for pos in range(start_idx, end_idx):
            tok = self._toks[pos]
            if tok.depth != 0:
                continue
            if tok.kind == "op" and tok.text == ":=":
                return tok
            if tok.kind == "ident" and tok.text == "where":
                return None
        return None


def extract_declarations(source: str, *, rel_path: str, commit: str) -> ExtractedModule:
    """Extract top-level Lean 4 imports and declarations under the closed-world grammar."""
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens = _Lexer(normalized, rel_path).tokenize()
    return _Parser(normalized, tokens, rel_path, commit).parse()
