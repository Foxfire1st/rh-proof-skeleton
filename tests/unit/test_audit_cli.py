"""Unit tests for Phase 0 status assembly, atomic writes, and the rh-audit CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pytest import CaptureFixture, MonkeyPatch

from rh_pipeline.audit.provenance import AuditInputError
from rh_pipeline.audit.status import build_verification_status, write_audit_artifacts
from rh_pipeline.cli.audit import main as audit_main
from rh_pipeline.models.audit import (
    AUDIT_SCHEMA_VERSION,
    ComparatorSummary,
    ProvenanceRecord,
    StaticScanFinding,
    StaticScanSummary,
    UpstreamRepoPin,
    VerificationStatus,
)

_CLEAN_CHALLENGE = "theorem challenge_thm : True := by\n  sorry\n"
_CLEAN_SOLUTION = "theorem solution_thm : True := trivial\n"
_MATHLIB_REV = "31ce06eaadf4a200c0e1407c5f10953cf606b8f8"


def _git_init_and_commit(repo_dir: Path) -> None:
    """Initialize a temporary Git repository with one commit."""
    cmds = (
        ["git", "init", "-b", "main"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", "commit", "-m", "init"],
    )
    for cmd in cmds:
        subprocess.run(cmd, cwd=repo_dir, check=True, capture_output=True)


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _create_synthetic_upstream(root: Path, overrides: dict[str, str] | None = None) -> Path:
    """Create a minimal valid synthetic upstream Git checkout for CLI tests."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"packages": [{"name": "mathlib", "rev": _MATHLIB_REV}]}
    root_comp = {
        "challenge_module": "Challenge",
        "solution_module": "Main",
        "theorem_names": ["thm_a", "thm_b"],
        "permitted_axioms": ["propext", "Classical.choice"],
        "external_kernels": {"lean4lean": ["lake", "exe", "lean4lean"]},
    }
    fc_comp: dict[str, object] = {
        "challenge_module": "FormalConjecturesChallenge",
        "solution_module": "FormalConjecturesSolution",
        "theorem_names": ["thm_b", "thm_c"],
        "permitted_axioms": ["Classical.choice", "Quot.sound"],
        "external_kernels": {},
    }
    files: dict[str, str] = {
        "lean-toolchain": "leanprover/lean4:v4.37.0-rc1\n",
        "lake-manifest.json": json.dumps(manifest) + "\n",
        "comparator.json": json.dumps(root_comp) + "\n",
        "comparator/formal-conjectures.json": json.dumps(fc_comp) + "\n",
        "verification/NOTES.md": "Community verification notes.\n",
        "verification/static-scan.txt": "Static scan log.\n",
        "src/Statements.lean": "def stmt : Prop := True\n",
        "src/Challenge.lean": _CLEAN_CHALLENGE,
        "src/Main.lean": _CLEAN_SOLUTION,
        "src/ExtraModule.lean": "theorem extra_ok : True := trivial\n",
        "comparator/FormalConjecturesStatement.lean": "def fcStmt : Prop := True\n",
        "comparator/FormalConjecturesChallenge.lean": _CLEAN_CHALLENGE,
        "comparator/FormalConjecturesBridge.lean": _CLEAN_SOLUTION,
        "comparator/FormalConjecturesSolution.lean": _CLEAN_SOLUTION,
    }
    if overrides:
        files.update(overrides)
    _write_files(root, files)
    _git_init_and_commit(root)
    return root


def _create_synthetic_replay(root: Path) -> Path:
    """Create a minimal valid synthetic replay metadata Git checkout."""
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "lean": "4.37.0-rc1",
        "lean_commit": "aa" * 20,
        "mathlib_commit": _MATHLIB_REV,
        "module_count": 12,
    }
    files = {
        "parts.sha256": "deadbeef  part01.tar.zst\n",
        "tracker/summary.json": json.dumps(summary) + "\n",
    }
    _write_files(root, files)
    _git_init_and_commit(root)
    return root


