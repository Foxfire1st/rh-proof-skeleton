"""End-to-end unit tests for extract_and_write, atomic cleanup, and rh-extract CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pytest import CaptureFixture, MonkeyPatch

from rh_pipeline.cli.extract import main as extract_main
from rh_pipeline.graph.stage_map import StageMapError, extract_and_write
from rh_pipeline.lean.parser import UnsupportedLeanFormError
from rh_pipeline.models.dag import STAGE_MAP_SCHEMA_VERSION, DagGraph


def _git_init_and_commit(repo_dir: Path) -> str:
    cmds = (
        ["git", "init", "-b", "main"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", "commit", "-m", "init"],
    )
    for cmd in cmds:
        subprocess.run(cmd, cwd=repo_dir, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--verify", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return head.stdout.strip()


def _create_synthetic_upstream(
    root: Path, overrides: dict[str, str] | None = None
) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        "src/Statements.lean": (
            "import Mathlib.NumberTheory.LSeries.RiemannZeta\n\n"
            "def RiemannHypothesis : Prop :=\n  True\n"
        ),
        "src/Challenge.lean": (
            "import Mathlib.NumberTheory.LSeries.RiemannZeta\n\n"
            "def RiemannHypothesis : Prop :=\n  True\n\n"
            "theorem challenge_rh : RiemannHypothesis := by\n  sorry\n"
        ),
        "src/Main.lean": (
            "import Mathlib.NumberTheory.LSeries.RiemannZeta\n"
            "import Statements\n"
            "import Development.InternalHelper\n\n"
            "theorem main_rh : RiemannHypothesis := by\n  trivial\n"
        ),
        "comparator/FormalConjecturesStatement.lean": (
            "import Mathlib.NumberTheory.LSeries.RiemannZeta\n\ndef FcRH : Prop :=\n  True\n"
        ),
        "comparator/FormalConjecturesChallenge.lean": (
            "import FormalConjecturesStatement\n\ntheorem fc_rh : FcRH := by\n  sorry\n"
        ),
    }
    if overrides:
        files.update(overrides)
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root, _git_init_and_commit(root)


def _write_stage_map(path: Path) -> Path:
    files = [
        ("src/Statements.lean", "00_statement/statements.lean", True),
        ("src/Challenge.lean", "00_statement/challenge.lean", True),
        ("src/Main.lean", "00_statement/main.lean", False),
        (
            "comparator/FormalConjecturesStatement.lean",
            "00_statement/formal_conjectures_statement.lean",
            True,
        ),
        (
            "comparator/FormalConjecturesChallenge.lean",
            "00_statement/formal_conjectures_challenge.lean",
            False,
        ),
    ]
    payload: dict[str, Any] = {
        "schema_version": STAGE_MAP_SCHEMA_VERSION,
        "stages": [
            {
                "stage_id": "00_statement",
                "order": 0,
                "title": "Formal Statements",
                "summary": "Statement and challenge contracts.",
                "files": [
                    {"upstream_path": up, "skeleton_file": sk, "preserve_defs": keep}
                    for up, sk, keep in files
                ],
            },
            {
                "stage_id": "01_stepanov",
                "order": 1,
                "title": "Stepanov Determinant",
                "summary": "Placeholder next stage.",
                "files": [],
            },
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _cli_args(upstream: Path, stage_map: Path, out_skeleton: Path, out_dag: Path) -> list[str]:
    return [
        "--upstream",
        str(upstream),
        "--stage-map",
        str(stage_map),
        "--out-skeleton",
        str(out_skeleton),
        "--out-dag",
        str(out_dag),
    ]


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_cli_end_to_end_and_byte_identical_determinism(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    upstream, commit = _create_synthetic_upstream(tmp_path / "upstream")
    stage_map = _write_stage_map(tmp_path / "dag" / "stage-map.json")
    out_skeleton = tmp_path / "skeleton"
    out_skeleton.mkdir()
    readme_text = "# Skeleton README sentinel\n"
    (out_skeleton / "README.md").write_text(readme_text, encoding="utf-8")
    out_dag = tmp_path / "dag" / "dag.json"

    argv = _cli_args(upstream, stage_map, out_skeleton, out_dag)
    assert extract_main(argv) == 0
    stdout = capsys.readouterr().out
    assert f"Extracted 5 node(s) across 2 stage(s) at {commit[:12]}" in stdout
    assert (out_skeleton / "README.md").read_text(encoding="utf-8") == readme_text

    dag_raw = out_dag.read_text(encoding="utf-8")
    assert dag_raw.endswith("\n")
    assert dag_raw == json.dumps(json.loads(dag_raw), indent=2, sort_keys=True) + "\n"
    for forbidden in ("timestamp", "generated_at", "created_at"):
        assert forbidden not in dag_raw

    dag = DagGraph.from_dict(json.loads(dag_raw))
    assert dag.upstream_commit == commit
    nodes = {node.id: node for node in dag.nodes}
    assert nodes["00_statement/challenge"].depends_on == ()
    assert nodes["00_statement/main"].depends_on == ("00_statement/statements",)
    assert nodes["00_statement/formal_conjectures_challenge"].depends_on == (
        "00_statement/formal_conjectures_statement",
    )

    first_skeleton = _snapshot_tree(out_skeleton)
    first_dag = out_dag.read_bytes()
    assert extract_main(argv) == 0
    assert _snapshot_tree(out_skeleton) == first_skeleton
    assert out_dag.read_bytes() == first_dag


def test_atomic_write_cleans_up_temp_files_on_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    upstream, _ = _create_synthetic_upstream(tmp_path / "upstream")
    stage_map = _write_stage_map(tmp_path / "dag" / "stage-map.json")
    out_skeleton = tmp_path / "skeleton"
    existing_stub = out_skeleton / "00_statement" / "statements.lean"
    existing_stub.parent.mkdir(parents=True)
    existing_stub.write_text("-- SENTINEL STUB\n", encoding="utf-8")
    out_dag = tmp_path / "dag" / "dag.json"
    out_dag.write_text("SENTINEL DAG\n", encoding="utf-8")

    fsync_calls = 0
    real_fsync = os.fsync

    def flaky_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 3:
            raise OSError("simulated disk error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky_fsync)
    with pytest.raises(OSError, match="simulated disk error"):
        extract_and_write(
            upstream_root=upstream,
            stage_map_path=stage_map,
            out_skeleton_dir=out_skeleton,
            out_dag_path=out_dag,
        )

    assert existing_stub.read_text(encoding="utf-8") == "-- SENTINEL STUB\n"
    assert out_dag.read_text(encoding="utf-8") == "SENTINEL DAG\n"
    assert list(out_skeleton.rglob("*.tmp")) == []
    assert list(out_dag.parent.glob("*.tmp")) == []


def test_zero_writes_on_stage_map_errors(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    stage_map = _write_stage_map(tmp_path / "dag" / "stage-map.json")
    out_skeleton = tmp_path / "out_skeleton"
    out_dag = tmp_path / "out_dag" / "dag.json"

    assert extract_main(_cli_args(tmp_path / "missing", stage_map, out_skeleton, out_dag)) == 2
    assert "ERROR:" in capsys.readouterr().err
    assert not out_skeleton.exists() and not out_dag.parent.exists()

    upstream, _ = _create_synthetic_upstream(tmp_path / "upstream")
    assert extract_main(_cli_args(upstream / "src", stage_map, out_skeleton, out_dag)) == 2
    assert "ERROR:" in capsys.readouterr().err
    assert not out_skeleton.exists() and not out_dag.parent.exists()

    (upstream / "src" / "Main.lean").unlink()
    assert extract_main(_cli_args(upstream, stage_map, out_skeleton, out_dag)) == 2
    assert "ERROR:" in capsys.readouterr().err
    assert not out_skeleton.exists() and not out_dag.parent.exists()

    outside = tmp_path / "outside.lean"
    outside.write_text("def escaped : Prop := True\n", encoding="utf-8")
    (upstream / "src" / "Main.lean").symlink_to(outside)
    with pytest.raises(StageMapError, match="escapes repository root"):
        extract_and_write(
            upstream_root=upstream,
            stage_map_path=stage_map,
            out_skeleton_dir=out_skeleton,
            out_dag_path=out_dag,
        )
    assert not out_skeleton.exists() and not out_dag.parent.exists()


def test_zero_writes_on_unsupported_lean_form_error(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    upstream, _ = _create_synthetic_upstream(tmp_path / "upstream")
    stage_map = _write_stage_map(tmp_path / "dag" / "stage-map.json")
    out_skeleton = tmp_path / "out_skeleton"
    out_dag = tmp_path / "out_dag" / "dag.json"

    import rh_pipeline.graph.stage_map as stage_map_mod

    real_extract = stage_map_mod.extract_declarations

    def fail_on_second(source: str, *, rel_path: str, commit: str) -> object:
        if rel_path == "src/Challenge.lean":
            raise UnsupportedLeanFormError(rel_path, 3, "mutual")
        return real_extract(source, rel_path=rel_path, commit=commit)

    monkeypatch.setattr(stage_map_mod, "extract_declarations", fail_on_second)
    assert extract_main(_cli_args(upstream, stage_map, out_skeleton, out_dag)) == 2
    assert "ERROR: src/Challenge.lean:3:mutual" in capsys.readouterr().err
    assert not out_skeleton.exists() and not out_dag.parent.exists()
