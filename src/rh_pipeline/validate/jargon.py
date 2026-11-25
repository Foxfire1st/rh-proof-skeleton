"""Deterministic Explainer Directive and Level-1 Jargon Validator."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from rh_pipeline.models.concepts import ConceptCard, parse_concept_card
from rh_pipeline.models.validation import ValidationFinding, sort_findings

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_DISPLAY_MATH_END_RE = re.compile(r"\\.|(\$\$)", re.DOTALL)
_INLINE_MATH_END_RE = re.compile(r"\\.|(\n\n|\$)", re.DOTALL)
_LINK_TOK_RE = re.compile(r"\\.|(\n\n|[()])", re.DOTALL)
_DIR_TOK_RE = re.compile(r"\\.|(\$|[{}])", re.DOTALL)
_SCAN_TOK_RE = re.compile(r"\\.|(\$|{{concept:|{{term:)", re.DOTALL)
_PROSE_TOK_RE = re.compile(r"\\.|(\$|\]\()", re.DOTALL)
_H2_RE = re.compile(r"^##\s+")
_LEVEL1_H2_RE = re.compile(r"^##\s+Level\s+1\b")
_LEVEL23_H2_RE = re.compile(r"^##\s+Level\s+[23]\b")
_RANK_REVEAL, _RANK_TERM, _RANK_RAW = 0, 1, 2
type _Event = tuple[int, int, str, str]


@dataclass(slots=True)
class _PageCtx:
    normalized: str
    rel_path: str
    registry: Mapping[str, ConceptCard]
    line_starts: list[int]
    line_offset: int
    events: list[_Event]
    findings: list[ValidationFinding]
    mask_spans: list[tuple[int, int]]

    def line_index(self, pos: int) -> int:
        return bisect_right(self.line_starts, pos) - 1

    def add_finding(self, rule: str, pos: int, message: str) -> None:
        line = self.line_index(pos) + 1 + self.line_offset
        self.findings.append(ValidationFinding(rule, self.rel_path, line, message))


def _blank_span(buf: list[str], start: int, end: int) -> None:
    buf[start:end] = ["\n" if ch == "\n" else " " for ch in buf[start:end]]


def _mask_fenced_code(text: str) -> str:
    out: list[str] = []
    fence_char, fence_len = "", 0
    for line in text.split("\n"):
        if fence_char:
            out.append(" " * len(line))
            cm = _FENCE_CLOSE_RE.match(line)
            if cm and cm.group(1)[0] == fence_char and len(cm.group(1)) >= fence_len:
                fence_char, fence_len = "", 0
            continue
        if (m := _FENCE_OPEN_RE.match(line)) and (m.group(1)[0] == "~" or "`" not in m.group(2)):
            fence_char, fence_len = m.group(1)[0], len(m.group(1))
            out.append(" " * len(line))
        else:
            out.append(line)
    return "\n".join(out)


def _mask_inline_code(text: str) -> str:
    buf, runs, idx = list(text), list(_BACKTICK_RUN_RE.finditer(text)), 0
    while idx < len(runs):
        opener = runs[idx]
        count = opener.end() - opener.start()
        for j in range(idx + 1, len(runs)):
            if "\n\n" in text[opener.end() : runs[j].start()]:
                break
            if runs[j].end() - runs[j].start() == count:
                _blank_span(buf, opener.start(), runs[j].end())
                idx = j
                break
        idx += 1
    return "".join(buf)


def _find_math_end(text: str, start: int) -> int | None:
    is_display = text.startswith("$$", start)
    pattern = _DISPLAY_MATH_END_RE if is_display else _INLINE_MATH_END_RE
    close = "$$" if is_display else "$"
    for m in pattern.finditer(text, start + len(close)):
        if m.group(1) == close:
            return m.end()
        if m.group(1) == "\n\n":
            return None
    return None


def _find_link_url_end(text: str, start: int) -> int | None:
    depth = 1
    for m in _LINK_TOK_RE.finditer(text, start + 1):
        tok = m.group(1)
        if tok == "\n\n":
            return None
        if tok is not None:
            depth += 1 if tok == "(" else -1
            if depth == 0:
                return m.end()
    return None


def _mask_math_and_links(text: str, buf: list[str]) -> None:
    pos = 0
    while (m := _PROSE_TOK_RE.search(text, pos)) is not None:
        tok = m.group(1)
        if tok == "$":
            span_start, end = m.start(), _find_math_end(text, m.start())
        elif tok == "](":
            span_start, end = m.start() + 1, _find_link_url_end(text, m.start() + 1)
        else:
            pos = m.end()
            continue
        if end is not None:
            _blank_span(buf, span_start, end)
        pos = end if end is not None else m.end()


def _find_directive_close(text: str, start: int) -> int | None:
    pos, brace_depth = start, 0
    while (m := _DIR_TOK_RE.search(text, pos)) is not None:
        tok = m.group(1)
        if tok == "$":
            pos = _find_math_end(text, m.start()) or m.end()
        elif tok == "{":
            brace_depth, pos = brace_depth + 1, m.end()
        elif tok == "}" and brace_depth > 0:
            brace_depth, pos = brace_depth - 1, m.end()
        elif tok == "}" and text.startswith("}}", m.start()):
            return m.start()
        else:
            pos = m.end()
    return None


def _parse_concept_directive(code_masked: str, pos: int, close_start: int, ctx: _PageCtx) -> int:
    payload_start, close_end = pos + 10, close_start + 2
    slug_raw, sep, _ = code_masked[payload_start:close_start].partition("|")
    slug, body_start = slug_raw.strip(), payload_start + len(slug_raw) + 1
    if not sep or not slug or not ctx.normalized[body_start:close_start].strip():
        ctx.mask_spans.append((pos, close_end))
        ctx.add_finding("invalid-directive", pos, "expected '{{concept:<slug>|<inline text>}}'")
        return close_end
    # Mask only the directive wrapper so inline prose is checked with `<slug>` already revealed.
    ctx.mask_spans.extend(((pos, body_start), (close_start, close_end)))
    if slug not in ctx.registry:
        ctx.add_finding("unknown-concept", pos, f"unknown concept '{slug}'")
    else:
        ctx.events.append((pos, _RANK_REVEAL, slug, ""))
    return body_start


def _parse_term_directive(pos: int, close_start: int, ctx: _PageCtx) -> int:
    payload_start, close_end = pos + 7, close_start + 2
    ctx.mask_spans.append((pos, close_end))
    slug_raw, sep, display_raw = ctx.normalized[payload_start:close_start].partition("|")
    slug = slug_raw.strip()
    if not slug or (sep and not display_raw.strip()):
        ctx.add_finding("invalid-directive", pos, "expected '{{term:<slug>[|<display>]}}'")
    elif slug not in ctx.registry:
        ctx.add_finding("unknown-concept", pos, f"unknown concept '{slug}'")
    else:
        ctx.events.append((pos, _RANK_TERM, slug, ""))
    return close_end


def _scan_directives(code_masked: str, ctx: _PageCtx) -> None:
    pos = 0
    while (m := _SCAN_TOK_RE.search(code_masked, pos)) is not None:
        tok = m.group(1)
        if tok == "$":
            pos = _find_math_end(code_masked, m.start()) or m.end()
        elif tok in ("{{concept:", "{{term:"):
            close_start = _find_directive_close(code_masked, m.end())
            if close_start is None:
                kind = "concept" if tok == "{{concept:" else "term"
                ctx.add_finding("invalid-directive", m.start(), f"unclosed {kind} directive")
                pos = m.end()
            elif tok == "{{concept:":
                pos = _parse_concept_directive(code_masked, m.start(), close_start, ctx)
            else:
                pos = _parse_term_directive(m.start(), close_start, ctx)
        else:
            pos = m.end()


def _level1_line_flags(code_masked: str, *, own_slug: str | None) -> list[bool]:
    flags: list[bool] = []
    active, seen_upper = own_slug is not None, False
    for line in code_masked.split("\n"):
        if _LEVEL1_H2_RE.match(line):
            active = True
        elif own_slug is not None and _LEVEL23_H2_RE.match(line):
            active, seen_upper = False, True
        elif _H2_RE.match(line) and (own_slug is None or seen_upper):
            active = False
        flags.append(active)
    return flags


def _build_term_matcher(
    registry: Mapping[str, ConceptCard],
) -> tuple[re.Pattern[str], dict[str, str]] | None:
    variants: dict[str, str] = {}
    for slug, card in sorted(registry.items()):
        for raw in (card.term, *card.aliases):
            if norm := " ".join(raw.lower().split()):
                variants.setdefault(norm, slug)
    for norm, slug in list(variants.items()):
        variants.setdefault(f"{norm}s", slug)
        variants.setdefault(f"{norm}es", slug)
    if not variants:
        return None
    # Sort longest-first so compound terms win over shorter sub-terms at the same offset.
    ordered = sorted(variants, key=lambda key: (-len(key), key))
    alts = [r"\s+".join(re.escape(part) for part in key.split()) for key in ordered]
    return re.compile(rf"(?<![\w-])(?:{'|'.join(alts)})(?![\w-])", re.IGNORECASE), variants


def _evaluate_events(ctx: _PageCtx, level1_flags: list[bool], *, own_slug: str | None) -> None:
    revealed: set[str] = set()
    for pos, rank, slug, matched in sorted(ctx.events):
        if rank == _RANK_REVEAL:
            revealed.add(slug)
        elif slug != own_slug and slug not in revealed and level1_flags[ctx.line_index(pos)]:
            msg = (
                f"term directive '{{{{term:{slug}}}}}' ({slug}) used before inline reveal"
                if rank == _RANK_TERM
                else f"term '{matched}' ({slug}) used before inline reveal"
            )
            ctx.add_finding("jargon-before-reveal", pos, msg)


def check_page_jargon(
    markdown: str,
    *,
    rel_path: str,
    registry: Mapping[str, ConceptCard],
    own_slug: str | None = None,
    line_offset: int = 0,
) -> tuple[ValidationFinding, ...]:
    """Validate concept/term directives across all sections and jargon order in Level-1 prose."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    starts = [0, *(m.end() for m in re.finditer("\n", normalized))]
    ctx = _PageCtx(normalized, rel_path, registry, starts, line_offset, [], [], [])
    code_masked = _mask_inline_code(_mask_fenced_code(normalized))
    _scan_directives(code_masked, ctx)
    level1_flags = _level1_line_flags(code_masked, own_slug=own_slug)
    buf = list(code_masked)
    for start, end in ctx.mask_spans:
        _blank_span(buf, start, end)
    _mask_math_and_links("".join(buf), buf)
    if (matcher := _build_term_matcher(registry)) is not None:
        pattern, variants = matcher
        for m in pattern.finditer("".join(buf)):
            slug = variants[" ".join(m.group(0).lower().split())]
            ctx.events.append((m.start(), _RANK_RAW, slug, m.group(0)))
    _evaluate_events(ctx, level1_flags, own_slug=own_slug)
    return sort_findings(ctx.findings)


