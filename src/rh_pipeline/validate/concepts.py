"""Concept Card corpus validator with cached Lean skeleton anchor resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rh_pipeline.lean.parser import UnsupportedLeanFormError, extract_declarations
from rh_pipeline.models.concepts import ConceptCard, parse_concept_card
from rh_pipeline.models.validation import ValidationFinding, sort_findings


@dataclass(slots=True)
class _AnchorResolver:
    """Caches parsed Lean skeleton declaration names per relative skeleton path."""

    skeleton_root: Path
    resolved_root: Path
    _cache: dict[str, tuple[frozenset[str] | None, str | None]]

    @classmethod
    def create(cls, skeleton_root: Path) -> _AnchorResolver:
        return cls(
            skeleton_root=skeleton_root,
            resolved_root=skeleton_root.resolve(),
            _cache={},
        )

    def is_valid_file(self, skeleton_rel_path: str) -> bool:
        target = (self.skeleton_root / skeleton_rel_path).resolve()
        return target.is_relative_to(self.resolved_root) and target.is_file()

    def lookup(self, skeleton_rel_path: str) -> tuple[frozenset[str] | None, str | None]:
        cached = self._cache.get(skeleton_rel_path)
        if cached is not None:
            return cached
        result = self._load_declarations(skeleton_rel_path)
        self._cache[skeleton_rel_path] = result
        return result

    def _load_declarations(
        self, skeleton_rel_path: str
    ) -> tuple[frozenset[str] | None, str | None]:
        target = (self.skeleton_root / skeleton_rel_path).resolve()
        try:
            source = target.read_text(encoding="utf-8")
            module = extract_declarations(source, rel_path=skeleton_rel_path, commit="local")
        except (OSError, UnicodeDecodeError, UnsupportedLeanFormError) as exc:
            return None, str(exc)
        return frozenset(decl.name for decl in module.declarations), None


def _is_safe_skeleton_rel_path(value: str) -> bool:
    if not value or value != value.strip() or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    if parts[0] == "skeleton":
        return False
    return not any(part in {"", ".", ".."} for part in parts)


def _parse_lean_anchor(anchor: str) -> tuple[str, str] | None:
    if anchor.count("#") != 1:
        return None
    rel_path, decl_name = anchor.split("#", 1)
    if not _is_safe_skeleton_rel_path(rel_path):
        return None
    if not decl_name or any(ch.isspace() for ch in decl_name):
        return None
    return rel_path, decl_name


def _read_card_text(path: Path, rel_path: str) -> str | ValidationFinding:
    if not path.is_file():
        return ValidationFinding(
            "concept-read", rel_path, 0, f"concept card path '{rel_path}' is not a regular file"
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ValidationFinding(
            "concept-read", rel_path, 0, f"failed to read concept card '{rel_path}': {exc}"
        )


def _load_concept_cards(
    docs_root: Path, concepts_dir: Path
) -> tuple[dict[str, ConceptCard], dict[str, str], list[ValidationFinding]]:
    cards: dict[str, ConceptCard] = {}
    card_paths: dict[str, str] = {}
    findings: list[ValidationFinding] = []
    for path in sorted(concepts_dir.glob("*.md")):
        rel_path = path.relative_to(docs_root).as_posix()
        loaded = _read_card_text(path, rel_path)
        if isinstance(loaded, ValidationFinding):
            findings.append(loaded)
            continue
        card, parse_findings, _ = parse_concept_card(
            loaded, expected_slug=path.stem, rel_path=rel_path
        )
        findings.extend(parse_findings)
        if card is None:
            continue
        if card.slug in cards:
            findings.append(
                ValidationFinding(
                    "concept-duplicate-slug",
                    rel_path,
                    0,
                    f"duplicate concept slug '{card.slug}'",
                )
            )
            continue
        cards[card.slug] = card
        card_paths[card.slug] = rel_path
    return cards, card_paths, findings


def _validate_child_concepts(
    card: ConceptCard, rel_path: str, known_cards: Mapping[str, ConceptCard]
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    # Distinct concepts may reference each other cyclically; only direct self-cycles are invalid.
    for child in card.child_concepts:
        if child == card.slug:
            findings.append(
                ValidationFinding(
                    "concept-self-cycle",
                    rel_path,
                    0,
                    f"concept '{card.slug}' must not reference itself in child_concepts",
                )
            )
        elif child not in known_cards:
            findings.append(
                ValidationFinding(
                    "concept-missing-child",
                    rel_path,
                    0,
                    f"child concept '{child}' does not exist in corpus",
                )
            )
    return findings


def _validate_one_anchor(
    anchor: str, rel_path: str, resolver: _AnchorResolver
) -> ValidationFinding | None:
    parsed = _parse_lean_anchor(anchor)
    if parsed is None:
        return ValidationFinding(
            "lean-anchor-format",
            rel_path,
            0,
            f"invalid lean_anchor {anchor!r}; expected '<skeleton_rel_path>#<DeclarationName>'",
        )
    skeleton_rel_path, decl_name = parsed
    if not resolver.is_valid_file(skeleton_rel_path):
        return ValidationFinding(
            "lean-anchor-missing-file",
            rel_path,
            0,
            f"lean_anchor file '{skeleton_rel_path}' not found under skeleton root",
        )
    decl_names, error = resolver.lookup(skeleton_rel_path)
    if decl_names is None:
        return ValidationFinding(
            "lean-anchor-parse-error",
            rel_path,
            0,
            f"failed to parse lean_anchor file '{skeleton_rel_path}': {error}",
        )
    if decl_name not in decl_names:
        return ValidationFinding(
            "lean-anchor-missing-decl",
            rel_path,
            0,
            f"declaration '{decl_name}' not found in '{skeleton_rel_path}'",
        )
    return None


def _validate_visual_ref(
    card: ConceptCard, rel_path: str, docs_root: Path, resolved_docs_root: Path
) -> ValidationFinding | None:
    if card.visual_ref is None:
        return None
    target = (docs_root / card.visual_ref).resolve()
    if target.is_relative_to(resolved_docs_root) and target.is_file():
        return None
    return ValidationFinding(
        "concept-missing-visual",
        rel_path,
        0,
        f"visual_ref '{card.visual_ref}' does not resolve to an existing file under docs root",
    )


def _validate_card(
    card: ConceptCard,
    rel_path: str,
    cards: Mapping[str, ConceptCard],
    resolver: _AnchorResolver,
    docs_roots: tuple[Path, Path],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    if not card.sources and not card.lean_anchors:
        findings.append(
            ValidationFinding(
                "concept-unsourced",
                rel_path,
                0,
                f"concept '{card.slug}' must declare at least one source or lean_anchor",
            )
        )
    findings.extend(_validate_child_concepts(card, rel_path, cards))
    for anchor in card.lean_anchors:
        anchor_finding = _validate_one_anchor(anchor, rel_path, resolver)
        if anchor_finding is not None:
            findings.append(anchor_finding)
    visual_finding = _validate_visual_ref(card, rel_path, docs_roots[0], docs_roots[1])
    if visual_finding is not None:
        findings.append(visual_finding)
    return findings


def validate_concept_corpus(
    docs_root: Path, *, skeleton_root: Path
) -> tuple[dict[str, ConceptCard], tuple[ValidationFinding, ...]]:
    """Validate all Concept Cards under `<docs_root>/concepts/*.md` against the skeleton."""
    concepts_dir = docs_root / "concepts"
    if not concepts_dir.exists() and not concepts_dir.is_symlink():
        return {}, ()
    if not concepts_dir.is_dir():
        finding = ValidationFinding(
            "concept-dir",
            "concepts",
            0,
            "concepts path exists under docs root but is not a directory",
        )
        return {}, sort_findings((finding,))

    cards, card_paths, findings = _load_concept_cards(docs_root, concepts_dir)
    resolver = _AnchorResolver.create(skeleton_root)
    docs_roots = (docs_root, docs_root.resolve())
    for slug in sorted(cards):
        findings.extend(_validate_card(cards[slug], card_paths[slug], cards, resolver, docs_roots))
    return cards, sort_findings(findings)
