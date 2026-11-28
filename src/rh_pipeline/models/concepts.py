"""Pure immutable Concept Card models and YAML frontmatter parser (zero filesystem I/O)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import yaml

from rh_pipeline.models.validation import ValidationFinding, sort_findings

ConceptSourceKind = Literal["book", "paper", "mathlib", "reference"]
VALID_CONCEPT_SOURCE_KINDS: frozenset[str] = frozenset({"book", "paper", "mathlib", "reference"})

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:")
_PLAIN_TEXT_LATEX_RE = re.compile(
    r"(?<!\\)\$\$"
    r"|(?<!\\)\$(?!\$)(?:\\.|[^$\\\n])+?\$"
    r"|\\[()\[\]]"
    r"|\\[A-Za-z]+"
)

_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"slug", "term", "inline_glimpse", "where_analogy_breaks"}
)
_OPTIONAL_KEYS: frozenset[str] = frozenset(
    {"aliases", "visual_ref", "sources", "lean_anchors", "child_concepts"}
)
_ALLOWED_KEYS: frozenset[str] = _REQUIRED_KEYS | _OPTIONAL_KEYS
_SOURCE_KEYS: frozenset[str] = frozenset({"title", "citation", "kind"})


def find_plain_text_latex(text: str) -> str | None:
    """Return the first raw LaTeX delimiter or control sequence in plain text, or `None`.

    Rejects `$$`, paired unescaped `$...$` spans, `\\(`, `\\)`, `\\[`, `\\]`, and
    `\\command` control sequences while allowing a lone currency `$`, escaped `\\$`,
    and ordinary prose backslashes that do not start a LaTeX token.
    """
    match = _PLAIN_TEXT_LATEX_RE.search(text)
    return match.group(0) if match is not None else None


def concept_variants(term: str, aliases: Sequence[str]) -> tuple[str, ...]:
    """Return deduplicated normalized terms, aliases, and simple `-s`/`-es` plurals for one card."""
    bases: list[str] = []
    seen: set[str] = set()
    for raw in (term, *aliases):
        norm = " ".join(raw.lower().split())
        if norm and norm not in seen:
            seen.add(norm)
            bases.append(norm)
    variants = list(bases)
    for norm in bases:
        for plural in (f"{norm}s", f"{norm}es"):
            if plural not in seen:
                seen.add(plural)
                variants.append(plural)
    return tuple(variants)


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _is_safe_docs_rel_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or value.startswith("/") or "\\" in value:
        return False
    return not any(part in {"", ".", ".."} for part in value.split("/"))


@dataclass(frozen=True, slots=True)
class ConceptSource:
    """One authoritative external or Mathlib source citation on a Concept Card."""

    title: str
    citation: str
    kind: ConceptSourceKind

    def __post_init__(self) -> None:
        _require_text(self.title, "source title")
        _require_text(self.citation, "source citation")
        if self.kind not in VALID_CONCEPT_SOURCE_KINDS:
            raise ValueError(
                f"source kind must be one of {sorted(VALID_CONCEPT_SOURCE_KINDS)}, "
                f"got {self.kind!r}"
            )


@dataclass(frozen=True, slots=True)
class ConceptCard:
    """Canonical Concept Card parsed from `<memory>/docs/concepts/<slug>.md`."""

    slug: str
    term: str
    aliases: tuple[str, ...]
    inline_glimpse: str
    visual_ref: str | None
    where_analogy_breaks: str
    sources: tuple[ConceptSource, ...]
    lean_anchors: tuple[str, ...]
    child_concepts: tuple[str, ...]
    body_markdown: str

    def __post_init__(self) -> None:
        _require_text(self.slug, "slug")
        if not SLUG_PATTERN.match(self.slug):
            raise ValueError(f"invalid concept slug: {self.slug!r}")
        _require_text(self.term, "term")
        _require_text(self.inline_glimpse, "inline_glimpse")
        _require_text(self.where_analogy_breaks, "where_analogy_breaks")
        if self.visual_ref is not None and not _is_safe_docs_rel_path(self.visual_ref):
            raise ValueError(f"invalid visual_ref path: {self.visual_ref!r}")


def _split_frontmatter(text: str) -> tuple[list[str], str, int] | None:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].rstrip(" \t") != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip(" \t") == "---":
            return lines[1:idx], "\n".join(lines[idx + 1 :]), idx + 1
    return None


def _index_key_lines(yaml_lines: list[str]) -> dict[str, int]:
    key_lines: dict[str, int] = {}
    for offset, raw_line in enumerate(yaml_lines):
        if (match := _TOP_LEVEL_KEY_RE.match(raw_line)) is not None:
            key_lines.setdefault(match.group(1), offset + 2)
    return key_lines


def _load_frontmatter_mapping(
    yaml_lines: list[str], *, rel_path: str
) -> tuple[dict[str, object] | None, list[ValidationFinding]]:
    try:
        raw: object = yaml.safe_load("\n".join(yaml_lines))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = int(mark.line) + 2 if mark is not None else 1
        return None, [
            ValidationFinding("concept-yaml", rel_path, line, f"malformed YAML frontmatter: {exc}")
        ]
    if not isinstance(raw, Mapping):
        msg = "YAML frontmatter must be a mapping"
        return None, [ValidationFinding("concept-frontmatter", rel_path, 1, msg)]
    mapping = cast(Mapping[object, object], raw)
    if not all(isinstance(key, str) for key in mapping):
        msg = "YAML frontmatter keys must be strings"
        return None, [ValidationFinding("concept-frontmatter", rel_path, 1, msg)]
    return cast(dict[str, object], dict(mapping)), []


def _check_frontmatter_keys(
    data: Mapping[str, object], key_lines: Mapping[str, int], *, rel_path: str
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for key in sorted(data.keys()):
        if key == "body_markdown":
            msg = "body_markdown must not be set in YAML frontmatter"
            findings.append(
                ValidationFinding("concept-frontmatter", rel_path, key_lines.get(key, 1), msg)
            )
        elif key not in _ALLOWED_KEYS:
            msg = f"unknown frontmatter key '{key}'"
            findings.append(
                ValidationFinding("concept-frontmatter", rel_path, key_lines.get(key, 1), msg)
            )
    for req in sorted(_REQUIRED_KEYS):
        if req not in data:
            msg = f"missing required frontmatter key '{req}'"
            findings.append(ValidationFinding("concept-frontmatter", rel_path, 1, msg))
    return findings


def _parse_nonempty_str(
    data: Mapping[str, object],
    key: str,
    key_lines: Mapping[str, int],
    *,
    rel_path: str,
    findings: list[ValidationFinding],
) -> str | None:
    if key not in data:
        return None
    val = data[key]
    if isinstance(val, str) and val.strip():
        return val
    msg = f"'{key}' must be a non-empty string"
    findings.append(ValidationFinding("concept-frontmatter", rel_path, key_lines.get(key, 1), msg))
    return None


def _parse_slug(
    data: Mapping[str, object],
    key_lines: Mapping[str, int],
    *,
    expected_slug: str,
    rel_path: str,
    findings: list[ValidationFinding],
) -> str | None:
    slug = _parse_nonempty_str(data, "slug", key_lines, rel_path=rel_path, findings=findings)
    if slug is None:
        return None
    line = key_lines.get("slug", 1)
    if not SLUG_PATTERN.match(slug):
        msg = f"slug '{slug}' must match ^[a-z0-9]+(?:-[a-z0-9]+)*$"
        findings.append(ValidationFinding("concept-slug", rel_path, line, msg))
        return None
    if slug != expected_slug:
        msg = f"slug '{slug}' does not match filename '{expected_slug}.md'"
        findings.append(ValidationFinding("concept-slug", rel_path, line, msg))
        return None
    return slug


def _parse_str_list(
    data: Mapping[str, object],
    key: str,
    key_lines: Mapping[str, int],
    *,
    rel_path: str,
    findings: list[ValidationFinding],
) -> tuple[str, ...]:
    if key not in data:
        return ()
    raw, line = data[key], key_lines.get(key, 1)
    if not isinstance(raw, list):
        msg = f"'{key}' must be a list of strings"
        findings.append(ValidationFinding("concept-frontmatter", rel_path, line, msg))
        return ()
    parsed: list[str] = []
    for idx, item in enumerate(cast(list[object], raw)):
        if isinstance(item, str) and item.strip():
            parsed.append(item)
        else:
            msg = f"'{key}[{idx}]' must be a non-empty string"
            findings.append(ValidationFinding("concept-frontmatter", rel_path, line, msg))
    return tuple(parsed)


def _parse_visual_ref(
    data: Mapping[str, object],
    key_lines: Mapping[str, int],
    *,
    rel_path: str,
    findings: list[ValidationFinding],
) -> str | None:
    if "visual_ref" not in data or data["visual_ref"] is None:
        return None
    raw, line = data["visual_ref"], key_lines.get("visual_ref", 1)
    if isinstance(raw, str) and _is_safe_docs_rel_path(raw):
        return raw
    msg = f"visual_ref {raw!r} must be a relative POSIX path under docs/ without '..'"
    findings.append(ValidationFinding("concept-visual-ref", rel_path, line, msg))
    return None


def _build_source(
    obj: Mapping[str, object],
    idx: int,
    line: int,
    *,
    rel_path: str,
    findings: list[ValidationFinding],
) -> ConceptSource | None:
    title, citation, kind = obj["title"], obj["citation"], obj["kind"]
    ok = True
    if not isinstance(title, str) or not title.strip():
        msg = f"sources[{idx}].title must be a non-empty string"
        findings.append(ValidationFinding("concept-source", rel_path, line, msg))
        ok = False
    if not isinstance(citation, str) or not citation.strip():
        msg = f"sources[{idx}].citation must be a non-empty string"
        findings.append(ValidationFinding("concept-source", rel_path, line, msg))
        ok = False
    if not isinstance(kind, str) or kind not in VALID_CONCEPT_SOURCE_KINDS:
        msg = f"sources[{idx}].kind must be one of {sorted(VALID_CONCEPT_SOURCE_KINDS)}"
        findings.append(ValidationFinding("concept-source", rel_path, line, msg))
        ok = False
    if not ok:
        return None
    return ConceptSource(
        title=cast(str, title),
        citation=cast(str, citation),
        kind=cast(ConceptSourceKind, kind),
    )


def _parse_one_source(
    item: object, idx: int, line: int, *, rel_path: str, findings: list[ValidationFinding]
) -> ConceptSource | None:
    if not isinstance(item, Mapping):
        msg = f"sources[{idx}] must be a mapping"
        findings.append(ValidationFinding("concept-source", rel_path, line, msg))
        return None
    raw_map = cast(Mapping[object, object], item)
    if not all(isinstance(k, str) for k in raw_map):
        msg = f"sources[{idx}] keys must be strings"
        findings.append(ValidationFinding("concept-source", rel_path, line, msg))
        return None
    obj = cast(Mapping[str, object], raw_map)
    actual = frozenset(obj.keys())
    missing, extra = sorted(_SOURCE_KEYS - actual), sorted(actual - _SOURCE_KEYS)
    if missing or extra:
        msg = f"sources[{idx}] key mismatch (missing={missing}, extra={extra})"
        findings.append(ValidationFinding("concept-source", rel_path, line, msg))
        return None
    return _build_source(obj, idx, line, rel_path=rel_path, findings=findings)


def _parse_sources(
    data: Mapping[str, object],
    key_lines: Mapping[str, int],
    *,
    rel_path: str,
    findings: list[ValidationFinding],
) -> tuple[ConceptSource, ...]:
    if "sources" not in data:
        return ()
    raw, line = data["sources"], key_lines.get("sources", 1)
    if not isinstance(raw, list):
        msg = "'sources' must be a list of source mappings"
        findings.append(ValidationFinding("concept-source", rel_path, line, msg))
        return ()
    parsed: list[ConceptSource] = []
    for idx, item in enumerate(cast(list[object], raw)):
        if (
            src := _parse_one_source(item, idx, line, rel_path=rel_path, findings=findings)
        ) is not None:
            parsed.append(src)
    return tuple(parsed)


def parse_concept_card(
    text: str, *, expected_slug: str, rel_path: str
) -> tuple[ConceptCard | None, tuple[ValidationFinding, ...], int]:
    """Parse a Concept Card purely from text and return `(card, findings, body_line_offset)`."""
    split = _split_frontmatter(text)
    if split is None:
        msg = "Concept Card must begin with YAML frontmatter delimited by '---'"
        return None, (ValidationFinding("concept-frontmatter", rel_path, 1, msg),), 0

    yaml_lines, body_markdown, body_line_offset = split
    data, load_findings = _load_frontmatter_mapping(yaml_lines, rel_path=rel_path)
    if data is None:
        return None, sort_findings(load_findings), body_line_offset

    key_lines = _index_key_lines(yaml_lines)
    findings = _check_frontmatter_keys(data, key_lines, rel_path=rel_path)
    slug = _parse_slug(
        data, key_lines, expected_slug=expected_slug, rel_path=rel_path, findings=findings
    )
    term = _parse_nonempty_str(data, "term", key_lines, rel_path=rel_path, findings=findings)
    glimpse = _parse_nonempty_str(
        data, "inline_glimpse", key_lines, rel_path=rel_path, findings=findings
    )
    breaks = _parse_nonempty_str(
        data, "where_analogy_breaks", key_lines, rel_path=rel_path, findings=findings
    )
    aliases = _parse_str_list(data, "aliases", key_lines, rel_path=rel_path, findings=findings)
    visual_ref = _parse_visual_ref(data, key_lines, rel_path=rel_path, findings=findings)
    sources = _parse_sources(data, key_lines, rel_path=rel_path, findings=findings)
    anchors = _parse_str_list(data, "lean_anchors", key_lines, rel_path=rel_path, findings=findings)
    children = _parse_str_list(
        data, "child_concepts", key_lines, rel_path=rel_path, findings=findings
    )

    if findings or slug is None or term is None or glimpse is None or breaks is None:
        return None, sort_findings(findings), body_line_offset

    card = ConceptCard(
        slug=slug,
        term=term,
        aliases=aliases,
        inline_glimpse=glimpse,
        visual_ref=visual_ref,
        where_analogy_breaks=breaks,
        sources=sources,
        lean_anchors=anchors,
        child_concepts=children,
        body_markdown=body_markdown,
    )
    return card, (), body_line_offset