def _iter_stage_docs(stages_dir: Path) -> Iterator[Path]:
    for stage_dir in sorted(stages_dir.iterdir()) if stages_dir.is_dir() else ():
        if stage_dir.is_dir():
            yield from (p for p in sorted(stage_dir.glob("*.md")) if p.is_file())


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def validate_explainer_docs(
    docs_root: Path, *, registry: Mapping[str, ConceptCard]
) -> tuple[ValidationFinding, ...]:
    """Scan strictly `<docs_root>/concepts/*.md` and `<docs_root>/stages/<stage_id>/*.md`."""
    findings: list[ValidationFinding] = []
    concepts_dir = docs_root / "concepts"
    for path in (
        (p for p in sorted(concepts_dir.glob("*.md")) if p.is_file())
        if concepts_dir.is_dir()
        else ()
    ):
        rel = path.relative_to(docs_root).as_posix()
        if (text := _read_text_or_none(path)) is None:
            continue
        card, _, off = parse_concept_card(text, expected_slug=path.stem, rel_path=rel)
        if card is not None:
            findings.extend(
                check_page_jargon(
                    card.body_markdown,
                    rel_path=rel,
                    registry=registry,
                    own_slug=card.slug,
                    line_offset=off,
                )
            )
    for path in _iter_stage_docs(docs_root / "stages"):
        rel = path.relative_to(docs_root).as_posix()
        if (text := _read_text_or_none(path)) is None:
            findings.append(ValidationFinding("explainer-read", rel, 0, f"failed to read '{rel}'"))
        else:
            findings.extend(check_page_jargon(text, rel_path=rel, registry=registry))
    return sort_findings(findings)
