"""Unit tests for the VitePress markdown transforms, site compiler, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rh_pipeline.cli.build_site import main as build_site_main
from rh_pipeline.models.audit import StaticScanSummary, VerificationStatus
from rh_pipeline.models.concepts import ConceptCard, ConceptSource
from rh_pipeline.site import (
    SiteBuildConfig,
    SiteBuildError,
    SiteDirectiveError,
    compile_site,
    expand_concept_directives,
    normalize_base_url,
    render_page_glossary,
    split_zoom_levels,
)
from rh_pipeline.site.renderers import render_verification_banner

COMMIT = "a" * 40


def _card(
    slug: str,
    term: str,
    *,
    glimpse: str = "A crisp one-line summary.",
    visual_ref: str | None = None,
) -> ConceptCard:
    return ConceptCard(
        slug=slug,
        term=term,
        aliases=(),
        inline_glimpse=glimpse,
        visual_ref=visual_ref,
        where_analogy_breaks="Analogy breaks at the boundary.",
        sources=(ConceptSource(title="Ref", citation="Sec. 1", kind="book"),),
        lean_anchors=(),
        child_concepts=(),
        body_markdown="Body text.",
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_fixture(
    tmp_path: Path, *, commit: str = COMMIT, condensed: bool = False
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    memory = tmp_path / "memory"
    out = tmp_path / "out_docs"

    ver = {
        "schema_version": "1.0.0",
        "upstream_commit": commit,
        "lean_toolchain": "leanprover/lean4:v4.37.0-rc1",
        "mathlib_rev": "b" * 40,
        "build_status": "unverified-locally",
        "scan_scope": "entry",
        "files_scanned": ["src/Statements.lean", "src/Main.lean"],
        "solution_forbidden_token_count": 0,
        "challenge_sorry_count": 2,
        "theorem_names": ["riemannHypothesis", "riemannZeta_eq_zero_re"],
        "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
        "community_verification_notes_sha256": None if condensed else "c" * 64,
        "upstream_static_scan_sha256": None if condensed else "d" * 64,
        "scan": {
            "scan_scope": "entry",
            "files_scanned": ["src/Statements.lean", "src/Main.lean"],
            "solution_findings": [],
            "challenge_findings": [],
            "challenge_sorry_count": 2,
        },
    }
    _write(repo / "audit" / "verification-status.json", json.dumps(ver, indent=2) + "\n")

    stmt_lean = (
        "-- AUTO-GENERATED\n\ndef RiemannZetaZerosOnLine : Prop :=\n  ∀ s : \u2102, s.re = 1 / 2\n"
    )
    main_lean = (
        "-- AUTO-GENERATED\n\ntheorem riemannHypothesis : RiemannZetaZerosOnLine := by\n  sorry\n"
    )
    _write(repo / "skeleton" / "00_statement" / "statements.lean", stmt_lean)
    _write(repo / "skeleton" / "00_statement" / "main.lean", main_lean)

    dag = {
        "schema_version": "rh-dag/v1",
        "upstream_commit": COMMIT,
        "stages": [
            {
                "stage_id": "00_statement",
                "order": 0,
                "title": "Statement and Entry Point",
                "summary": "Core RH statement.",
                "files": [
                    {
                        "upstream_path": "src/Statements.lean",
                        "skeleton_file": "00_statement/statements.lean",
                        "preserve_defs": True,
                    },
                    {
                        "upstream_path": "src/Main.lean",
                        "skeleton_file": "00_statement/main.lean",
                        "preserve_defs": False,
                    },
                ],
            }
        ],
        "nodes": [
            {
                "id": "00_statement/statements",
                "stage_id": "00_statement",
                "title": "Statements",
                "skeleton_file": "00_statement/statements.lean",
                "upstream_path": "src/Statements.lean",
                "declarations": [
                    {
                        "name": "RiemannZetaZerosOnLine",
                        "kind": "def",
                        "start_line": 3,
                        "end_line": 4,
                        "upstream_path": "src/Statements.lean",
                        "upstream_start_line": 7,
                        "upstream_end_line": 8,
                    }
                ],
                "depends_on": [],
                "is_landmark": False,
            },
            {
                "id": "00_statement/main",
                "stage_id": "00_statement",
                "title": "Main",
                "skeleton_file": "00_statement/main.lean",
                "upstream_path": "src/Main.lean",
                "declarations": [
                    {
                        "name": "riemannHypothesis",
                        "kind": "theorem",
                        "start_line": 3,
                        "end_line": 4,
                        "upstream_path": "src/Main.lean",
                        "upstream_start_line": 46,
                        "upstream_end_line": 47,
                    }
                ],
                "depends_on": ["00_statement/statements"],
                "is_landmark": True,
            },
        ],
    }
    _write(repo / "dag" / "dag.json", json.dumps(dag, indent=2) + "\n")

    _write(
        memory / "docs" / "assets" / "critical-strip.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10"/></svg>\n',
    )
    _write(
        memory / "docs" / "concepts" / "critical-line.md",
        "---\n"
        "slug: critical-line\n"
        'term: "Critical Line"\n'
        "aliases:\n"
        '  - "half-line"\n'
        'inline_glimpse: "The vertical line $\\\\operatorname{Re}(s) = 1/2$ in the plane."\n'
        "visual_ref: assets/critical-strip.svg\n"
        'where_analogy_breaks: "The line is an algebraic axis of symmetry, not a physical wire."\n'
        "sources:\n"
        '  - title: "Titchmarsh"\n'
        '    citation: "Ch. 2"\n'
        "    kind: book\n"
        "lean_anchors:\n"
        '  - "00_statement/statements.lean#RiemannZetaZerosOnLine"\n'
        "child_concepts:\n"
        "  - riemann-zeta\n"
        "---\n"
        "\n"
        "The critical line carries the non-trivial zeros of "
        "{{concept:riemann-zeta|the zeta function}}.\n",
    )
    _write(
        memory / "docs" / "concepts" / "riemann-zeta.md",
        "---\n"
        "slug: riemann-zeta\n"
        'term: "Riemann Zeta Function"\n'
        'inline_glimpse: "Analytic continuation of $\\\\sum_{n=1}^{\\\\infty} n^{-s}$."\n'
        'where_analogy_breaks: "The Dirichlet series diverges at $s = 1/2$."\n'
        "sources:\n"
        '  - title: "Mathlib"\n'
        '    citation: "Mathlib.NumberTheory.LSeries.RiemannZeta"\n'
        "    kind: mathlib\n"
        "lean_anchors:\n"
        '  - "00_statement/main.lean#riemannHypothesis"\n'
        "---\n"
        "\n"
        "Defined across the complex plane except $s = 1$.\n",
    )
    _write(
        memory / "docs" / "stages" / "00_statement" / "overview.md",
        "# Stage 00: Statement Overview\n"
        "\n"
        "<!-- rh:stage-minimap:00_statement -->\n"
        "\n"
        "![Strip](assets/critical-strip.svg)\n"
        '<img src="assets/critical-strip.svg" alt="Strip raw" />\n'
        "\n"
        "## Level 1 — Intuition\n"
        "\n"
        "Every non-trivial zero lies on "
        "{{concept:critical-line|a single vertical line of symmetry}}. "
        "Revisit {{term:critical-line|that symmetry line}} and "
        "{{concept:riemann-zeta|the prime-encoding function}}.\n"
        "\n"
        "## Level 2 — Mathematical Blueprint\n"
        "\n"
        "Using {{term:riemann-zeta}}, the zeros have real part $1/2$.\n"
        "\n"
        "## Level 3 — Formal Lean Statement\n"
        "\n"
        "<!-- rh:lean-node:00_statement/statements -->\n"
        "\n"
        "<!-- rh:lean-node:00_statement/main -->\n"
        "\n"
        "## Closing Notes\n"
        "\n"
        "See the upstream repository for full proofs.\n",
    )
    return repo, memory, out


def test_normalize_base_url() -> None:
    assert normalize_base_url("/") == "/"
    assert normalize_base_url("ar-rh-proof-skeleton") == "/ar-rh-proof-skeleton/"
    assert normalize_base_url("/a/b/") == "/a/b/"
    for bad in ("", "   ", "/../secret/", "/a/./b/", "/a//b/", r"\bad"):
        with pytest.raises(SiteDirectiveError):
            normalize_base_url(bad)


def test_expand_concept_directives_rules_and_escaping() -> None:
    reg = {
        "zeta": _card(
            "zeta",
            'Riemann "Zeta" <Function>',
            glimpse='Values & zeros of "zeta"',
            visual_ref="assets/zeta.svg",
        ),
        "strip": _card("strip", "Critical Strip", glimpse="0 < Re(s) < 1"),
    }
    md = (
        "Intro {{concept:zeta|prose with $|s| > 1$, $\\sum_{n=1}^{\\infty} n^{-s}$, "
        "and {balanced} braces}}.\n"
        "Hover {{term:zeta}} and custom {{term:strip|the strip}} and repeat {{term:zeta}}.\n"
        "Inline code `{{term:unknown}}` and math ${{concept:unknown|bad}}$ "
        "and $$\\frac{1}{2} {{term:unknown}}$$.\n"
        "```md\n{{concept:unknown|fenced}}\n```\n"
        "~~~\n{{term:unknown}}\n~~~\n"
    )
    expanded, refs = expand_concept_directives(md, reg, base_url="/site/")
    assert refs == ("zeta", "strip")
    assert (
        '<ConceptReveal slug="zeta" term="Riemann &quot;Zeta&quot; &lt;Function&gt;" '
        'href="/site/concepts/zeta">prose with $|s| > 1$, $\\sum_{n=1}^{\\infty} n^{-s}$, '
        "and {balanced} braces</ConceptReveal>"
    ) in expanded
    assert (
        '<ConceptHover slug="zeta" term="Riemann &quot;Zeta&quot; &lt;Function&gt;" '
        'display="Riemann &quot;Zeta&quot; &lt;Function&gt;" '
        'glimpse="Values &amp; zeros of &quot;zeta&quot;" href="/site/concepts/zeta" '
        'visual-src="/site/assets/zeta.svg" '
        'visual-alt="Diagram illustrating Riemann &quot;Zeta&quot; &lt;Function&gt;" />'
    ) in expanded
    assert (
        '<ConceptHover slug="strip" term="Critical Strip" display="the strip" '
        'glimpse="0 &lt; Re(s) &lt; 1" href="/site/concepts/strip" />'
    ) in expanded
    assert "`{{term:unknown}}`" in expanded
    assert "${{concept:unknown|bad}}$" in expanded
    assert "```md\n{{concept:unknown|fenced}}\n```" in expanded
    assert "~~~\n{{term:unknown}}\n~~~" in expanded


@pytest.mark.parametrize(
    "bad_md",
    [
        "{{concept:missing|prose}}",
        "{{term:missing}}",
        "{{concept:zeta|unclosed",
        "{{term:zeta",
        "{{concept:zeta}}",
        "{{concept:|prose}}",
        "{{concept:zeta|   }}",
        "{{term:}}",
        "{{term:zeta|   }}",
    ],
)
def test_expand_concept_directives_fails_closed(bad_md: str) -> None:
    reg = {"zeta": _card("zeta", "Zeta")}
    with pytest.raises(SiteDirectiveError):
        expand_concept_directives(bad_md, reg, base_url="/")


def test_split_zoom_levels_and_page_glossary() -> None:
    md = (
        "# Title\n\nPreamble.\n\n"
        "```md\n## Level 1 — Fake\n```\n\n"
        "## Level 1 — One\n\nA.\n\n"
        "## Level 2 — Two\n\nB.\n\n"
        "## Level 3 — Three\n\nC.\n\n"
        "## Epilogue\n\nEnd.\n"
    )
    wrapped = split_zoom_levels(md, page_id="00_statement/overview")
    assert wrapped.startswith("# Title\n\nPreamble.\n\n```md\n## Level 1 — Fake\n```\n\n<ZoomTabs")
    assert '<ZoomTabs page-id="00_statement/overview">' in wrapped
    assert "<template #level1>\n\n## Level 1 — One\n\nA.\n\n</template>" in wrapped
    assert "<template #level2>\n\n## Level 2 — Two\n\nB.\n\n</template>" in wrapped
    assert "<template #level3>\n\n## Level 3 — Three\n\nC.\n\n</template>" in wrapped
    assert wrapped.endswith("</ZoomTabs>\n\n## Epilogue\n\nEnd.\n")

    partial = "# Only two\n\n## Level 1\n\nA\n\n## Level 2\n\nB\n"
    assert split_zoom_levels(partial, page_id="x") == partial

    reg = {"a": _card("a", "Alpha & Beta", glimpse="First <concept>.")}
    assert render_page_glossary([], reg, base_url="/b/") == ""
    glossary = render_page_glossary(["a", "a"], reg, base_url="/b/")
    assert '<section class="rh-page-glossary" aria-label="Page glossary">' in glossary
    assert "## Page Glossary" in glossary
    assert '<dt><a href="/b/concepts/a">Alpha &amp; Beta</a></dt>' in glossary
    assert "<dd>First &lt;concept&gt;.</dd>" in glossary


def test_compile_site_end_to_end_determinism_and_pruning(tmp_path: Path) -> None:
    repo, memory, out = _make_fixture(tmp_path)
    _write(out / ".vitepress" / "config.mts", "export default {}\n")
    _write(out / "stages" / "stale" / "old.md", "stale\n")
    _write(out / "concepts" / "stale.md", "stale\n")
    _write(out / "assets" / "stale.svg", "stale\n")
    _write(out / "public" / "assets" / "stale.svg", "stale\n")

    cfg = SiteBuildConfig(repo_root=repo, memory_root=memory, out_docs_dir=out)
    report1 = compile_site(cfg)
    assert (report1.stages_built, report1.concepts_built, report1.assets_copied) == (1, 2, 1)

    assert (out / ".vitepress" / "config.mts").read_text(encoding="utf-8") == "export default {}\n"
    assert not (out / "stages" / "stale" / "old.md").exists()
    assert not (out / "concepts" / "stale.md").exists()
    assert not (out / "assets" / "stale.svg").exists()
    assert not (out / "public" / "assets" / "stale.svg").exists()

    overview = (out / "stages" / "00_statement" / "overview.md").read_text(encoding="utf-8")
    assert '<VerificationBanner build-status="unverified-locally"' in overview
    assert 'repo-layout="legacy"' in overview
    assert ':solution-forbidden-count="0"' in overview
    assert ':challenge-sorry-count="2"' in overview
    assert ':theorem-count="2"' in overview
    assert '<ZoomTabs page-id="00_statement/overview">' in overview
    assert '<ConceptReveal slug="critical-line"' in overview
    assert '<ConceptHover slug="critical-line"' in overview
    assert 'visual-src="/ar-rh-proof-skeleton/assets/critical-strip.svg"' in overview
    assert '<section class="rh-page-glossary"' in overview
    assert "def RiemannZetaZerosOnLine : Prop :=" in overview
    assert (
        f"https://github.com/leanprover-community/riemann-hypothesis/blob/{COMMIT}/"
        "src/Statements.lean#L7-L8"
    ) in overview
    assert '<figure class="rh-stage-minimap">' in overview
    assert "<svg viewBox=" in overview
    assert "![Strip](/assets/critical-strip.svg)" in overview
    assert 'src="/assets/critical-strip.svg"' in overview

    zeta_page = (out / "concepts" / "riemann-zeta.md").read_text(encoding="utf-8")
    assert "# Riemann Zeta Function" in zeta_page
    assert "## Where this analogy breaks" in zeta_page
    assert "## Formal Lean Anchors" in zeta_page
    assert "theorem riemannHypothesis : RiemannZetaZerosOnLine := by" in zeta_page
    assert (
        f"https://github.com/leanprover-community/riemann-hypothesis/blob/{COMMIT}/"
        "src/Main.lean#L46-L47"
    ) in zeta_page
    assert "**Stage:** [Stage 00: Statement Overview](/stages/00_statement/overview)" in zeta_page
    assert "**Concept:** [Critical Line](/concepts/critical-line)" in zeta_page

    assert (out / "assets" / "critical-strip.svg").is_file()
    assert (out / "public" / "assets" / "critical-strip.svg").is_file()
    assert "# Concept Index" in (out / "concepts" / "index.md").read_text(encoding="utf-8")
    assert "[Stage 00 Overview](/stages/00_statement/overview)" in (out / "index.md").read_text(
        encoding="utf-8"
    )

    snap1 = {
        p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()
    }
    compile_site(cfg)
    snap2 = {
        p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()
    }
    assert snap1 == snap2


def test_compile_site_fail_closed_zero_writes(tmp_path: Path) -> None:
    cases = [
        "missing_ver",
        "missing_dag",
        "commit_mismatch",
        "unknown_slug",
        "missing_asset",
        "bad_node",
    ]
    for case in cases:
        case_dir = tmp_path / case
        repo, memory, out = _make_fixture(
            case_dir, commit=("f" * 40 if case == "commit_mismatch" else COMMIT)
        )
        if case == "missing_ver":
            (repo / "audit" / "verification-status.json").unlink()
        elif case == "missing_dag":
            (repo / "dag" / "dag.json").unlink()
        elif case == "unknown_slug":
            p = memory / "docs" / "stages" / "00_statement" / "overview.md"
            p.write_text(
                p.read_text(encoding="utf-8") + "\n{{term:nonexistent}}\n", encoding="utf-8"
            )
        elif case == "missing_asset":
            (memory / "docs" / "assets" / "critical-strip.svg").unlink()
        elif case == "bad_node":
            p = memory / "docs" / "stages" / "00_statement" / "overview.md"
            p.write_text(
                p.read_text(encoding="utf-8") + "\n<!-- rh:lean-node:00_statement/nope -->\n",
                encoding="utf-8",
            )
        with pytest.raises(SiteBuildError):
            compile_site(SiteBuildConfig(repo_root=repo, memory_root=memory, out_docs_dir=out))
        assert not out.exists()


def test_build_site_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, memory, out = _make_fixture(tmp_path)
    code = build_site_main(["--repo", str(repo), "--memory", str(memory), "--out", str(out)])
    assert code == 0
    assert "stages=1, concepts=2, assets=1" in capsys.readouterr().out

    bad_out = tmp_path / "bad_out"
    (repo / "dag" / "dag.json").unlink()
    err_code = build_site_main(
        ["--repo", str(repo), "--memory", str(memory), "--out", str(bad_out)]
    )
    assert err_code == 1
    assert "ERROR:" in capsys.readouterr().err
    assert not bad_out.exists()


def test_preserves_backticks_in_stage_titles_and_deduplicates_concept_h1(tmp_path: Path) -> None:
    repo, memory, out = _make_fixture(tmp_path)
    _write(
        memory / "docs" / "stages" / "00_statement" / "main-theorems.md",
        "# Entry Theorems (`src/Main.lean`)\n\n"
        "## Level 1 — Plain\n\n"
        "Uses {{concept:critical-line|vertical midpoint line}}.\n\n"
        "## Level 2 — Undergrad\n\nBridge.\n\n"
        "## Level 3 — Formal\n\n<!-- rh:lean-node:00_statement/main -->\n",
    )
    card_path = memory / "docs" / "concepts" / "critical-line.md"
    card_text = card_path.read_text(encoding="utf-8")
    card_path.write_text(
        card_text.replace("The critical line", "# Critical Line\n\nThe critical line", 1),
        encoding="utf-8",
    )

    compile_site(SiteBuildConfig(repo_root=repo, memory_root=memory, out_docs_dir=out))
    index_md = (out / "index.md").read_text(encoding="utf-8")
    assert "[Entry Theorems (`src/Main.lean`)](/stages/00_statement/main-theorems)" in index_md
    assert 'href="/ar-rh-proof-skeleton/stages/00_statement/main-theorems"' in index_md

    concept_md = (out / "concepts" / "critical-line.md").read_text(encoding="utf-8")
    assert concept_md.count("# Critical Line") == 1
    assert "**Stage:** [Entry Theorems (`src/Main.lean`)](/stages/00_statement/main-theorems)" in (
        concept_md
    )


def test_verification_banner_repo_layout(tmp_path: Path) -> None:
    scan = StaticScanSummary(
        scan_scope="entry",
        files_scanned=("src/Statements.lean", "src/Main.lean"),
        solution_findings=(),
        challenge_findings=(),
        challenge_sorry_count=2,
    )
    legacy_status = VerificationStatus(
        schema_version="1.0.0",
        upstream_commit=COMMIT,
        lean_toolchain="leanprover/lean4:v4.37.0-rc1",
        mathlib_rev="b" * 40,
        build_status="unverified-locally",
        scan_scope="entry",
        files_scanned=("src/Statements.lean", "src/Main.lean"),
        solution_forbidden_token_count=0,
        challenge_sorry_count=2,
        theorem_names=("riemannHypothesis",),
        permitted_axioms=("propext", "Quot.sound", "Classical.choice"),
        community_verification_notes_sha256="c" * 64,
        upstream_static_scan_sha256="d" * 64,
        scan=scan,
    )
    condensed_status = VerificationStatus(
        schema_version="1.0.0",
        upstream_commit=COMMIT,
        lean_toolchain="leanprover/lean4:v4.37.0-rc1",
        mathlib_rev="b" * 40,
        build_status="unverified-locally",
        scan_scope="entry",
        files_scanned=("src/Statements.lean", "src/Main.lean"),
        solution_forbidden_token_count=0,
        challenge_sorry_count=2,
        theorem_names=("riemannHypothesis",),
        permitted_axioms=("propext", "Quot.sound", "Classical.choice"),
        community_verification_notes_sha256=None,
        upstream_static_scan_sha256=None,
        scan=scan,
    )

    assert 'repo-layout="legacy"' in render_verification_banner(legacy_status)
    assert 'repo-layout="condensed"' in render_verification_banner(condensed_status)

    repo, memory, out = _make_fixture(tmp_path, condensed=True)
    compile_site(SiteBuildConfig(repo_root=repo, memory_root=memory, out_docs_dir=out))
    overview = (out / "stages" / "00_statement" / "overview.md").read_text(encoding="utf-8")
    assert 'repo-layout="condensed"' in overview
    assert 'repo-layout="condensed"' in (out / "index.md").read_text(encoding="utf-8")


def _add_roadmap_stages(repo: Path) -> None:
    skel = "01_reduction/reduce.lean"
    _write(repo / "skeleton" / skel, "theorem red : True := by sorry\n")
    dag_path = repo / "dag" / "dag.json"
    dag = json.loads(dag_path.read_text(encoding="utf-8"))
    s1_files = [{"upstream_path": "src/Red.lean", "skeleton_file": skel, "preserve_defs": False}]
    s1 = {"stage_id": "01_reduction", "order": 1, "title": "Reduction", "summary": "S1 text."}
    s2 = {"stage_id": "02_derivatives", "order": 2, "title": "Derivatives", "summary": "S2 text."}
    dag["stages"].extend([{**s1, "files": s1_files}, {**s2, "files": []}])
    node: dict[str, object] = {"id": "01_reduction/reduce", "stage_id": "01_reduction"}
    node |= {"title": "Reduce", "skeleton_file": skel, "upstream_path": "src/Red.lean"}
    node |= {"declarations": [], "depends_on": [], "is_landmark": True}
    dag["nodes"].append(node)
    dag_path.write_text(json.dumps(dag, indent=2) + "\n", encoding="utf-8")


def test_landing_index_overview_only_roadmap(tmp_path: Path) -> None:
    repo, memory, out = _make_fixture(tmp_path)
    _add_roadmap_stages(repo)
    compile_site(SiteBuildConfig(repo_root=repo, memory_root=memory, out_docs_dir=out))
    index_md = (out / "index.md").read_text(encoding="utf-8")
    assert "[Stage 00 Overview](/stages/00_statement/overview)" in index_md
    assert "/stages/00_statement/proof-journey" not in index_md
    assert "## 8-Pillar Proof Roadmap" in index_md
    for expected in (
        "### Stage 00 — Statement and Entry Point",
        "### Stage 01 — Reduction",
        "### Stage 02 — Derivatives",
        "- **Extracted landmarks:** 2",
        "- **Extracted landmarks:** 1",
        "- **Extracted landmarks:** 0",
        "- **Status:** Skeleton extracted — essay upcoming",
        "- **Status:** Upcoming",
    ):
        assert expected in index_md
    assert "/stages/01_reduction/" not in index_md and "/stages/02_derivatives/" not in index_md


def test_landing_index_with_proof_journey(tmp_path: Path) -> None:
    repo, memory, out = _make_fixture(tmp_path)
    _add_roadmap_stages(repo)
    journey_link = "[Full Proof Journey & Alpöge's 20-Step Map](/stages/00_statement/proof-journey)"
    s00 = memory / "docs" / "stages" / "00_statement"
    _write(s00 / "proof-journey.md", "# Full Proof Journey & Alpöge's 20-Step Map\n\nBody.\n")
    _write(s00 / "main-theorems.md", "# Main Theorems\n\nBody.\n")
    compile_site(SiteBuildConfig(repo_root=repo, memory_root=memory, out_docs_dir=out))
    index_md = (out / "index.md").read_text(encoding="utf-8")
    intro, rest = index_md.split("## 8-Pillar Proof Roadmap", 1)
    assert journey_link in intro
    roadmap, tail = rest.split("## Landmark Pages", 1)
    landmarks = tail.split("## Stage 00 Dependency Map", 1)[0]
    assert "/stages/00_statement/proof-journey" not in landmarks
    assert "/stages/00_statement/overview" not in landmarks
    assert "[Main Theorems](/stages/00_statement/main-theorems)" in landmarks
    assert journey_link in roadmap and "S1 text." in roadmap
    assert "Skeleton extracted — essay upcoming" in roadmap
    assert "/stages/01_reduction/" not in index_md
