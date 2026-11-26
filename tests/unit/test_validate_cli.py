"""Unit and end-to-end tests for the rh-validate CLI entry point."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from rh_pipeline.cli.validate import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def _copy_minimal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "audit", repo / "audit")
    shutil.copytree(REPO_ROOT / "dag", repo / "dag")
    shutil.copytree(REPO_ROOT / "skeleton", repo / "skeleton")
    return repo


def _write_valid_concept(docs_root: Path) -> None:
    concepts_dir = docs_root / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (docs_root / "assets").mkdir(parents=True, exist_ok=True)
    (docs_root / "assets" / "strip.svg").write_text("<svg/>", encoding="utf-8")
    (concepts_dir / "critical-strip.md").write_text(
        dedent(
            """\
            ---
            slug: critical-strip
            term: critical strip
            aliases:
              - strip of zeros
            inline_glimpse: the vertical band where real parts lie between 0 and 1
            visual_ref: assets/strip.svg
            where_analogy_breaks: Complex outputs twist in 2D rather than moving on one rail.
            lean_anchors:
              - 00_statement/statements.lean#RiemannZetaZerosOnLine
            ---
            The critical strip frames the region where non-trivial zeros live.
            """
        ),
        encoding="utf-8",
    )


def test_cli_without_memory_validates_repo_and_prints_skip_info(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--repo", str(REPO_ROOT)])
    out = capsys.readouterr().out
    assert code == 0
    assert "INFO: memory checks skipped (no --memory path)" in out
    assert "OK: rh-validate passed (0 findings)." in out


def test_cli_missing_repo_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "does-not-exist"
    code = main(["--repo", str(missing)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"cli-missing-repo:{missing}:0:" in out


def test_cli_missing_memory_path_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _copy_minimal_repo(tmp_path)
    missing_mem = tmp_path / "missing-memory"
    code = main(["--repo", str(repo), "--memory", str(missing_mem)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"cli-missing-memory:{missing_mem}:0:" in out


def test_cli_memory_without_concepts_dir_passes_with_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _copy_minimal_repo(tmp_path)
    mem = tmp_path / "memory"
    mem.mkdir()
    code = main(["--repo", str(repo), "--memory", str(mem)])
    out = capsys.readouterr().out
    assert code == 0
    assert "INFO: no concept cards found" in out
    assert "OK: rh-validate passed (0 findings)." in out


def test_cli_memory_with_empty_concepts_dir_passes_with_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _copy_minimal_repo(tmp_path)
    mem = tmp_path / "memory"
    (mem / "docs" / "concepts").mkdir(parents=True)
    code = main(["--repo", str(repo), "--memory", str(mem)])
    out = capsys.readouterr().out
    assert code == 0
    assert "INFO: no concept cards found" in out


def test_cli_populated_memory_passes_and_ignores_onboarding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _copy_minimal_repo(tmp_path)
    mem = tmp_path / "memory"
    _write_valid_concept(mem / "docs")
    stage_dir = mem / "docs" / "stages" / "00_statement"
    stage_dir.mkdir(parents=True)
    (stage_dir / "index.md").write_text(
        dedent(
            """\
            # Stage 00

            ## Level 1 — First-Principles
            We study {{concept:critical-strip|the band where $|z| < 1$}}.
            Later, {{term:critical-strip|critical strips}} hold all non-trivial zeros.

            ## Level 2 — Undergrad Bridge
            The critical strip appears directly here.
            """
        ),
        encoding="utf-8",
    )
    onboarding_dir = mem / "onboarding"
    onboarding_dir.mkdir(parents=True)
    (onboarding_dir / "code-card.md").write_text(
        "## Level 1 — First-Principles\nUnrevealed critical strip and {{concept:missing|bad}}.\n",
        encoding="utf-8",
    )

    code = main(["--repo", str(repo), "--memory", str(mem)])
    out = capsys.readouterr().out
    assert code == 0
    assert "OK: rh-validate passed (0 findings)." in out


def test_cli_reports_sorted_canonical_findings_across_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _copy_minimal_repo(tmp_path)
    dag_path = repo / "dag" / "dag.json"
    dag_data = json.loads(dag_path.read_text(encoding="utf-8"))
    dag_data["upstream_commit"] = "0" * 40
    dag_path.write_text(json.dumps(dag_data, indent=2) + "\n", encoding="utf-8")

    mem = tmp_path / "memory"
    _write_valid_concept(mem / "docs")
    stage_dir = mem / "docs" / "stages" / "00_statement"
    stage_dir.mkdir(parents=True)
    (stage_dir / "index.md").write_text(
        "## Level 1 — First-Principles\nThe critical strip comes too early.\n",
        encoding="utf-8",
    )

    code = main(["--repo", str(repo), "--memory", str(mem)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert any(line.startswith("schema-commit-mismatch:dag/dag.json:0:") for line in lines)
    assert any(
        line.startswith("jargon-before-reveal:stages/00_statement/index.md:2:") for line in lines
    )
    keys = [
        (parts[1], int(parts[2]), parts[0], parts[3])
        for parts in (line.split(":", 3) for line in lines)
    ]
    assert keys == sorted(keys)


def test_cli_non_utf8_files_emit_findings_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _copy_minimal_repo(tmp_path)
    mem = tmp_path / "memory"
    (mem / "docs" / "concepts").mkdir(parents=True)
    (mem / "docs" / "concepts" / "broken.md").write_bytes(b"\xff\xfe")
    stage_dir = mem / "docs" / "stages" / "00_statement"
    stage_dir.mkdir(parents=True)
    (stage_dir / "bad.md").write_bytes(b"\xff\xfe")

    code = main(["--repo", str(repo), "--memory", str(mem)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert any(line.startswith("concept-read:concepts/broken.md:0:") for line in lines)
    assert any(line.startswith("explainer-read:stages/00_statement/bad.md:0:") for line in lines)


def test_cli_console_script_runs_on_repo() -> None:
    exe = REPO_ROOT / ".venv" / "bin" / "rh-validate"
    completed = subprocess.run(
        [str(exe), "--repo", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "INFO: memory checks skipped (no --memory path)" in completed.stdout
    assert "OK: rh-validate passed (0 findings)." in completed.stdout