def _sample_provenance_and_scan() -> tuple[ProvenanceRecord, StaticScanSummary]:
    """Build deterministic in-memory ProvenanceRecord and StaticScanSummary fixtures."""
    comp_a = ComparatorSummary(
        "comparator.json",
        "Challenge",
        "Main",
        ("thm_a", "thm_b"),
        ("propext", "Classical.choice"),
        {"lean4lean": ("lake", "exe", "lean4lean")},
    )
    comp_b = ComparatorSummary(
        "comparator/formal-conjectures.json",
        "FormalConjecturesChallenge",
        "FormalConjecturesSolution",
        ("thm_b", "thm_c"),
        ("Classical.choice", "Quot.sound"),
        {},
    )
    upstream = UpstreamRepoPin(
        "synthetic-rh", "12" * 20, "main", "leanprover/lean4:v4.37.0-rc1", _MATHLIB_REV
    )
    notes = {"verification/NOTES.md": "aa" * 32, "verification/static-scan.txt": "bb" * 32}
    provenance = ProvenanceRecord(
        AUDIT_SCHEMA_VERSION,
        upstream,
        None,
        (comp_a, comp_b),
        {"comparator/formal-conjectures.json": "11" * 32},
        notes,
    )
    finding = StaticScanFinding("src/Challenge.lean", 2, "sorry", "challenge")
    scan = StaticScanSummary("entry", ("src/Challenge.lean", "src/Main.lean"), (), (finding,), 1)
    return provenance, scan


def _load_artifacts(out_dir: Path) -> tuple[ProvenanceRecord, VerificationStatus]:
    prov_raw: dict[str, Any] = json.loads((out_dir / "provenance.json").read_text("utf-8"))
    stat_raw: dict[str, Any] = json.loads((out_dir / "verification-status.json").read_text("utf-8"))
    return ProvenanceRecord.from_dict(prov_raw), VerificationStatus.from_dict(stat_raw)


def test_build_verification_status_and_strict_round_trip() -> None:
    provenance, scan = _sample_provenance_and_scan()
    status = build_verification_status(provenance, scan)

    assert status.build_status == "unverified-locally"
    assert status.theorem_names == ("thm_a", "thm_b", "thm_c")
    assert status.permitted_axioms == ("propext", "Classical.choice", "Quot.sound")
    assert status.solution_forbidden_token_count == 0
    assert status.challenge_sorry_count == 1
    assert status.community_verification_notes_sha256 == "aa" * 32
    assert status.upstream_static_scan_sha256 == "bb" * 32

    payload = status.to_dict()
    assert VerificationStatus.from_dict(payload) == status

    with pytest.raises(ValueError, match="build_status"):
        VerificationStatus.from_dict({**payload, "build_status": "verified"})

    missing_note = ProvenanceRecord(
        provenance.schema_version,
        provenance.upstream,
        None,
        provenance.comparators,
        provenance.comparator_file_digests,
        {"verification/NOTES.md": "aa" * 32},
    )
    with pytest.raises(AuditInputError, match=re.escape("verification/static-scan.txt")):
        build_verification_status(missing_note, scan)


def test_write_audit_artifacts_deterministic_and_atomic(tmp_path: Path) -> None:
    provenance, scan = _sample_provenance_and_scan()
    status = build_verification_status(provenance, scan)
    out_dir = tmp_path / "nested" / "audit"

    write_audit_artifacts(out_dir, provenance, status)
    prov_bytes = (out_dir / "provenance.json").read_bytes()
    status_bytes = (out_dir / "verification-status.json").read_bytes()

    for raw in (prov_bytes, status_bytes):
        assert raw.endswith(b"\n")
        text = raw.decode("utf-8")
        assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
        for forbidden_key in ("timestamp", "generated_at", "created_at"):
            assert forbidden_key not in text

    write_audit_artifacts(out_dir, provenance, status)
    assert (out_dir / "provenance.json").read_bytes() == prov_bytes
    assert (out_dir / "verification-status.json").read_bytes() == status_bytes


