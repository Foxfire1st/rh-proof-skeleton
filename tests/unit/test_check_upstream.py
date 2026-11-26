"""Unit tests for read-only upstream freshness triage and the rh-check-upstream CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from pytest import CaptureFixture

from rh_pipeline.cli.check_upstream import main as check_upstream_main
from rh_pipeline.models.audit import (
    AUDIT_SCHEMA_VERSION,
    ComparatorSummary,
    ProvenanceRecord,
    ReplayPin,
    UpstreamRepoPin,
)
from rh_pipeline.models.dag import STAGE_MAP_SCHEMA_VERSION

_MATHLIB_REV = "31ce06eaadf4a200c0e1407c5f10953cf606b8f8"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=t@example.com", "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write_and_commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel_path, content in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return _commit(repo, message)


def _init_repo(repo: Path, files: dict[str, str]) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    return _write_and_commit(repo, files, "initial commit")


def _write_provenance(path: Path, upstream_commit: str, replay_commit: str | None = None) -> Path:
    comp = ComparatorSummary(
        "comparator.json", "Challenge", "Main", ("riemannHypothesis",), ("propext",), {}
    )
    replay_pin = (
        None
        if replay_commit is None
        else ReplayPin(replay_commit, "11" * 32, "4.33.1", "aa" * 20, _MATHLIB_REV, 12)
    )
    record = ProvenanceRecord(
        schema_version=AUDIT_SCHEMA_VERSION,
        upstream=UpstreamRepoPin(
            "riemann-hypothesis",
            upstream_commit,
            "main",
            "leanprover/lean4:v4.37.0-rc1",
            _MATHLIB_REV,
        ),
        replay_pin=replay_pin,
        comparators=(comp,),
        comparator_file_digests={"comparator/formal-conjectures.json": "22" * 32},
        note_digests={"verification/NOTES.md": "33" * 32},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.to_json(), encoding="utf-8")
    return path


def _write_stage_map(path: Path) -> Path:
    def _stage(stage_id: str, order: int, up: str, skel: str, preserve: bool) -> dict[str, Any]:
        return {
            "stage_id": stage_id,
            "order": order,
            "title": stage_id,
            "summary": f"Summary {stage_id}",
            "files": [{"upstream_path": up, "skeleton_file": skel, "preserve_defs": preserve}],
        }

    payload = {
        "schema_version": STAGE_MAP_SCHEMA_VERSION,
        "stages": [
            _stage("00_statement", 0, "src/Statements.lean", "00_statement/Statements.lean", True),
            _stage("01_core", 1, "src/Core/Lemma.lean", "01_core/Lemma.lean", False),
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _base_files() -> dict[str, str]:
    return {
        "README.md": "# RH\n",
        "GUIDE.md": "Guide v1\n",
        "Challenge.lean": "theorem c : True := trivial\n",
        "comparator/Bridge.lean": "def b := 1\n",
        "src/Statements.lean": "def stmt := True\n",
        "src/Core/Lemma.lean": "theorem core : True := trivial\n",
        "src/Unmapped.lean": "def helper := 0\n",
    }


def _run_json(argv: list[str], capsys: CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = check_upstream_main([*argv, "--json"])
    raw = capsys.readouterr().out
    assert raw.endswith("\n")
    parsed: dict[str, Any] = json.loads(raw)
    assert raw == json.dumps(parsed, indent=2, sort_keys=True) + "\n"
    assert all(key not in raw for key in ("timestamp", "generated_at", "created_at"))
    return code, parsed


def test_up_to_date_and_human_output_read_only(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    upstream = tmp_path / "upstream"
    pinned = _init_repo(upstream, _base_files())
    prov = _write_provenance(tmp_path / "audit" / "provenance.json", pinned)
    smap = _write_stage_map(tmp_path / "dag" / "stage-map.json")
    args = ["--upstream", str(upstream), "--provenance", str(prov), "--stage-map", str(smap)]

    assert check_upstream_main(args) == 0
    out = capsys.readouterr().out
    assert f"Status: up-to-date (exit 0)\nRef: HEAD\nPinned commit: {pinned}\n" in out
    assert "Replay status: not-checked\n" in out

    jcode, data = _run_json(args, capsys)
    assert (jcode, data) == (
        0,
        {
            "affected_stages": [],
            "changed_audit_paths": [],
            "changed_file_count": 0,
            "changed_mapped_paths": [],
            "commit_delta": 0,
            "exit_code": 0,
            "pinned_commit": pinned,
            "ref": "HEAD",
            "replay_pinned_commit": None,
            "replay_ref": None,
            "replay_status": "not-checked",
            "replay_target_commit": None,
            "status": "up-to-date",
            "target_commit": pinned,
        },
    )
    assert (
        _git(upstream, "rev-parse", "HEAD"),
        _git(upstream, "rev-parse", "--abbrev-ref", "HEAD"),
        _git(upstream, "status", "--porcelain"),
    ) == (pinned, "main", "")


def test_unmapped_only_and_ref_preserves_head(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    upstream = tmp_path / "upstream"
    pinned = _init_repo(upstream, _base_files())
    _git(upstream, "checkout", "-b", "other-branch")
    target = _write_and_commit(upstream, {"src/Unmapped.lean": "def helper := 1\n"}, "unmapped")
    _git(upstream, "checkout", "main")
    prov = _write_provenance(tmp_path / "audit" / "provenance.json", pinned)
    smap = _write_stage_map(tmp_path / "dag" / "stage-map.json")

    code, data = _run_json(
        [
            "--upstream",
            str(upstream),
            "--ref",
            "other-branch",
            "--provenance",
            str(prov),
            "--stage-map",
            str(smap),
        ],
        capsys,
    )
    assert (code, data["status"], data["ref"], data["target_commit"]) == (
        0,
        "unmapped-only",
        "other-branch",
        target,
    )
    assert (data["commit_delta"], data["changed_file_count"], data["affected_stages"]) == (1, 1, [])
    assert (data["changed_audit_paths"], data["changed_mapped_paths"]) == ([], [])
    assert (
        _git(upstream, "rev-parse", "HEAD"),
        _git(upstream, "rev-parse", "--abbrev-ref", "HEAD"),
    ) == (
        pinned,
        "main",
    )


def test_mapped_drift_audit_stage_and_rename(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    upstream = tmp_path / "upstream"
    pinned = _init_repo(upstream, _base_files())
    _write_and_commit(
        upstream,
        {
            "GUIDE.md": "Guide v2\n",
            "Challenge.lean": "theorem c : True := by trivial\n",
            "comparator/foo.lean": "def foo := 2\n",
            "verification/NOTES.md": "Updated notes\n",
        },
        "audit files changed",
    )
    _git(upstream, "mv", "src/Core/Lemma.lean", "src/MovedLemma.lean")
    target = _write_and_commit(
        upstream,
        {"src/Statements.lean": "def stmt := True ∧ True\n", "src/Unmapped.lean": "def h := 2\n"},
        "mapped rename and edit",
    )
    prov = _write_provenance(tmp_path / "audit" / "provenance.json", pinned)
    smap = _write_stage_map(tmp_path / "dag" / "stage-map.json")

    code, data = _run_json(
        ["--upstream", str(upstream), "--provenance", str(prov), "--stage-map", str(smap)], capsys
    )
    assert (code, data["status"], data["target_commit"], data["commit_delta"]) == (
        10,
        "mapped-drift",
        target,
        2,
    )
    assert data["changed_audit_paths"] == [
        "Challenge.lean",
        "GUIDE.md",
        "comparator/foo.lean",
        "src/Statements.lean",
        "verification/NOTES.md",
    ]
    assert data["changed_mapped_paths"] == ["src/Core/Lemma.lean", "src/Statements.lean"]
    assert data["affected_stages"] == ["00_statement", "01_core"]


def test_history_switched_diverged_and_missing_commit(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    upstream = tmp_path / "upstream"
    base = _init_repo(upstream, _base_files())
    pinned = _write_and_commit(upstream, {"src/Unmapped.lean": "def a := 1\n"}, "old line")
    _git(upstream, "checkout", "-B", "rewritten", base)
    rewritten = _write_and_commit(upstream, {"src/Unmapped.lean": "def a := 2\n"}, "new line")
    prov = _write_provenance(tmp_path / "audit" / "provenance.json", pinned)
    smap = _write_stage_map(tmp_path / "dag" / "stage-map.json")

    code, data = _run_json(
        [
            "--upstream",
            str(upstream),
            "--ref",
            "rewritten",
            "--provenance",
            str(prov),
            "--stage-map",
            str(smap),
        ],
        capsys,
    )
    assert (code, data["status"], data["target_commit"], data["commit_delta"]) == (
        11,
        "history-switched",
        rewritten,
        None,
    )

    prov_missing = _write_provenance(tmp_path / "audit" / "missing.json", "f" * 40)
    code_m, data_m = _run_json(
        ["--upstream", str(upstream), "--provenance", str(prov_missing), "--stage-map", str(smap)],
        capsys,
    )
    assert (code_m, data_m["status"], data_m["commit_delta"]) == (11, "history-switched", None)


def test_replay_statuses_are_informational(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    upstream = tmp_path / "upstream"
    pinned_up = _init_repo(upstream, _base_files())
    replay = tmp_path / "replay"
    replay_base = _init_repo(replay, {"parts.sha256": "v1\n"})
    replay_pinned = _write_and_commit(replay, {"parts.sha256": "v2\n"}, "replay pin")
    replay_moved = _write_and_commit(replay, {"parts.sha256": "v3\n"}, "replay moved")
    _git(replay, "checkout", "-B", "diverged", replay_base)
    replay_div = _write_and_commit(replay, {"parts.sha256": "v4\n"}, "replay diverged")
    _git(replay, "checkout", "main")

    smap = _write_stage_map(tmp_path / "dag" / "stage-map.json")
    prov_no = _write_provenance(tmp_path / "audit" / "no_replay.json", pinned_up, None)
    prov_yes = _write_provenance(tmp_path / "audit" / "with_replay.json", pinned_up, replay_pinned)
    prov_miss = _write_provenance(tmp_path / "audit" / "miss_replay.json", pinned_up, "e" * 40)
    base = ["--upstream", str(upstream), "--stage-map", str(smap)]

    c0, d0 = _run_json([*base, "--provenance", str(prov_no), "--replay", str(replay)], capsys)
    assert (c0, d0["replay_status"]) == (0, "not-pinned")

    for prov_p, ref, expected, expected_target in (
        (prov_yes, replay_pinned, "up-to-date", replay_pinned),
        (prov_yes, "main", "moved", replay_moved),
        (prov_yes, "diverged", "history-switched", replay_div),
        (prov_miss, "main", "history-switched", replay_moved),
    ):
        argv = [*base, "--provenance", str(prov_p), "--replay", str(replay), "--replay-ref", ref]
        c, d = _run_json(argv, capsys)
        assert (c, d["status"], d["replay_status"], d["replay_target_commit"]) == (
            0,
            "up-to-date",
            expected,
            expected_target,
        )


def test_input_and_schema_errors_exit_2(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    upstream = tmp_path / "upstream"
    pinned = _init_repo(upstream, _base_files())
    replay = tmp_path / "replay"
    _init_repo(replay, {"parts.sha256": "v1\n"})
    prov = _write_provenance(tmp_path / "audit" / "provenance.json", pinned)
    smap = _write_stage_map(tmp_path / "dag" / "stage-map.json")
    non_git = tmp_path / "non_git"
    non_git.mkdir()
    bad_prov = tmp_path / "bad_prov.json"
    bad_prov.write_text('{"schema_version": "1.0.0"}\n', encoding="utf-8")
    bad_smap = tmp_path / "bad_smap.json"
    bad_smap.write_text("{not-json", encoding="utf-8")
    base = ["--provenance", str(prov), "--stage-map", str(smap)]

    cases = (
        ["--upstream", str(tmp_path / "missing"), *base],
        ["--upstream", str(non_git), *base],
        ["--upstream", str(upstream / "src"), *base],
        ["--upstream", str(upstream), "--ref", "no-such-ref", *base],
        [
            "--upstream",
            str(upstream),
            "--provenance",
            str(tmp_path / "x.json"),
            "--stage-map",
            str(smap),
        ],
        ["--upstream", str(upstream), "--provenance", str(bad_prov), "--stage-map", str(smap)],
        [
            "--upstream",
            str(upstream),
            "--provenance",
            str(prov),
            "--stage-map",
            str(tmp_path / "y.json"),
        ],
        ["--upstream", str(upstream), "--provenance", str(prov), "--stage-map", str(bad_smap)],
        ["--upstream", str(upstream), *base, "--replay", str(non_git)],
        ["--upstream", str(upstream), *base, "--replay", str(replay), "--replay-ref", "bad-ref"],
    )
    for argv in cases:
        assert check_upstream_main(argv) == 2
        assert "ERROR:" in capsys.readouterr().err
