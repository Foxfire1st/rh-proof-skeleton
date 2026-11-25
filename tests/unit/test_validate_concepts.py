"""Unit tests for Concept Card models, frontmatter parsing, and corpus validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import rh_pipeline.validate.concepts as concepts_validator
from rh_pipeline.lean.parser import extract_declarations
from rh_pipeline.models.concepts import (
    ConceptCard,
    ConceptSource,
    parse_concept_card,
)
from rh_pipeline.models.lean import ExtractedModule
from rh_pipeline.models.validation import ValidationFinding, sort_findings
from rh_pipeline.validate.concepts import validate_concept_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SKELETON_ROOT = REPO_ROOT / "skeleton"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "validate" / "concepts"


def _make_card_text(
    *,
    slug: str = "demo-concept",
    term: str = "Demo concept",
    inline_glimpse: str = "Short explanation.",
    where_analogy_breaks: str = "Analogy breaks at the boundary.",
    extra_yaml: str = "",
    body: str = "Card body paragraph.",
    leading_blank_line: bool = True,
) -> str:
    extra = f"{extra_yaml.rstrip()}\n" if extra_yaml.strip() else ""
    sep = "\n\n" if leading_blank_line else "\n"
    return (
        "---\n"
        f"slug: {slug}\n"
        f"term: {term}\n"
        f"inline_glimpse: {inline_glimpse}\n"
        f"where_analogy_breaks: {where_analogy_breaks}\n"
        f"{extra}"
        f"---{sep}"
        f"{body}\n"
    )


def _write_card(docs_root: Path, filename: str, text: str) -> Path:
    path = docs_root / "concepts" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- ValidationFinding & model invariants ---


def test_validation_finding_format_sort_and_invariants() -> None:
    f1 = ValidationFinding("rule-b", "concepts/b.md", 2, "msg b")
    f2 = ValidationFinding("rule-a", "concepts/a.md", 10, "msg a2")
    f3 = ValidationFinding("rule-a", "concepts/a.md", 2, "msg a1")
    f4 = ValidationFinding("rule-a", "concepts/a.md", 2, "msg a0")

    assert f1.format() == "rule-b:concepts/b.md:2:msg b"
    assert sort_findings([f1, f2, f3, f4]) == (f4, f3, f2, f1)

    for kwargs in (
        {"rule": "", "path": "a.md", "line": 0, "message": "x"},
        {"rule": "r", "path": "", "line": 0, "message": "x"},
        {"rule": "r", "path": "a.md", "line": -1, "message": "x"},
        {"rule": "r", "path": "a.md", "line": True, "message": "x"},
        {"rule": "r", "path": "a.md", "line": 0, "message": ""},
    ):
        with pytest.raises(ValueError):
            ValidationFinding(**kwargs)  # type: ignore[arg-type]


def test_concept_source_and_card_invariants() -> None:
    src = ConceptSource(title="Book", citation="Ch. 1", kind="book")
    assert src.kind == "book"

    with pytest.raises(ValueError, match="title"):
        ConceptSource(title=" ", citation="Ch. 1", kind="book")
    with pytest.raises(ValueError, match="citation"):
        ConceptSource(title="Book", citation="", kind="book")
    with pytest.raises(ValueError, match="kind"):
        ConceptSource(title="Book", citation="Ch. 1", kind="blog")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="slug"):
        ConceptCard(
            slug="Bad_Slug",
            term="Term",
            aliases=(),
            inline_glimpse="Glimpse",
            visual_ref=None,
            where_analogy_breaks="Breaks",
            sources=(src,),
            lean_anchors=(),
            child_concepts=(),
            body_markdown="Body",
        )
    with pytest.raises(ValueError, match="visual_ref"):
        ConceptCard(
            slug="good-slug",
            term="Term",
            aliases=(),
            inline_glimpse="Glimpse",
            visual_ref="../escape.svg",
            where_analogy_breaks="Breaks",
            sources=(src,),
            lean_anchors=(),
            child_concepts=(),
            body_markdown="Body",
        )


# --- parse_concept_card pure parser ---


def test_parse_concept_card_valid_and_body_line_offset() -> None:
    text_with_blank = _make_card_text(
        slug="zeta-zero",
        extra_yaml=(
            "aliases:\n"
            "  - non-trivial zero\n"
            "visual_ref: assets/zeta.svg\n"
            "sources:\n"
            "  - title: Titchmarsh\n"
            "    citation: Ch. 2\n"
            "    kind: book\n"
            "lean_anchors:\n"
            "  - 00_statement/statements.lean#RiemannZetaZerosOnLine\n"
            "child_concepts:\n"
            "  - critical-strip\n"
        ),
        body="First body line.\nSecond body line.",
        leading_blank_line=True,
    )
    card, findings, offset = parse_concept_card(
        text_with_blank, expected_slug="zeta-zero", rel_path="concepts/zeta-zero.md"
    )
    assert findings == ()
    assert card is not None
    assert card.slug == "zeta-zero"
    assert card.aliases == ("non-trivial zero",)
    assert card.visual_ref == "assets/zeta.svg"
    assert card.sources == (ConceptSource("Titchmarsh", "Ch. 2", "book"),)
    assert card.lean_anchors == ("00_statement/statements.lean#RiemannZetaZerosOnLine",)
    assert card.child_concepts == ("critical-strip",)

    # Line number of "First body line." in original text equals offset + 1-based line in body.
    orig_lines = text_with_blank.splitlines()
    body_lines = card.body_markdown.splitlines()
    assert orig_lines[offset] == body_lines[0] == ""
    assert orig_lines[offset + 1] == body_lines[1] == "First body line."

    # Without blank line and with CRLF line endings.
    text_no_blank = _make_card_text(
        slug="zeta-zero", body="Immediate body.", leading_blank_line=False
    )
    crlf_text = text_no_blank.replace("\n", "\r\n")
    card2, findings2, offset2 = parse_concept_card(
        crlf_text, expected_slug="zeta-zero", rel_path="concepts/zeta-zero.md"
    )
    assert findings2 == ()
    assert card2 is not None
    assert offset2 == 6
    assert (
        crlf_text.splitlines()[offset2] == card2.body_markdown.splitlines()[0] == "Immediate body."
    )


def test_parse_concept_card_missing_delimiters_and_malformed_yaml() -> None:
    card, findings, offset = parse_concept_card(
        "No frontmatter here\n", expected_slug="demo", rel_path="concepts/demo.md"
    )
    assert card is None
    assert offset == 0
    assert [f.rule for f in findings] == ["concept-frontmatter"]

    unclosed = "---\nslug: demo\nterm: Demo\n"
    card2, findings2, _ = parse_concept_card(
        unclosed, expected_slug="demo", rel_path="concepts/demo.md"
    )
    assert card2 is None
    assert [f.rule for f in findings2] == ["concept-frontmatter"]

    malformed = "---\nslug: [unclosed\n---\nbody\n"
    card3, findings3, offset3 = parse_concept_card(
        malformed, expected_slug="demo", rel_path="concepts/demo.md"
    )
    assert card3 is None
    assert offset3 == 3
    assert [f.rule for f in findings3] == ["concept-yaml"]
    assert findings3[0].line >= 2

    non_mapping = "---\n- just\n- a\n- list\n---\nbody\n"
    card4, findings4, _ = parse_concept_card(
        non_mapping, expected_slug="demo", rel_path="concepts/demo.md"
    )
    assert card4 is None
    assert [f.rule for f in findings4] == ["concept-frontmatter"]


def test_parse_concept_card_unknown_and_body_markdown_and_missing_required_keys() -> None:
    text = "---\nslug: demo\nbody_markdown: forbidden\nextra_field: unexpected\n---\nBody\n"
    card, findings, _ = parse_concept_card(text, expected_slug="demo", rel_path="concepts/demo.md")
    assert card is None
    messages = [f.message for f in findings]
    assert any("body_markdown must not be set" in msg for msg in messages)
    assert any("unknown frontmatter key 'extra_field'" in msg for msg in messages)
    for req in ("term", "inline_glimpse", "where_analogy_breaks"):
        assert any(f"missing required frontmatter key '{req}'" in msg for msg in messages)

    no_slug = "---\nterm: T\ninline_glimpse: G\nwhere_analogy_breaks: W\n---\nBody\n"
    card2, findings2, _ = parse_concept_card(
        no_slug, expected_slug="demo", rel_path="concepts/demo.md"
    )
    assert card2 is None
    assert any("missing required frontmatter key 'slug'" in f.message for f in findings2)


def test_parse_concept_card_slug_visual_ref_and_sources_errors() -> None:
    bad_slug = _make_card_text(slug="Bad_Slug")
    card, findings, _ = parse_concept_card(
        bad_slug, expected_slug="Bad_Slug", rel_path="concepts/Bad_Slug.md"
    )
    assert card is None
    assert [f.rule for f in findings] == ["concept-slug"]

    mismatch = _make_card_text(slug="other-slug")
    card2, findings2, _ = parse_concept_card(
        mismatch, expected_slug="expected-slug", rel_path="concepts/expected-slug.md"
    )
    assert card2 is None
    assert [f.rule for f in findings2] == ["concept-slug"]
    assert "does not match filename" in findings2[0].message

    for bad_ref in ("/abs/path.svg", "../escape.svg", "a\\b.svg", "a//b.svg", ""):
        card_v, findings_v, _ = parse_concept_card(
            _make_card_text(extra_yaml=f"visual_ref: '{bad_ref}'"),
            expected_slug="demo-concept",
            rel_path="concepts/demo-concept.md",
        )
        assert card_v is None
        assert [f.rule for f in findings_v] == ["concept-visual-ref"]

    bad_sources = _make_card_text(
        extra_yaml=(
            "sources:\n"
            "  - not-a-mapping\n"
            "  - title: Missing kind\n"
            "    citation: Ch. 1\n"
            "  - title: ''\n"
            "    citation: ' '\n"
            "    kind: wiki\n"
        )
    )
    card_s, findings_s, _ = parse_concept_card(
        bad_sources, expected_slug="demo-concept", rel_path="concepts/demo-concept.md"
    )
    assert card_s is None
    assert all(f.rule == "concept-source" for f in findings_s)
    assert len(findings_s) == 5

    not_list_sources = _make_card_text(extra_yaml="sources: Titchmarsh")
    card_s2, findings_s2, _ = parse_concept_card(
        not_list_sources, expected_slug="demo-concept", rel_path="concepts/demo-concept.md"
    )
    assert card_s2 is None
    assert [f.rule for f in findings_s2] == ["concept-source"]


# --- validate_concept_corpus ---


def test_validate_corpus_missing_empty_and_non_directory(tmp_path: Path) -> None:
    cards, findings = validate_concept_corpus(tmp_path, skeleton_root=REAL_SKELETON_ROOT)
    assert cards == {}
    assert findings == ()

    (tmp_path / "concepts").mkdir()
    cards2, findings2 = validate_concept_corpus(tmp_path, skeleton_root=REAL_SKELETON_ROOT)
    assert cards2 == {}
    assert findings2 == ()

    bad_docs = tmp_path / "bad_docs"
    bad_docs.mkdir()
    (bad_docs / "concepts").write_text("not a directory", encoding="utf-8")
    cards3, findings3 = validate_concept_corpus(bad_docs, skeleton_root=REAL_SKELETON_ROOT)
    assert cards3 == {}
    assert [f.rule for f in findings3] == ["concept-dir"]


def test_validate_corpus_synthetic_fixtures_with_mutual_cycle() -> None:
    cards, findings = validate_concept_corpus(
        FIXTURE_ROOT / "docs", skeleton_root=FIXTURE_ROOT / "skeleton"
    )
    assert findings == ()
    assert sorted(cards) == ["critical-strip", "riemann-zeta"]
    assert cards["riemann-zeta"].child_concepts == ("critical-strip",)
    assert cards["critical-strip"].child_concepts == ("riemann-zeta",)


def test_validate_corpus_real_skeleton_anchors(tmp_path: Path) -> None:
    _write_card(
        tmp_path,
        "riemann-hypothesis.md",
        _make_card_text(
            slug="riemann-hypothesis",
            extra_yaml=(
                "lean_anchors:\n"
                "  - 00_statement/statements.lean#RiemannZetaZerosOnLine\n"
                "  - 00_statement/formal_conjectures_challenge.lean"
                "#GRH.generalized_riemann_hypothesis\n"
            ),
        ),
    )
    cards, findings = validate_concept_corpus(tmp_path, skeleton_root=REAL_SKELETON_ROOT)
    assert findings == ()
    assert list(cards) == ["riemann-hypothesis"]


def test_validate_corpus_sourcing_rules(tmp_path: Path) -> None:
    _write_card(tmp_path, "unsourced.md", _make_card_text(slug="unsourced"))
    _write_card(
        tmp_path,
        "sources-only.md",
        _make_card_text(
            slug="sources-only",
            extra_yaml=("sources:\n  - title: Titchmarsh\n    citation: Ch. 1\n    kind: book\n"),
        ),
    )
    _write_card(
        tmp_path,
        "anchors-only.md",
        _make_card_text(
            slug="anchors-only",
            extra_yaml="lean_anchors:\n  - 00_statement/statements.lean#RiemannZetaZerosOnLine\n",
        ),
    )

    cards, findings = validate_concept_corpus(tmp_path, skeleton_root=REAL_SKELETON_ROOT)
    assert sorted(cards) == ["anchors-only", "sources-only", "unsourced"]
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("concept-unsourced", "concepts/unsourced.md", 0)
    ]


def test_validate_corpus_visual_ref_states(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "assets").mkdir(parents=True)
    (docs_root / "assets" / "ok.svg").write_text("<svg/>", encoding="utf-8")
    outside = tmp_path / "outside.svg"
    outside.write_text("<svg/>", encoding="utf-8")
    (docs_root / "assets" / "escape.svg").symlink_to(outside)

    source_yaml = "sources:\n  - title: Ref\n    citation: Sec. 1\n    kind: reference\n"
    _write_card(
        docs_root,
        "with-visual.md",
        _make_card_text(slug="with-visual", extra_yaml=source_yaml + "visual_ref: assets/ok.svg\n"),
    )
    _write_card(
        docs_root,
        "no-visual.md",
        _make_card_text(slug="no-visual", extra_yaml=source_yaml),
    )
    _write_card(
        docs_root,
        "missing-visual.md",
        _make_card_text(
            slug="missing-visual", extra_yaml=source_yaml + "visual_ref: assets/nope.svg\n"
        ),
    )
    _write_card(
        docs_root,
        "symlink-escape.md",
        _make_card_text(
            slug="symlink-escape", extra_yaml=source_yaml + "visual_ref: assets/escape.svg\n"
        ),
    )

    _, findings = validate_concept_corpus(docs_root, skeleton_root=REAL_SKELETON_ROOT)
    assert [(f.rule, f.path) for f in findings] == [
        ("concept-missing-visual", "concepts/missing-visual.md"),
        ("concept-missing-visual", "concepts/symlink-escape.md"),
    ]


def test_validate_corpus_lean_anchor_validation_and_parse_errors(tmp_path: Path) -> None:
    skeleton_root = tmp_path / "skeleton"
    (skeleton_root / "00_statement").mkdir(parents=True)
    (skeleton_root / "00_statement" / "ok.lean").write_text(
        "def GoodDecl : Prop := True\n", encoding="utf-8"
    )
    (skeleton_root / "00_statement" / "broken.lean").write_text(
        "local def BadSyntax : Prop := True\n", encoding="utf-8"
    )
    outside_lean = tmp_path / "outside.lean"
    outside_lean.write_text("def Escaped : Prop := True\n", encoding="utf-8")
    (skeleton_root / "00_statement" / "escape.lean").symlink_to(outside_lean)

    docs_root = tmp_path / "docs"
    _write_card(
        docs_root,
        "bad-anchors.md",
        _make_card_text(
            slug="bad-anchors",
            extra_yaml=(
                "lean_anchors:\n"
                "  - 00_statement/ok.lean\n"
                "  - 00_statement/ok.lean#A#B\n"
                "  - skeleton/00_statement/ok.lean#GoodDecl\n"
                "  - ../00_statement/ok.lean#GoodDecl\n"
                "  - /00_statement/ok.lean#GoodDecl\n"
                "  - 00_statement\\ok.lean#GoodDecl\n"
                "  - 00_statement/ok.lean#Bad Decl\n"
                "  - 00_statement/ok.lean#\n"
                "  - 00_statement/missing.lean#GoodDecl\n"
                "  - 00_statement/escape.lean#Escaped\n"
                "  - 00_statement/ok.lean#NoSuchDecl\n"
                "  - 00_statement/broken.lean#BadSyntax\n"
            ),
        ),
    )

    _, findings = validate_concept_corpus(docs_root, skeleton_root=skeleton_root)
    rules = [f.rule for f in findings]
    assert rules.count("lean-anchor-format") == 8
    assert rules.count("lean-anchor-missing-file") == 2
    assert rules.count("lean-anchor-missing-decl") == 1
    assert rules.count("lean-anchor-parse-error") == 1
    assert len(findings) == 12


def test_validate_corpus_caches_skeleton_file_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skeleton_root = tmp_path / "skeleton"
    (skeleton_root / "00_statement").mkdir(parents=True)
    (skeleton_root / "00_statement" / "shared.lean").write_text(
        "def Alpha : Prop := True\ndef Beta : Prop := True\n", encoding="utf-8"
    )
    (skeleton_root / "00_statement" / "broken.lean").write_text(
        "local def Broken : Prop := True\n", encoding="utf-8"
    )

    calls: list[str] = []

    def spy_extract(source: str, *, rel_path: str, commit: str) -> ExtractedModule:
        calls.append(rel_path)
        return extract_declarations(source, rel_path=rel_path, commit=commit)

    monkeypatch.setattr(concepts_validator, "extract_declarations", spy_extract)

    docs_root = tmp_path / "docs"
    _write_card(
        docs_root,
        "card-one.md",
        _make_card_text(
            slug="card-one",
            extra_yaml=(
                "lean_anchors:\n"
                "  - 00_statement/shared.lean#Alpha\n"
                "  - 00_statement/broken.lean#Broken\n"
            ),
        ),
    )
    _write_card(
        docs_root,
        "card-two.md",
        _make_card_text(
            slug="card-two",
            extra_yaml=(
                "lean_anchors:\n"
                "  - 00_statement/shared.lean#Beta\n"
                "  - 00_statement/broken.lean#Broken\n"
            ),
        ),
    )

    _, findings = validate_concept_corpus(docs_root, skeleton_root=skeleton_root)
    assert calls == ["00_statement/shared.lean", "00_statement/broken.lean"]
    assert [(f.rule, f.path) for f in findings] == [
        ("lean-anchor-parse-error", "concepts/card-one.md"),
        ("lean-anchor-parse-error", "concepts/card-two.md"),
    ]


def test_validate_corpus_child_concepts_dangling_self_and_mutual_cycle(tmp_path: Path) -> None:
    source_yaml = "sources:\n  - title: Ref\n    citation: Ch. 1\n    kind: book\n"
    _write_card(
        tmp_path,
        "alpha.md",
        _make_card_text(
            slug="alpha",
            extra_yaml=source_yaml + "child_concepts:\n  - beta\n  - alpha\n  - ghost\n",
        ),
    )
    _write_card(
        tmp_path,
        "beta.md",
        _make_card_text(slug="beta", extra_yaml=source_yaml + "child_concepts:\n  - alpha\n"),
    )

    _, findings = validate_concept_corpus(tmp_path, skeleton_root=REAL_SKELETON_ROOT)
    assert [(f.rule, f.path) for f in findings] == [
        ("concept-missing-child", "concepts/alpha.md"),
        ("concept-self-cycle", "concepts/alpha.md"),
    ]


def test_validate_corpus_read_errors_and_duplicate_slug_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "folder.md").mkdir()
    (concepts_dir / "bad-utf8.md").write_bytes(b"\xff\xfe\xfd")

    _, findings = validate_concept_corpus(tmp_path, skeleton_root=REAL_SKELETON_ROOT)
    assert [(f.rule, f.path) for f in findings] == [
        ("concept-read", "concepts/bad-utf8.md"),
        ("concept-read", "concepts/folder.md"),
    ]

    dup_docs = tmp_path / "dup"
    _write_card(dup_docs, "first.md", _make_card_text(slug="first"))
    _write_card(dup_docs, "second.md", _make_card_text(slug="second"))
    fixed_card = ConceptCard(
        slug="same",
        term="Same",
        aliases=(),
        inline_glimpse="G",
        visual_ref=None,
        where_analogy_breaks="W",
        sources=(ConceptSource("Book", "Ch. 1", "book"),),
        lean_anchors=(),
        child_concepts=(),
        body_markdown="Body",
    )

    def fake_parse(
        *args: Any, **kwargs: Any
    ) -> tuple[ConceptCard, tuple[ValidationFinding, ...], int]:
        return fixed_card, (), 0

    monkeypatch.setattr(concepts_validator, "parse_concept_card", fake_parse)
    cards, dup_findings = validate_concept_corpus(dup_docs, skeleton_root=REAL_SKELETON_ROOT)
    assert list(cards) == ["same"]
    assert [(f.rule, f.path) for f in dup_findings] == [
        ("concept-duplicate-slug", "concepts/second.md")
    ]
