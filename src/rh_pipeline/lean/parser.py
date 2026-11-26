"""Closed-world Lean 4 top-level declaration extractor."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal, NoReturn, cast

from rh_pipeline.lean._lexer import (
    CLOSE_DELIMS,
    LeanLexer,
    Token,
    UnsupportedLeanFormError,
    split_dotted_name,
)
from rh_pipeline.models.lean import (
    VALID_DECLARATION_KINDS,
    ExtractedModule,
    LeanDeclaration,
    LeanDeclarationKind,
)

__all__ = ["UnsupportedLeanFormError", "extract_declarations"]

_MODIFIERS = frozenset({"public", "private", "protected", "noncomputable", "partial", "unsafe"})
_SECTION_MODS = frozenset({"public", "noncomputable"})
_SCOPED_PREFIX_KW = frozenset({"open", "omit", "seal", "unseal", "variable", "include"})
_STANDALONE_CMD_KW = frozenset(
    {"open", "variable", "include", "omit", "export", "universe", "attribute", "grind_pattern"}
)
_POST_SCOPED_CMD_KW = frozenset({"attribute", "grind_pattern"})
_DERIVING_KINDS = frozenset({"structure", "inductive", "class"})
_NO_BODY_KINDS = _DERIVING_KINDS | {"axiom"}
_BODY_KINDS = frozenset({"def", "abbrev", "theorem", "lemma", "instance"})
_BAR_CONTINUATION_KINDS = _DERIVING_KINDS | _BODY_KINDS
_ALWAYS_COL0_CONT = frozenset({":", ":="}) | CLOSE_DELIMS
_FORBIDDEN_BODY = frozenset({"termination_by", "decreasing_by"})
_NON_NAME = frozenset({"where", "by", "deriving", "extends", "with", "in", "end"})

_FIRST_LINE_BREAK_RE = re.compile(r"^[ \t]*\n")

_Token = Token
type _ScopeFrame = tuple[Literal["namespace", "section"], str | None, int]


@dataclass(frozen=True, slots=True)
class _DeclPrefix:
    start_idx: int | None
    has_doc: bool
    attrs: tuple[tuple[int, int], ...]
    first_scoped_line: int | None
    skipped_spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _DeclSpan:
    start_idx: int
    kw_idx: int
    end_idx: int
    skipped_spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _DeclTarget:
    name_tok: _Token | None
    qualified: str


def _normalize_body(raw_body: str) -> str:
    match = _FIRST_LINE_BREAK_RE.match(raw_body)
    return raw_body[match.end() :].rstrip() if match else raw_body.lstrip(" \t").rstrip()


def _apply_sig_edits(src: str, start: int, end: int, edits: list[tuple[int, int, str]]) -> str:
    parts: list[str] = []
    cursor = start
    for edit_start, edit_end, replacement in edits:
        parts.append(src[cursor:edit_start])
        parts.append(replacement)
        cursor = edit_end
    parts.append(src[cursor:end])
    return "".join(parts).rstrip()


class _Parser:
    """Closed-world top-level Lean 4 parser."""

    def __init__(self, src: str, toks: list[_Token], path: str, commit: str) -> None:
        self._src, self._toks, self._n = src, toks, len(toks)
        self._path, self._commit = path, commit
        self._idx = 0
        self._imports: set[str] = set()
        self._scopes: list[_ScopeFrame] = []
        self._decls: list[LeanDeclaration] = []
        self._anon_instance_count = 0

    def _fail(self, line: int, form: str) -> NoReturn:
        raise UnsupportedLeanFormError(self._path, line, form)

    def _peek_ident(self, expected: str) -> bool:
        nxt = self._idx + 1
        return (
            nxt < self._n and self._toks[nxt].kind == "ident" and self._toks[nxt].text == expected
        )

    def _is_line_initial(self, pos: int) -> bool:
        return pos == 0 or self._toks[pos - 1].end_line < self._toks[pos].line

    def _is_new_top_level_tok(self, tok: _Token, start_line: int) -> bool:
        return (
            tok.depth == 0
            and tok.col == 0
            and tok.line > start_line
            and not (tok.kind == "delim" and tok.text in CLOSE_DELIMS)
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
        prefix = self._consume_prefix()
        if self._idx >= self._n:
            if prefix.start_idx is not None:
                self._fail(self._toks[prefix.start_idx].line, "orphan-doc-or-attribute")
            assert prefix.first_scoped_line is not None
            self._fail(prefix.first_scoped_line, "orphan-scoped-prefix")
        if not prefix.has_doc and self._try_non_decl(prefix):
            return
        start_idx = self._idx if prefix.start_idx is None else prefix.start_idx
        self._parse_declaration(start_idx, prefix.skipped_spans)

    def _set_start(self, start_idx: int | None) -> int:
        return self._idx if start_idx is None else start_idx

    def _advance_scoped_prefix(
        self,
        tok: _Token,
        in_pos: int,
        start_idx: int | None,
        skipped_spans: list[tuple[int, int]],
    ) -> None:
        cut_start = tok.start - tok.col if self._is_line_initial(self._idx) else tok.start
        self._idx = in_pos + 1
        if start_idx is not None:
            skipped_spans.append((cut_start, self._idx))

    def _consume_prefix(self) -> _DeclPrefix:
        start_idx: int | None = None
        has_doc = False
        attrs: list[tuple[int, int]] = []
        first_scoped_line: int | None = None
        skipped_spans: list[tuple[int, int]] = []
        while self._idx < self._n:
            tok = self._toks[self._idx]
            if tok.kind == "doc" and not has_doc:
                start_idx = self._set_start(start_idx)
                has_doc = True
                self._idx += 1
            elif self._at_attr_start():
                start_idx = self._set_start(start_idx)
                attrs.extend(self._consume_attributes())
            elif (in_pos := self._try_scoped_prefix()) is not None:
                first_scoped_line = first_scoped_line or tok.line
                self._advance_scoped_prefix(tok, in_pos, start_idx, skipped_spans)
            else:
                break
        return _DeclPrefix(
            start_idx, has_doc, tuple(attrs), first_scoped_line, tuple(skipped_spans)
        )

    def _try_scoped_prefix(self) -> int | None:
        cur = self._toks[self._idx]
        if cur.kind != "ident" or cur.text not in _SCOPED_PREFIX_KW:
            return None
        pos = self._idx + 1
        while pos < self._n:
            tok = self._toks[pos]
            if tok.depth == 0:
                if tok.kind == "ident" and tok.text == "in" and pos > self._idx + 1:
                    return pos
                if self._is_new_top_level_tok(tok, cur.line):
                    return None
            pos += 1
        return None

    def _try_non_decl(self, prefix: _DeclPrefix) -> bool:
        cur = self._toks[self._idx]
        if cur.kind != "ident":
            return False
        if prefix.first_scoped_line is not None:
            return not prefix.attrs and self._try_admitted_command(cur, post_scoped=True)
        if prefix.attrs:
            return self._is_expose_only(prefix.attrs) and self._try_section()
        return self._try_section() or self._try_header(cur) or self._try_admitted_command(cur)

    def _is_expose_only(self, attrs: tuple[tuple[int, int], ...]) -> bool:
        if len(attrs) != 1:
            return False
        s, e = attrs[0]
        return e - s == 4 and self._toks[s + 2].text == "expose"

    def _try_section(self) -> bool:
        pos = self._idx
        while (
            pos < self._n
            and self._toks[pos].kind == "ident"
            and self._toks[pos].text in _SECTION_MODS
        ):
            pos += 1
        if pos >= self._n or self._toks[pos].kind != "ident" or self._toks[pos].text != "section":
            return False
        self._idx = pos
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

    def _try_admitted_command(self, cur: _Token, *, post_scoped: bool = False) -> bool:
        allowed = _POST_SCOPED_CMD_KW if post_scoped else _STANDALONE_CMD_KW
        if cur.text in allowed or (cur.text == "local" and self._peek_ident("grind_pattern")):
            self._skip_to_next_top_level(cur.line)
            return True
        return False

    def _skip_to_next_top_level(self, start_line: int) -> None:
        pos = self._idx + 1
        while pos < self._n and not self._is_new_top_level_tok(self._toks[pos], start_line):
            pos += 1
        self._idx = pos

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
        names = split_dotted_name(self._take_line_ident(line, kind))
        self._scopes.extend((kind, seg, line) for seg in names)

    def _parse_end(self) -> None:
        line = self._toks[self._idx].line
        self._idx += 1
        if self._idx >= self._n or self._toks[self._idx].line != line:
            if not self._scopes:
                self._fail(line, "stray-end")
            self._scopes.pop()
            return
        for seg in reversed(split_dotted_name(self._take_line_ident(line, "mismatched-end"))):
            if not self._scopes or self._scopes[-1][1] != seg:
                self._fail(line, "mismatched-end")
            self._scopes.pop()

    def _parse_declaration(
        self, start_idx: int, skipped_spans: tuple[tuple[int, int], ...]
    ) -> None:
        local_tok = self._consume_decl_modifiers()
        if self._idx >= self._n:
            if local_tok is not None:
                self._fail(local_tok.line, "local")
            self._fail(self._toks[start_idx].line, "orphan-modifier")
        kw = self._toks[self._idx]
        if kw.kind != "ident" or kw.text not in VALID_DECLARATION_KINDS:
            if local_tok is not None:
                self._fail(local_tok.line, "local")
            self._fail(kw.line, kw.text if kw.kind == "ident" else f"unexpected-{kw.text}")
        if local_tok is not None and kw.text != "instance":
            self._fail(local_tok.line, "local")
        end_idx = self._find_decl_end(self._idx, cast(LeanDeclarationKind, kw.text))
        span = _DeclSpan(start_idx, self._idx, end_idx, skipped_spans)
        self._extract_declaration(span, cast(LeanDeclarationKind, kw.text))

    def _consume_decl_modifiers(self) -> _Token | None:
        local_tok: _Token | None = None
        while self._idx < self._n and self._toks[self._idx].kind == "ident":
            tok = self._toks[self._idx]
            if tok.text == "scoped":
                self._fail(tok.line, "scoped")
            if tok.text == "local":
                local_tok = tok if local_tok is None else local_tok
            elif tok.text not in _MODIFIERS:
                break
            self._idx += 1
        return local_tok

    def _extract_declaration(self, span: _DeclSpan, kind: LeanDeclarationKind) -> None:
        for pos in range(span.kw_idx + 1, span.end_idx):
            tok = self._toks[pos]
            if tok.depth == 0 and tok.kind == "ident" and tok.text in _FORBIDDEN_BODY:
                self._fail(tok.line, tok.text)
        name_tok = self._find_name_tok(span.kw_idx, span.end_idx, kind)
        scan_start = span.kw_idx + 2 if name_tok is not None else span.kw_idx + 1
        delim = self._find_body_delim(scan_start, span.end_idx, kind)
        if name_tok is None and not self._has_anon_instance_colon(span.kw_idx, delim):
            self._fail(self._toks[span.kw_idx].line, "anonymous-instance")
        target = _DeclTarget(name_tok, self._qualify_name(name_tok))
        sig, body = self._build_sig_and_body(span, delim, target)
        self._decls.append(
            LeanDeclaration(
                name=target.qualified,
                kind=kind,
                signature=sig,
                body=body,
                start_line=self._toks[span.start_idx].line,
                end_line=self._toks[span.end_idx - 1].end_line,
                upstream_path=self._path,
                upstream_commit=self._commit,
                body_lines=0 if body == "" else max(1, len(body.strip().splitlines())),
                body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            )
        )
        self._idx = span.end_idx

    def _find_decl_end(self, kw_idx: int, kind: LeanDeclarationKind) -> int:
        kw_line, pos = self._toks[kw_idx].line, kw_idx + 1
        seen_body = False
        while pos < self._n:
            tok = self._toks[pos]
            at_col0 = tok.depth == 0 and tok.col == 0 and tok.line > kw_line
            if at_col0 and not self._is_col0_continuation(pos, kind, seen_body):
                break
            if tok.depth == 0 and self._starts_body(pos):
                seen_body = True
            pos += 1
        return pos

    def _starts_body(self, pos: int) -> bool:
        tok = self._toks[pos]
        if tok.kind == "op" and tok.text == ":=":
            return True
        if tok.kind == "ident" and tok.text == "where":
            return True
        return tok.kind == "op" and tok.text == "|" and self._is_line_initial(pos)

    def _is_col0_continuation(self, pos: int, kind: LeanDeclarationKind, seen_body: bool) -> bool:
        tok = self._toks[pos]
        if tok.text in _ALWAYS_COL0_CONT:
            return True
        if tok.text == "|":
            return kind in _BAR_CONTINUATION_KINDS
        if tok.text == "where":
            return kind in _DERIVING_KINDS or (kind in _BODY_KINDS and not seen_body)
        return kind in _DERIVING_KINDS and self._is_deriving_clause(pos)

    def _is_deriving_clause(self, pos: int) -> bool:
        if self._toks[pos].text != "deriving":
            return False
        nxt = self._toks[pos + 1] if pos + 1 < self._n else None
        return not (nxt and nxt.kind == "ident" and nxt.text == "instance")

    def _find_name_tok(self, kw_idx: int, end_idx: int, kind: LeanDeclarationKind) -> _Token | None:
        tok = self._toks[kw_idx + 1] if kw_idx + 1 < end_idx else None
        if tok is not None and tok.kind == "ident" and tok.text not in _NON_NAME:
            return tok
        if kind == "instance":
            return None
        self._fail(self._toks[kw_idx].line, f"unnamed-{kind}")

    def _has_anon_instance_colon(self, kw_idx: int, delim: tuple[int, str] | None) -> bool:
        if delim is None:
            return False
        return any(
            self._toks[p].depth == 0 and self._toks[p].kind == "op" and self._toks[p].text == ":"
            for p in range(kw_idx + 1, delim[0])
        )

    def _qualify_name(self, name_tok: _Token | None) -> str:
        if name_tok is not None and name_tok.text.startswith("_root_."):
            return name_tok.text.removeprefix("_root_.")
        if name_tok is not None:
            raw = name_tok.text
        else:
            self._anon_instance_count += 1
            raw = f"_instance_{self._anon_instance_count}"
        ns = [name for k, name, _ in self._scopes if k == "namespace" and name is not None]
        return ".".join([*ns, raw]) if ns else raw

    def _find_body_delim(
        self, scan_start: int, end_idx: int, kind: LeanDeclarationKind
    ) -> tuple[int, str] | None:
        if kind in _NO_BODY_KINDS:
            return None
        if kind == "opaque":
            return self._find_opaque_assign(scan_start, end_idx)
        return self._scan_body_delim(scan_start, end_idx)

    def _scan_body_delim(self, scan_start: int, end_idx: int) -> tuple[int, str] | None:
        seen_match = False
        for pos in range(scan_start, end_idx):
            tok = self._toks[pos]
            if tok.depth != 0:
                continue
            if tok.text in {":=", "where"}:
                return pos, tok.text
            if tok.text == "match":
                seen_match = True
            elif self._is_equation_arm_start(pos, end_idx, seen_match):
                return pos, "|"
        return None

    def _is_equation_arm_start(self, pos: int, end_idx: int, seen_match: bool) -> bool:
        if seen_match or self._toks[pos].text != "|":
            return False
        return self._is_line_initial(pos) and self._has_arm_arrow(pos, end_idx)

    def _find_opaque_assign(self, scan_start: int, end_idx: int) -> tuple[int, str] | None:
        for pos in range(scan_start, end_idx):
            tok = self._toks[pos]
            if tok.depth != 0:
                continue
            if tok.kind == "op" and tok.text == ":=":
                return pos, ":="
            if tok.kind == "ident" and tok.text == "where":
                return None
        return None

    def _has_arm_arrow(self, bar_pos: int, end_idx: int) -> bool:
        for pos in range(bar_pos + 1, end_idx):
            tok = self._toks[pos]
            if tok.depth != 0:
                continue
            if tok.kind == "op" and tok.text == "=>":
                return True
            if (tok.kind == "op" and tok.text == ":=") or (
                tok.kind == "ident" and tok.text == "where"
            ):
                return False
        return False

    def _build_sig_and_body(
        self,
        span: _DeclSpan,
        delim: tuple[int, str] | None,
        target: _DeclTarget,
    ) -> tuple[str, str]:
        kind = self._toks[span.kw_idx].text
        s_pos = self._toks[span.start_idx].start
        e_pos = self._toks[span.end_idx - 1].end
        edits = self._sig_edits(span, target)
        if delim is None:
            if kind in _NO_BODY_KINDS or kind == "opaque":
                return _apply_sig_edits(self._src, s_pos, e_pos, edits), ""
            self._fail(self._toks[span.kw_idx].line, f"missing-body-{kind}")
        delim_idx, delim_kind = delim
        delim_tok = self._toks[delim_idx]
        if delim_kind == ":=":
            sig = _apply_sig_edits(self._src, s_pos, delim_tok.start, edits)
            body = _normalize_body(self._src[delim_tok.end : e_pos])
        else:
            cut = (
                delim_tok.start - delim_tok.col
                if self._is_line_initial(delim_idx)
                else delim_tok.start
            )
            sig = _apply_sig_edits(self._src, s_pos, cut, edits)
            body = self._src[cut:e_pos].rstrip()
        if not body:
            self._fail(self._toks[span.kw_idx].line, f"empty-body-{kind}")
        return sig, body

    def _sig_edits(self, span: _DeclSpan, target: _DeclTarget) -> list[tuple[int, int, str]]:
        edits = [(start, self._toks[next_idx].start, "") for start, next_idx in span.skipped_spans]
        if target.name_tok is not None:
            edits.append((target.name_tok.start, target.name_tok.end, target.qualified))
        else:
            kw_end = self._toks[span.kw_idx].end
            edits.append((kw_end, kw_end, f" {target.qualified}"))
        return edits


def extract_declarations(source: str, *, rel_path: str, commit: str) -> ExtractedModule:
    """Extract top-level Lean 4 imports and declarations under the closed-world grammar."""
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens = LeanLexer(normalized, rel_path).tokenize()
    return _Parser(normalized, tokens, rel_path, commit).parse()