def test_write_audit_artifacts_cleans_up_temp_files_on_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    provenance, scan = _sample_provenance_and_scan()
    status = build_verification_status(provenance, scan)
    out_dir = tmp_path / "audit"
    out_dir.mkdir()
    (out_dir / "provenance.json").write_text("OLD_PROV\n", encoding="utf-8")
    (out_dir / "verification-status.json").write_text("OLD_STATUS\n", encoding="utf-8")

    fsync_calls = 0
    real_fsync = os.fsync

    def flaky_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("simulated disk error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky_fsync)
    with pytest.raises(OSError, match="simulated disk error"):
        write_audit_artifacts(out_dir, provenance, status)

    assert (out_dir / "provenance.json").read_text(encoding="utf-8") == "OLD_PROV\n"
    assert (out_dir / "verification-status.json").read_text(encoding="utf-8") == "OLD_STATUS\n"
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "provenance.json",
        "verification-status.json",
    ]


def test_cli_clean_repo_entry_full_and_replay(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    upstream = _create_synthetic_upstream(tmp_path / "upstream")
    replay = _create_synthetic_replay(tmp_path / "replay")
    out_entry = tmp_path / "out_entry"

    assert audit_main(["--upstream", str(upstream), "--out", str(out_entry)]) == 0
    assert "solution_forbidden=0" in capsys.readouterr().out
    prov, stat = _load_artifacts(out_entry)
    assert prov.replay_pin is None
    assert stat.scan_scope == "entry"
    assert stat.challenge_sorry_count == 2

    out_full = tmp_path / "out_full"
    full_args = ["--upstream", str(upstream), "--replay", str(replay), "--scope", "full"]
    assert audit_main([*full_args, "--out", str(out_full)]) == 0
    prov_full, stat_full = _load_artifacts(out_full)
    assert prov_full.replay_pin is not None
    assert prov_full.replay_pin.module_count == 12
    assert stat_full.scan_scope == "full"
    assert "src/ExtraModule.lean" in stat_full.files_scanned


@pytest.mark.parametrize(
    ("rel_path", "bad_source"),
    [
        ("src/Main.lean", "theorem oops : True := by\n  sorry\n"),
        ("src/Main.lean", '@[extern "c_sym"]\ndef sneaky : Nat := 0\n'),
        ("src/Challenge.lean", "axiom bogus : False\ntheorem t : True := by sorry\n"),
    ],
)
def test_cli_forbidden_tokens_exit_1_and_still_write_json(
    tmp_path: Path, rel_path: str, bad_source: str
) -> None:
    upstream = _create_synthetic_upstream(tmp_path / "upstream", overrides={rel_path: bad_source})
    out_dir = tmp_path / "out_bad"

    assert audit_main(["--upstream", str(upstream), "--out", str(out_dir)]) == 1
    _, status = _load_artifacts(out_dir)
    assert status.solution_forbidden_token_count > 0


def test_cli_input_and_scan_errors_exit_2_without_writing(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    missing_out = tmp_path / "out_missing"
    missing_args = ["--upstream", str(tmp_path / "missing"), "--out", str(missing_out)]
    assert audit_main(missing_args) == 2
    assert "ERROR:" in capsys.readouterr().err
    assert not missing_out.exists()

    broken_upstream = _create_synthetic_upstream(tmp_path / "broken_input")
    (broken_upstream / "verification" / "NOTES.md").unlink()
    out_input = tmp_path / "out_input_err"
    assert audit_main(["--upstream", str(broken_upstream), "--out", str(out_input)]) == 2
    assert "ERROR:" in capsys.readouterr().err
    assert not out_input.exists()

    scan_upstream = _create_synthetic_upstream(
        tmp_path / "broken_scan", overrides={"src/Main.lean": "/- unclosed block comment\n"}
    )
    out_scan = tmp_path / "out_scan_err"
    assert audit_main(["--upstream", str(scan_upstream), "--out", str(out_scan)]) == 2
    assert "ERROR:" in capsys.readouterr().err
    assert not out_scan.exists()
