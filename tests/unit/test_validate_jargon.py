"""Unit tests for the Explainer Directive & Level-1 Jargon Validator."""

from __future__ import annotations

from pathlib import Path

from rh_pipeline.models.concepts import ConceptCard
from rh_pipeline.models.validation import ValidationFinding
from rh_pipeline.validate.jargon import check_page_jargon, validate_explainer_docs


def _card(slug: str, term: str, *, aliases: tuple[str, ...] = ()) -> ConceptCard:
    return ConceptCard(
        slug=slug,
        term=term,
        aliases=aliases,
        inline_glimpse=f"Plain glimpse of {term}.",
        visual_ref=None,
        where_analogy_breaks=f"Where {term} analogy breaks.",
        sources=(),
        lean_anchors=(),
        child_concepts=(),
        body_markdown="",
    )


def _registry() -> dict[str, ConceptCard]:
    cards = (
        _card(
            "dirichlet-l-function",
            "Dirichlet L-function",
            aliases=("Dirichlet L-series", "twisted L-series"),
        ),
        _card("l-function", "L-function"),
        _card("critical-strip", "critical strip"),
        _card("analytic", "analytic"),
        _card("branch", "branch", aliases=("branch cut",)),
        _card("dirichlet-series", "Dirichlet series"),
    )
    return {card.slug: card for card in cards}


def test_conforming_level1_prose_passes() -> None:
    markdown = (
        "# Stage Overview\n"
        "\n"
        "## Level 1 — First-Principles\n"
        "We start with {{concept:dirichlet-l-function|a sum weighted by periodic signs}}.\n"
        "Later we refer to {{term:dirichlet-l-function|Dirichlet L-functions}} and also\n"
        "plain Dirichlet L-function, Dirichlet L-functions, or Dirichlet L-series.\n"
    )
    findings = check_page_jargon(
        markdown, rel_path="stages/01_intro/overview.md", registry=_registry()
    )
    assert findings == ()


def test_raw_term_alias_plurals_and_premature_term_directive_emit_exact_lines() -> None:
    markdown = (
        "## Level 1 — Plain Idea\n"
        "Line 2 mentions Dirichlet L-function too early.\n"
        "Line 3 mentions twisted L-series as an alias.\n"
        "Line 4 mentions critical strips as an -s plural.\n"
        "Line 5 mentions branches as an -es plural.\n"
        "Line 6 uses {{term:dirichlet-series}} prematurely.\n"
        "Line 7 uses {{term:dirichlet-series|Dirichlet series}} prematurely.\n"
    )
    findings = check_page_jargon(markdown, rel_path="stages/01_intro/page.md", registry=_registry())
    assert findings == (
        ValidationFinding(
            "jargon-before-reveal",
            "stages/01_intro/page.md",
            2,
            "term 'Dirichlet L-function' (dirichlet-l-function) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "stages/01_intro/page.md",
            3,
            "term 'twisted L-series' (dirichlet-l-function) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "stages/01_intro/page.md",
            4,
            "term 'critical strips' (critical-strip) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "stages/01_intro/page.md",
            5,
            "term 'branches' (branch) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "stages/01_intro/page.md",
            6,
            (
                "term directive '{{term:dirichlet-series}}' (dirichlet-series) "
                "used before inline reveal"
            ),
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "stages/01_intro/page.md",
            7,
            (
                "term directive '{{term:dirichlet-series}}' (dirichlet-series) "
                "used before inline reveal"
            ),
        ),
    )


def test_same_line_before_vs_after_reveal_and_multiline_whitespace() -> None:
    markdown = (
        "## Level 1 — Ordering\n"
        "Before: critical strip then {{concept:critical-strip|vertical band}} and critical strip.\n"
        "Reveal first: {{concept:dirichlet-l-function|a twisted sum}} then Dirichlet\n"
        "  L-function and {{term:dirichlet-l-function}}.\n"
    )
    findings = check_page_jargon(
        markdown, rel_path="stages/01_intro/order.md", registry=_registry()
    )
    assert findings == (
        ValidationFinding(
            "jargon-before-reveal",
            "stages/01_intro/order.md",
            2,
            "term 'critical strip' (critical-strip) used before inline reveal",
        ),
    )


def test_concept_directive_math_pipe_latex_braces_and_inline_code() -> None:
    markdown = (
        "## Level 1 — Formulas Inside Reveals\n"
        "Consider {{concept:critical-strip|the band where $|z| < 1$}} and\n"
        "{{concept:dirichlet-series|the sum $\\frac{1}{n^{s}}$ over integers}}.\n"
        "Also allow {single braces} in {{concept:branch|a {chosen} sheet $$\\frac{1}{n^{s}}$$}}.\n"
        "Brace touching close {{concept:analytic|locally {smooth}}} and "
        "{{concept:l-function|`L(s)`}}.\n"
        "Afterward we can say critical strip, Dirichlet series, branch, analytic, and L-function.\n"
    )
    findings = check_page_jargon(markdown, rel_path="stages/02_math/math.md", registry=_registry())
    assert findings == ()


