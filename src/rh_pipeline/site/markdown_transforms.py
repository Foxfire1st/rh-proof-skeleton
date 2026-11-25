"""Markdown directive expansion, zoom-level wrapping, and static glossary rendering."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rh_pipeline.models.concepts import ConceptCard

__all__ = [
    "SiteDirectiveError",
    "expand_concept_directives",
    "mask_code_spans",
    "mask_fenced_code",
    "normalize_base_url",
    "render_page_glossary",
    "split_zoom_levels",
]

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_DISPLAY_MATH_END_RE = re.compile(r"\\.|(\$\$)", re.DOTALL)
_INLINE_MATH_END_RE = re.compile(r"\\.|(\n\n|\$)", re.DOTALL)
_DIR_TOK_RE = re.compile(r"\\.|(\$|[{}])", re.DOTALL)
_SCAN_TOK_RE = re.compile(r"\\.|(\$|{{concept:|{{term:)", re.DOTALL)
_H2_RE = re.compile(r"^##\s+")
_LEVEL1_H2_RE = re.compile(r"^##\s+Level\s+1\b")
_LEVEL2_H2_RE = re.compile(r"^##\s+Level\s+2\b")
_LEVEL3_H2_RE = re.compile(r"^##\s+Level\s+3\b")
_ANY_LEVEL_H2_RE = re.compile(r"^##\s+Level\s+[123]\b")
_BAD_BASE_RE = re.compile(r"[\s\\]|//")


class SiteDirectiveError(ValueError):
    """Raised when a base URL or concept/term directive is malformed or unresolved."""


@dataclass(slots=True)
class _ExpandState:
    registry: Mapping[str, ConceptCard]
    base_url: str
    seen: set[str]
    ordered: list[str]

    def record(self, slug: str) -> ConceptCard:
        card = self.registry.get(slug)
        if card is None:
            raise SiteDirectiveError(f"Unknown concept slug: {slug!r}")
        if slug not in self.seen:
            self.seen.add(slug)
            self.ordered.append(slug)
        return card


def normalize_base_url(base_url: str) -> str:
    """Normalize a site base URL to start and end with '/' and reject traversal."""
    cleaned = base_url.strip()
    if not cleaned or _BAD_BASE_RE.search(cleaned):
        raise SiteDirectiveError(f"Invalid base_url: {base_url!r}")
    parts = [p for p in cleaned.strip("/").split("/") if p]
    if any(part in {".", ".."} for part in parts):
        raise SiteDirectiveError(f"base_url must not contain path traversal: {base_url!r}")
    return "/" if not parts else f"/{'/'.join(parts)}/"


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


def mask_fenced_code(text: str) -> str:
    """Return a same-length copy of `text` with fenced code blocks replaced by spaces."""
    return _mask_fenced_code(text)


def mask_code_spans(text: str) -> str:
    """Return a same-length copy of `text` with fenced and inline code replaced by spaces."""
    return _mask_inline_code(_mask_fenced_code(text))


def _find_math_end(text: str, start: int) -> int | None:
    is_display = text.startswith("$$", start)
    pattern = _DISPLAY_MATH_END_RE if is_display else _INLINE_MATH_END_RE
    close = "$$" if is_display else "$"
    for match in pattern.finditer(text, start + len(close)):
        if match.group(1) == close:
            return match.end()
        if match.group(1) == "\n\n":
            return None
    return None


def _find_directive_close(text: str, start: int) -> int | None:
    pos, brace_depth = start, 0
    while (match := _DIR_TOK_RE.search(text, pos)) is not None:
        tok = match.group(1)
        if tok == "$":
            pos = _find_math_end(text, match.start()) or match.end()
        elif tok == "{":
            brace_depth, pos = brace_depth + 1, match.end()
        elif tok == "}" and brace_depth > 0:
            brace_depth, pos = brace_depth - 1, match.end()
        elif tok == "}" and text.startswith("}}", match.start()):
            return match.start()
        else:
            pos = match.end()
    return None


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _render_concept_reveal(card: ConceptCard, inner_html: str, *, base_url: str) -> str:
    href = _esc(f"{base_url}concepts/{card.slug}")
    return (
        f'<ConceptReveal slug="{_esc(card.slug)}" term="{_esc(card.term)}" href="{href}">'
        f"{inner_html}</ConceptReveal>"
    )


def _render_concept_hover(card: ConceptCard, display: str, *, base_url: str) -> str:
    attrs = [
        f'slug="{_esc(card.slug)}"',
        f'term="{_esc(card.term)}"',
        f'display="{_esc(display)}"',
        f'glimpse="{_esc(card.inline_glimpse)}"',
        f'href="{_esc(f"{base_url}concepts/{card.slug}")}"',
    ]
    if card.visual_ref is not None:
        attrs.append(f'visual-src="{_esc(f"{base_url}{card.visual_ref}")}"')
        attrs.append(f'visual-alt="{_esc(f"Diagram illustrating {card.term}")}"')
    return f"<ConceptHover {' '.join(attrs)} />"


def _expand_concept_tag(
    normalized: str, code_masked: str, start: int, close_start: int, state: _ExpandState
) -> str:
    payload_start = start + len("{{concept:")
    slug_raw, sep, _ = code_masked[payload_start:close_start].partition("|")
    slug, body_start = slug_raw.strip(), payload_start + len(slug_raw) + 1
    body = normalized[body_start:close_start]
    if not sep or not slug or not body.strip():
        raise SiteDirectiveError(
            "Malformed concept directive; expected '{{concept:<slug>|<prose>}}'"
        )
    card = state.record(slug)
    expanded_body = _expand_segment(body, code_masked[body_start:close_start], state)
    return _render_concept_reveal(card, expanded_body, base_url=state.base_url)


def _expand_term_tag(normalized: str, start: int, close_start: int, state: _ExpandState) -> str:
    payload = normalized[start + len("{{term:") : close_start]
    slug_raw, sep, display_raw = payload.partition("|")
    slug = slug_raw.strip()
    if not slug or (sep and not display_raw.strip()):
        raise SiteDirectiveError("Malformed term directive; expected '{{term:<slug>[|<display>]}}'")
    card = state.record(slug)
    return _render_concept_hover(
        card, display_raw.strip() if sep else card.term, base_url=state.base_url
    )


def _expand_segment(normalized: str, code_masked: str, state: _ExpandState) -> str:
    parts: list[str] = []
    cursor, pos = 0, 0
    while (match := _SCAN_TOK_RE.search(code_masked, pos)) is not None:
        tok = match.group(1)
        if tok == "$":
            pos = _find_math_end(code_masked, match.start()) or match.end()
            continue
        if tok not in ("{{concept:", "{{term:"):
            pos = match.end()
            continue
        close_start = _find_directive_close(code_masked, match.end())
        if close_start is None:
            kind = "concept" if tok == "{{concept:" else "term"
            raise SiteDirectiveError(f"Unclosed {kind} directive")
        parts.append(normalized[cursor : match.start()])
        if tok == "{{concept:":
            tag = _expand_concept_tag(normalized, code_masked, match.start(), close_start, state)
        else:
            tag = _expand_term_tag(normalized, match.start(), close_start, state)
        parts.append(tag)
        cursor = pos = close_start + 2
    parts.append(normalized[cursor:])
    return "".join(parts)


def expand_concept_directives(
    markdown: str, registry: Mapping[str, ConceptCard], *, base_url: str
) -> tuple[str, tuple[str, ...]]:
    """Expand `{{concept:...}}` and `{{term:...}}` directives outside code and math."""
    norm_base = normalize_base_url(base_url)
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    state = _ExpandState(registry=registry, base_url=norm_base, seen=set(), ordered=[])
    expanded = _expand_segment(normalized, mask_code_spans(normalized), state)
    return expanded, tuple(state.ordered)


def _find_line_index(masked_lines: list[str], start: int, pattern: re.Pattern[str]) -> int | None:
    for idx in range(start, len(masked_lines)):
        if pattern.match(masked_lines[idx]):
            return idx
    return None


def _find_level3_end(masked_lines: list[str], idx3: int) -> int:
    for idx in range(idx3 + 1, len(masked_lines)):
        line = masked_lines[idx]
        if _H2_RE.match(line) and not _ANY_LEVEL_H2_RE.match(line):
            return idx
    return len(masked_lines)


def _locate_zoom_indices(masked_lines: list[str]) -> tuple[int, int, int, int] | None:
    idx1 = _find_line_index(masked_lines, 0, _LEVEL1_H2_RE)
    idx2 = _find_line_index(masked_lines, idx1 + 1, _LEVEL2_H2_RE) if idx1 is not None else None
    idx3 = _find_line_index(masked_lines, idx2 + 1, _LEVEL3_H2_RE) if idx2 is not None else None
    if idx1 is None or idx2 is None or idx3 is None:
        return None
    return idx1, idx2, idx3, _find_level3_end(masked_lines, idx3)


def split_zoom_levels(markdown: str, *, page_id: str) -> str:
    """Wrap ordered Level 1 / Level 2 / Level 3 sections in `<ZoomTabs>` when all three exist."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    indices = _locate_zoom_indices(_mask_fenced_code(normalized).split("\n"))
    if indices is None:
        return markdown
    idx1, idx2, idx3, end3 = indices
    preamble = "\n".join(lines[:idx1]).strip()
    l1, l2 = "\n".join(lines[idx1:idx2]).strip(), "\n".join(lines[idx2:idx3]).strip()
    l3, epilogue = "\n".join(lines[idx3:end3]).strip(), "\n".join(lines[end3:]).strip()
    tabs = (
        f'<ZoomTabs page-id="{_esc(page_id)}">\n'
        f"<template #level1>\n\n{l1}\n\n</template>\n"
        f"<template #level2>\n\n{l2}\n\n</template>\n"
        f"<template #level3>\n\n{l3}\n\n</template>\n"
        "</ZoomTabs>"
    )
    sections = [part for part in (preamble, tabs, epilogue) if part]
    return "\n\n".join(sections) + ("\n" if normalized.endswith("\n") else "")


def render_page_glossary(
    referenced_slugs: Sequence[str], registry: Mapping[str, ConceptCard], *, base_url: str
) -> str:
    """Render a static script-off / print fallback glossary for referenced concepts."""
    norm_base = normalize_base_url(base_url)
    deduped = list(dict.fromkeys(referenced_slugs))
    if not deduped:
        return ""
    entries: list[str] = []
    for slug in deduped:
        card = registry.get(slug)
        if card is None:
            raise SiteDirectiveError(f"Unknown concept slug in glossary: {slug!r}")
        href = _esc(f"{norm_base}concepts/{slug}")
        entries.append(
            f'  <dt><a href="{href}">{html.escape(card.term)}</a></dt>\n'
            f"  <dd>{html.escape(card.inline_glimpse)}</dd>"
        )
    body = "\n".join(entries)
    return (
        '<section class="rh-page-glossary" aria-label="Page glossary">\n\n'
        "## Page Glossary\n\n"
        f"<dl>\n{body}\n</dl>\n\n"
        "</section>"
    )
