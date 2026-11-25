"""Unit tests for the Phase 0 Lean lexical static scanner."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from rh_pipeline.audit.provenance import AuditInputError
from rh_pipeline.audit.static_scan import (
    ENTRY_SCAN_FILES,
    AuditScanError,
    scan_lean_source,
    scan_upstream_repo,
)
from rh_pipeline.models.audit import ScanScope, StaticScanFinding, StaticScanSummary

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "lean_audit"


def _write_repo_file(root: Path, rel_path: str, content: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_clean_entry_repo(root: Path) -> None:
    for rel_path in ENTRY_SCAN_FILES:
        if rel_path.endswith("Challenge.lean"):
            _write_repo_file(root, rel_path, "theorem target : True := by\n  sorry\n")
        else:
            _write_repo_file(root, rel_path, "theorem ok : True := trivial\n")


def test_comments_nested_blocks_and_line_numbers() -> None:
    source = (
        "-- sorry admit axiom\n"
        '/-! native_decide @[extern "c_fn"] -/\n'
        "/-- opaque trustCompiler -/\n"
        "/- outer /- sorry -/ still outer -/\n"
        "/-/ still inside comment with sorry -/\n"
        "theorem t : True := by\n"
        "  /- outer /- inner -/ -/ sorry\n"
    )
    findings = scan_lean_source(source, rel_path="src/Demo.lean", role="solution")
    assert findings == (
        StaticScanFinding(rel_path="src/Demo.lean", line=7, token="sorry", role="solution"),
    )


def test_strings_raw_strings_char_literals_and_identifier_primes() -> None:
    source = (
        'def s1 := "sorry \\"admit\\" \\\\" ++ "axiom"\n'
        'def s2 := "multi\nline\nsorry"\n'
        'def s3 := r#"unsafe @[implemented_by f] "sorry""#\n'
        'def s4 := r##"nested "#\n sorry"##\n'
        "def chars := ('\"', '\\'', '\\\\', 'a', '\\n', '\\x41', '\\u0041', '\\u{41}')\n"
        "def primes (hre' x' : Nat) : Nat := hre' + x'\n"
        "theorem after : True := by\n"
        "  sorry\n"
    )
    findings = scan_lean_source(source, rel_path="src/Literals.lean", role="solution")
    assert findings == (
        StaticScanFinding(rel_path="src/Literals.lean", line=11, token="sorry", role="solution"),
    )


def test_word_boundaries_and_qualified_names() -> None:
    source = (
        "def my_sorry := 1\n"
        "def sorryAx := 2\n"
        "def unsafe_fn := 3\n"
        "def sorry' := 4\n"
        "def χsorry := 5\n"
        "def extern := 6\n"
        "def implemented_by := 7\n"
        "def qualified := Lean.ofReduceBool\n"
    )
    findings = scan_lean_source(source, rel_path="src/Boundary.lean", role="solution")
    assert findings == (
        StaticScanFinding(
            rel_path="src/Boundary.lean",
            line=8,
            token="ofReduceBool",
            role="solution",
        ),
    )


def test_all_forbidden_tokens_and_attribute_forms() -> None:
    source = (
        "theorem a : True := by sorry\n"
        "theorem b : True := by admit\n"
        "axiom p : Prop\n"
        "opaque n : Nat\n"
        "unsafe def u : Nat := 0\n"
        "set_option debug.skipKernelTC true\n"
        "theorem c : 1 = 1 := by native_decide\n"
        "def r := ofReduceBool\n"
        "def t := trustCompiler\n"
        '@[extern "c_sym"] def f1 : Nat := 0\n'
        '@[inline, extern "c_sym"] def f2 : Nat := 0\n'
        '@[local extern "c_sym"] def f3 : Nat := 0\n'
        "@[implemented_by f1] def f4 : Nat := 0\n"
        'attribute [extern "c_sym"] f1\n'
        "local attribute [implemented_by f1] f2\n"
        'scoped attribute [extern "c_scoped"] f3\n'
        "@[\n"
        '  /- comment with extern "ignored" -/\n'
        "  inline,\n"
        '  extern "multiline"\n'
        "] def f5 : Nat := 0\n"
    )
    findings = scan_lean_source(source, rel_path="src/All.lean", role="solution")
    assert [(f.line, f.token) for f in findings] == [
        (1, "sorry"),
        (2, "admit"),
        (3, "axiom"),
        (4, "opaque"),
        (5, "unsafe"),
        (6, "skipKernelTC"),
        (7, "native_decide"),
        (8, "ofReduceBool"),
        (9, "trustCompiler"),
        (10, "extern"),
        (11, "extern"),
        (12, "extern"),
        (13, "implemented_by"),
        (14, "extern"),
        (15, "implemented_by"),
        (16, "extern"),
        (20, "extern"),
    ]


def test_unclosed_block_comment_raises_with_path_and_line() -> None:
    source = "def ok := 1\n/- outer\n/- inner -/\ndef hidden := 2\n"
    with pytest.raises(AuditScanError, match=r"src/Broken\.lean:2"):
        scan_lean_source(source, rel_path="src/Broken.lean", role="solution")

    nested_unclosed = "def ok := 1\n/- outer\n/- inner\ndef hidden := 2\n"
    with pytest.raises(AuditScanError, match=r"src/Nested\.lean:3"):
        scan_lean_source(nested_unclosed, rel_path="src/Nested.lean", role="solution")


def test_scan_upstream_repo_entry_vs_full_and_challenge_routing(tmp_path: Path) -> None:
    _make_clean_entry_repo(tmp_path)
    _write_repo_file(
        tmp_path,
        "src/Challenge.lean",
        "axiom badChallengeAxiom : Prop\ntheorem ch : True := by\n  sorry\n",
    )
    _write_repo_file(
        tmp_path,
        "src/Extra/Helper.lean",
        "theorem extraBad : True := by\n  native_decide\n",
    )
    _write_repo_file(
        tmp_path,
        "scripts/helper.lean",
        "theorem ignoredScript : True := by\n  sorry\n",
    )

    entry = scan_upstream_repo(tmp_path, scope="entry")
    assert entry.scan_scope == "entry"
    assert entry.files_scanned == tuple(sorted(ENTRY_SCAN_FILES))
    assert entry.challenge_sorry_count == 2
    assert [(f.rel_path, f.line, f.token, f.role) for f in entry.challenge_findings] == [
        ("comparator/FormalConjecturesChallenge.lean", 2, "sorry", "challenge"),
        ("src/Challenge.lean", 3, "sorry", "challenge"),
    ]
    assert [(f.rel_path, f.line, f.token, f.role) for f in entry.solution_findings] == [
        ("src/Challenge.lean", 1, "axiom", "challenge"),
    ]

    full = scan_upstream_repo(tmp_path, scope="full")
    assert full.scan_scope == "full"
    assert "src/Extra/Helper.lean" in full.files_scanned
    assert "scripts/helper.lean" not in full.files_scanned
    assert full.challenge_sorry_count == 2
    assert [(f.rel_path, f.line, f.token, f.role) for f in full.solution_findings] == [
        ("src/Challenge.lean", 1, "axiom", "challenge"),
        ("src/Extra/Helper.lean", 2, "native_decide", "solution"),
    ]


def test_scan_upstream_repo_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(AuditInputError, match="Upstream root directory"):
        scan_upstream_repo(tmp_path / "missing")

    with pytest.raises(AuditInputError, match="Invalid scan scope"):
        scan_upstream_repo(tmp_path, scope=cast(ScanScope, "invalid"))

    with pytest.raises(AuditInputError, match="Required entry Lean file missing"):
        scan_upstream_repo(tmp_path, scope="entry")

    with pytest.raises(AuditInputError, match="Required upstream directory missing"):
        scan_upstream_repo(tmp_path, scope="full")


def test_static_scan_models_round_trip() -> None:
    finding = StaticScanFinding(
        rel_path="src/Main.lean",
        line=12,
        token="extern",
        role="solution",
    )
    assert StaticScanFinding.from_dict(finding.to_dict()) == finding

    summary = StaticScanSummary(
        scan_scope="entry",
        files_scanned=tuple(sorted(ENTRY_SCAN_FILES)),
        solution_findings=(finding,),
        challenge_findings=(
            StaticScanFinding(
                rel_path="src/Challenge.lean",
                line=5,
                token="sorry",
                role="challenge",
            ),
        ),
        challenge_sorry_count=1,
    )
    assert StaticScanSummary.from_dict(summary.to_dict()) == summary


def test_synthetic_lean_audit_fixtures() -> None:
    assert _FIXTURE_DIR.is_dir()

    clean_src = (_FIXTURE_DIR / "clean_comments_and_literals.lean").read_text(encoding="utf-8")
    assert scan_lean_source(clean_src, rel_path="clean.lean", role="solution") == ()

    forbidden_src = (_FIXTURE_DIR / "forbidden_tokens.lean").read_text(encoding="utf-8")
    findings = scan_lean_source(forbidden_src, rel_path="forbidden.lean", role="solution")
    assert [(f.line, f.token) for f in findings] == [
        (4, "sorry"),
        (7, "admit"),
        (9, "axiom"),
        (11, "opaque"),
        (13, "unsafe"),
        (15, "skipKernelTC"),
        (18, "native_decide"),
        (20, "ofReduceBool"),
        (22, "trustCompiler"),
        (24, "extern"),
        (27, "extern"),
        (32, "implemented_by"),
        (36, "extern"),
        (40, "implemented_by"),
        (43, "sorry"),
    ]

    unclosed_src = (_FIXTURE_DIR / "unclosed_block_comment.lean").read_text(encoding="utf-8")
    with pytest.raises(AuditScanError, match=r"unclosed\.lean:5"):
        scan_lean_source(unclosed_src, rel_path="unclosed.lean", role="solution")