def test_unrevealed_term_inside_other_concept_inline_text_is_caught() -> None:
    markdown = (
        "## Level 1 — Nested Mention\n"
        "We define {{concept:dirichlet-l-function|a Dirichlet L-function on the critical strip}}.\n"
    )
    findings = check_page_jargon(markdown, rel_path="stages/02_math/nest.md", registry=_registry())
    assert findings == (
        ValidationFinding(
            "jargon-before-reveal",
            "stages/02_math/nest.md",
            2,
            "term 'critical strip' (critical-strip) used before inline reveal",
        ),
    )


def test_unknown_concept_and_invalid_directive_in_all_levels() -> None:
    markdown = (
        "## Level 1 — Intuition\n"
        "Bad syntax {{concept:critical-strip}} here.\n"
        "Empty slug {{concept:|some text}} and empty text {{concept:analytic|   }}.\n"
        "Unknown L1 {{concept:missing-l1|some explanation}}.\n"
        "## Level 2 — Undergrad Bridge\n"
        "Unknown L2 {{term:missing-l2}}.\n"
        "## Level 3 — Formal Lean Contract\n"
        "Unknown L3 {{concept:missing-l3|formal note}} and {{term:missing-l3-term|display}}.\n"
        "Empty term {{term:}} and empty display {{term:analytic|  }}.\n"
        "Unclosed {{concept:analytic|no closing braces\n"
    )
    findings = check_page_jargon(markdown, rel_path="stages/03_errors/bad.md", registry=_registry())
    got = [(f.rule, f.line) for f in findings]
    assert got == [
        ("invalid-directive", 2),
        ("invalid-directive", 3),
        ("invalid-directive", 3),
        ("unknown-concept", 4),
        ("unknown-concept", 6),
        ("unknown-concept", 8),
        ("unknown-concept", 8),
        ("invalid-directive", 9),
        ("invalid-directive", 9),
        ("invalid-directive", 10),
    ]


def test_empty_registry_validates_directives_without_spurious_matches() -> None:
    markdown = "## Level 1 — Empty\nPlain text and {{term:unknown-slug}} and {{concept:bad}}.\n"
    findings = check_page_jargon(markdown, rel_path="stages/00/empty.md", registry={})
    assert [(f.rule, f.line) for f in findings] == [
        ("invalid-directive", 2),
        ("unknown-concept", 2),
    ]


def test_exemptions_word_boundaries_math_code_links_and_sections() -> None:
    markdown = (
        "Pre-L1 intro mentions critical strip without triggering.\n"
        "```markdown\n"
        "## Level 1 — Fake Heading Inside Fence\n"
        "Dirichlet L-function inside fenced code.\n"
        "```\n"
        "## Level 1 — Real Level 1\n"
        "Word boundary: non-analytic and analytic-ish stay exempt.\n"
        "Inline math $Dirichlet L-function$ and escaped \\$ keeps $analytic$ masked.\n"
        "Display math:\n"
        "$$\n"
        "Dirichlet L-function + critical strip\n"
        "$$\n"
        "Inline code `Dirichlet L-function` and ``critical strip with ` backtick`` are exempt.\n"
        "Tilde fence:\n"
        "~~~lean\n"
        "def analytic := critical strip\n"
        "~~~\n"
        "Link URL exempt: [plain label](/concepts/dirichlet-l-function) and\n"
        "[another label](https://example.org/wiki/Branch_(critical_strip)/analytic).\n"
        "### Nested Level-1 Subsection\n"
        "Link label IS checked: [Dirichlet L-function](/concepts/dirichlet-l-function).\n"
        "## Level 2 — Undergrad Bridge\n"
        "Level 2 freely mentions Dirichlet L-function, analytic, and {{term:critical-strip}}.\n"
        "## Level 3 — Formal Lean Contract\n"
        "Level 3 freely mentions Dirichlet series and {{term:branch}}.\n"
    )
    findings = check_page_jargon(markdown, rel_path="stages/04_exempt/ok.md", registry=_registry())
    assert findings == (
        ValidationFinding(
            "jargon-before-reveal",
            "stages/04_exempt/ok.md",
            21,
            "term 'Dirichlet L-function' (dirichlet-l-function) used before inline reveal",
        ),
    )


def test_concept_card_own_slug_level_slicing_and_line_offset() -> None:
    body = (
        "A Dirichlet L-function is introduced right away on its own card (not L-function).\n"
        "But mentioning critical strip before reveal is caught.\n"
        "## Where this analogy breaks\n"
        "Still Level 1 before Level 2/3, so branch is caught too.\n"
        "## Level 2 — Undergrad Bridge\n"
        "Here analytic and Dirichlet series are exempt.\n"
        "## References\n"
        "Non-Level-1 section after Level 2 keeps analytic exempt.\n"
        "## Level 1 — Recap\n"
        "Explicit Level 1 re-activates checks for Dirichlet series.\n"
    )
    findings = check_page_jargon(
        body,
        rel_path="concepts/dirichlet-l-function.md",
        registry=_registry(),
        own_slug="dirichlet-l-function",
        line_offset=8,
    )
    assert findings == (
        ValidationFinding(
            "jargon-before-reveal",
            "concepts/dirichlet-l-function.md",
            9,
            "term 'L-function' (l-function) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "concepts/dirichlet-l-function.md",
            10,
            "term 'critical strip' (critical-strip) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "concepts/dirichlet-l-function.md",
            12,
            "term 'branch' (branch) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "concepts/dirichlet-l-function.md",
            18,
            "term 'Dirichlet series' (dirichlet-series) used before inline reveal",
        ),
    )


def test_own_slug_compound_term_blocks_shorter_subterm_match() -> None:
    body = "A Dirichlet L-function is introduced right away on its own card.\n"
    findings = check_page_jargon(
        body,
        rel_path="concepts/dirichlet-l-function.md",
        registry=_registry(),
        own_slug="dirichlet-l-function",
        line_offset=5,
    )
    assert findings == ()


def test_crlf_line_endings_preserve_exact_line_numbers() -> None:
    markdown = "## Level 1 — CRLF\r\nFirst clean line.\r\nThird line has analytic.\r\n"
    findings = check_page_jargon(markdown, rel_path="stages/05_crlf/page.md", registry=_registry())
    assert findings == (
        ValidationFinding(
            "jargon-before-reveal",
            "stages/05_crlf/page.md",
            3,
            "term 'analytic' (analytic) used before inline reveal",
        ),
    )


def test_validate_explainer_docs_scans_strictly_concepts_and_stages(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "concepts").mkdir(parents=True)
    (docs_root / "stages" / "00_statement").mkdir(parents=True)
    (docs_root / "stages" / "01_stage" / "nested").mkdir(parents=True)
    (docs_root / "onboarding").mkdir(parents=True)

    (docs_root / "concepts" / "analytic.md").write_text(
        "---\n"
        "slug: analytic\n"
        "term: analytic\n"
        "inline_glimpse: Locally given by power series.\n"
        "where_analogy_breaks: Real smoothness is weaker.\n"
        "---\n"
        "An analytic function is smooth everywhere.\n"
        "It connects to the critical strip.\n",
        encoding="utf-8",
    )
    (docs_root / "concepts" / "broken.md").write_text(
        "Not valid frontmatter mentioning Dirichlet L-function.\n",
        encoding="utf-8",
    )
    (docs_root / "stages" / "00_statement" / "overview.md").write_text(
        "## Level 1 — Statement\n"
        "Uses {{concept:critical-strip|vertical band}} then critical strip.\n"
        "Unrevealed branch here.\n",
        encoding="utf-8",
    )
    (docs_root / "onboarding" / "notes.md").write_text(
        "## Level 1 — Onboarding\nDirichlet L-function and {{concept:unknown-onboarding|bad}}.\n",
        encoding="utf-8",
    )
    (docs_root / "stages" / "top_level.md").write_text(
        "## Level 1 — Top\nDirichlet L-function.\n",
        encoding="utf-8",
    )
    (docs_root / "stages" / "01_stage" / "nested" / "deep.md").write_text(
        "## Level 1 — Deep\nDirichlet L-function.\n",
        encoding="utf-8",
    )
    (docs_root / "README.md").write_text(
        "## Level 1 — Root\nDirichlet L-function.\n",
        encoding="utf-8",
    )

    findings = validate_explainer_docs(docs_root, registry=_registry())
    assert findings == (
        ValidationFinding(
            "jargon-before-reveal",
            "concepts/analytic.md",
            8,
            "term 'critical strip' (critical-strip) used before inline reveal",
        ),
        ValidationFinding(
            "jargon-before-reveal",
            "stages/00_statement/overview.md",
            3,
            "term 'branch' (branch) used before inline reveal",
        ),
    )


def test_validate_explainer_docs_handles_missing_directories(tmp_path: Path) -> None:
    assert validate_explainer_docs(tmp_path / "empty_docs", registry=_registry()) == ()
