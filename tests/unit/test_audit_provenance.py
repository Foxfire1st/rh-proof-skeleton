"""Unit tests for Phase 0 provenance collection and strict audit model schemas."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rh_pipeline.audit.provenance import AuditInputError, collect_provenance
from rh_pipeline.models.audit import (
    AUDIT_SCHEMA_VERSION,
    ComparatorSummary,
    ProvenanceRecord,
    ReplayPin,
    UpstreamRepoPin,
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_git_repo(repo: Path, *, branch: str = "main") -> str:
    _git(repo, "init", "-b", branch)
    _git(repo, "config", "user.name", "Test Author")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "initial")
    return _git(repo, "rev-parse", "--verify", "HEAD")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_upstream_repo(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.20.0\n", encoding="utf-8")
    _write_json(
        root / "lake-manifest.json",
        {
            "version": "1.1.0",
            "packages": [
                {"name": "batteries", "rev": "11111111"},
                {"name": "mathlib", "rev": "abcdef1234567890"},
            ],
        },
    )
    _write_json(
        root / "comparator.json",
        {
            "challenge_module": "Challenge",
            "solution_module": "Solution",
            "theorem_names": ["riemann_hypothesis"],
            "permitted_axioms": ["propext", "Classical.choice", "Quot.sound"],
            "external_kernels": {"lean4lean": ["lean4lean", "--check"]},
        },
    )
    _write_json(
        root / "comparator" / "formal-conjectures.json",
        {
            "challenge_module": "FormalConjectures.Challenge",
            "solution_module": "FormalConjectures.Solution",
            "theorem_names": ["formal_rh"],
            "permitted_axioms": ["propext"],
            "external_kernels": {},
        },
    )
    (root / "comparator" / "nested" / "aux.txt").parent.mkdir(parents=True, exist_ok=True)
    (root / "comparator" / "nested" / "aux.txt").write_text("aux data\n", encoding="utf-8")
    (root / "verification").mkdir(parents=True, exist_ok=True)
    (root / "verification" / "NOTES.md").write_text("# Notes\nVerified.\n", encoding="utf-8")
    (root / "verification" / "static-scan.txt").write_text("0 forbidden tokens\n", encoding="utf-8")
    head = _init_git_repo(root)
    return root, head


def _make_replay_repo(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "parts.sha256").write_text("deadbeef  lean.tar.gz.part01\n", encoding="utf-8")
    _write_json(
        root / "tracker" / "summary.json",
        {
            "lean": "v4.20.0",
            "lean_commit": "aaaa1111",
            "mathlib_commit": "bbbb2222",
            "module_count": 175187,
        },
    )
    head = _init_git_repo(root)
    return root, head


def test_collect_provenance_without_and_with_replay(tmp_path: Path) -> None:
    upstream_root, upstream_head = _make_upstream_repo(tmp_path / "riemann-hypothesis")
    replay_root, replay_head = _make_replay_repo(tmp_path / "alpoge-rh-replay")

    without_replay = collect_provenance(upstream_root)
    assert without_replay.schema_version == AUDIT_SCHEMA_VERSION
    assert without_replay.replay_pin is None
    assert without_replay.upstream == UpstreamRepoPin(
        repo_name="riemann-hypothesis",
        head_commit=upstream_head,
        branch="main",
        lean_toolchain="leanprover/lean4:v4.20.0",
        mathlib_rev="abcdef1234567890",
    )
    assert [item.config_path for item in without_replay.comparators] == [
        "comparator.json",
        "comparator/formal-conjectures.json",
    ]
    assert list(without_replay.comparator_file_digests.keys()) == [
        "comparator/formal-conjectures.json",
        "comparator/nested/aux.txt",
    ]
    assert without_replay.comparator_file_digests == {
        "comparator/formal-conjectures.json": _sha256(
            upstream_root / "comparator" / "formal-conjectures.json"
        ),
        "comparator/nested/aux.txt": _sha256(upstream_root / "comparator" / "nested" / "aux.txt"),
    }
    assert without_replay.note_digests == {
        "verification/NOTES.md": _sha256(upstream_root / "verification" / "NOTES.md"),
        "verification/static-scan.txt": _sha256(upstream_root / "verification" / "static-scan.txt"),
    }

    with_replay = collect_provenance(upstream_root, replay_root=replay_root)
    assert with_replay.replay_pin == ReplayPin(
        head_commit=replay_head,
        parts_sha256_digest=_sha256(replay_root / "parts.sha256"),
        lean_version="v4.20.0",
        lean_commit="aaaa1111",
        mathlib_commit="bbbb2222",
        module_count=175187,
    )


def test_collect_provenance_detached_head(tmp_path: Path) -> None:
    upstream_root, head = _make_upstream_repo(tmp_path / "upstream")
    _git(upstream_root, "checkout", "--detach", head)

    record = collect_provenance(upstream_root)
    assert record.upstream.head_commit == head
    assert record.upstream.branch == "HEAD"


def test_canonical_json_determinism_and_no_timestamps(tmp_path: Path) -> None:
    upstream_root, _ = _make_upstream_repo(tmp_path / "upstream")
    replay_root, _ = _make_replay_repo(tmp_path / "replay")

    first = collect_provenance(upstream_root, replay_root=replay_root)
    second = collect_provenance(upstream_root, replay_root=replay_root)
    rendered = first.to_json()
    assert rendered == second.to_json()
    assert rendered == json.dumps(first.to_dict(), indent=2, sort_keys=True) + "\n"
    assert rendered.endswith("\n")
    for forbidden in ("timestamp", "generated_at", "created_at", "collected_at"):
        assert forbidden not in rendered


def test_provenance_models_strict_roundtrip_and_rejection(tmp_path: Path) -> None:
    upstream_root, _ = _make_upstream_repo(tmp_path / "upstream")
    replay_root, _ = _make_replay_repo(tmp_path / "replay")
    record = collect_provenance(upstream_root, replay_root=replay_root)

    assert ProvenanceRecord.from_dict(record.to_dict()) == record
    assert UpstreamRepoPin.from_dict(record.upstream.to_dict()) == record.upstream
    assert record.replay_pin is not None
    assert ReplayPin.from_dict(record.replay_pin.to_dict()) == record.replay_pin
    assert ComparatorSummary.from_dict(record.comparators[0].to_dict()) == record.comparators[0]

    for cls, valid in (
        (ProvenanceRecord, record.to_dict()),
        (UpstreamRepoPin, record.upstream.to_dict()),
        (ReplayPin, record.replay_pin.to_dict()),
        (ComparatorSummary, record.comparators[0].to_dict()),
    ):
        missing = dict(valid)
        missing.pop(next(iter(missing)))
        with pytest.raises(ValueError, match="key mismatch"):
            cls.from_dict(missing)

        extra = {**valid, "unexpected_key": "nope"}
        with pytest.raises(ValueError, match="key mismatch"):
            cls.from_dict(extra)

    bad_replay: dict[str, Any] = {**record.replay_pin.to_dict(), "module_count": True}
    with pytest.raises(ValueError, match="must be an integer"):
        ReplayPin.from_dict(bad_replay)

    bad_comp: dict[str, Any] = {**record.comparators[0].to_dict(), "theorem_names": "not-a-list"}
    with pytest.raises(ValueError, match="must be a list"):
        ComparatorSummary.from_dict(bad_comp)

    bad_upstream: dict[str, Any] = {**record.upstream.to_dict(), "branch": 123}
    with pytest.raises(ValueError, match="must be a string"):
        UpstreamRepoPin.from_dict(bad_upstream)


@pytest.mark.parametrize(
    "missing_rel",
    [
        "lean-toolchain",
        "lake-manifest.json",
        "comparator.json",
        "comparator/formal-conjectures.json",
        "verification/NOTES.md",
        "verification/static-scan.txt",
    ],
)
def test_collect_provenance_missing_required_upstream_file(
    tmp_path: Path, missing_rel: str
) -> None:
    upstream_root, _ = _make_upstream_repo(tmp_path / "upstream")
    (upstream_root / missing_rel).unlink()
    with pytest.raises(AuditInputError):
        collect_provenance(upstream_root)


def test_collect_provenance_git_root_errors(tmp_path: Path) -> None:
    with pytest.raises(AuditInputError):
        collect_provenance(tmp_path / "does-not-exist")

    non_git = tmp_path / "not-a-git-repo"
    non_git.mkdir()
    with pytest.raises(AuditInputError):
        collect_provenance(non_git)

    upstream_root, _ = _make_upstream_repo(tmp_path / "upstream")
    with pytest.raises(AuditInputError):
        collect_provenance(upstream_root / "verification")


def test_collect_provenance_malformed_json_and_missing_mathlib(tmp_path: Path) -> None:
    upstream_root, _ = _make_upstream_repo(tmp_path / "upstream-bad-json")
    (upstream_root / "comparator.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(AuditInputError):
        collect_provenance(upstream_root)

    no_mathlib, _ = _make_upstream_repo(tmp_path / "upstream-no-mathlib")
    _write_json(
        no_mathlib / "lake-manifest.json",
        {"version": "1.1.0", "packages": [{"name": "batteries", "rev": "11111111"}]},
    )
    with pytest.raises(AuditInputError):
        collect_provenance(no_mathlib)


@pytest.mark.parametrize("missing_rel", ["parts.sha256", "tracker/summary.json"])
def test_collect_provenance_missing_replay_files(tmp_path: Path, missing_rel: str) -> None:
    upstream_root, _ = _make_upstream_repo(tmp_path / "upstream")
    replay_root, _ = _make_replay_repo(tmp_path / "replay")
    (replay_root / missing_rel).unlink()
    with pytest.raises(AuditInputError):
        collect_provenance(upstream_root, replay_root=replay_root)


def test_collect_provenance_rejects_bool_module_count_in_replay(tmp_path: Path) -> None:
    upstream_root, _ = _make_upstream_repo(tmp_path / "upstream")
    replay_root, _ = _make_replay_repo(tmp_path / "replay")
    _write_json(
        replay_root / "tracker" / "summary.json",
        {
            "lean": "v4.20.0",
            "lean_commit": "aaaa1111",
            "mathlib_commit": "bbbb2222",
            "module_count": False,
        },
    )
    with pytest.raises(AuditInputError):
        collect_provenance(upstream_root, replay_root=replay_root)


def _make_condensed_upstream_repo(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.37.0-rc1\n", encoding="utf-8")
    _write_json(
        root / "lake-manifest.json",
        {
            "version": "1.1.0",
            "packages": [{"name": "mathlib", "rev": "abcdef1234567890"}],
        },
    )
    _write_json(
        root / "comparator.json",
        {
            "challenge_module": "Challenge",
            "solution_module": "Main",
            "theorem_names": ["riemann_hypothesis"],
            "definition_names": [],
            "permitted_axioms": ["propext", "Classical.choice", "Quot.sound"],
            "enable_nanoda": True,
        },
    )
    for doc_name in ("README.md", "GUIDE.md", "CONTENTS.md", "INDEX.md", "REFERENCES.md"):
        (root / doc_name).write_text(f"# {doc_name}\n", encoding="utf-8")
    head = _init_git_repo(root)
    return root, head


def test_collect_provenance_condensed_edition_and_roundtrip(tmp_path: Path) -> None:
    upstream_root, head = _make_condensed_upstream_repo(tmp_path / "condensed-rh")
    record = collect_provenance(upstream_root)

    assert record.upstream.head_commit == head
    assert record.comparator_file_digests == {}
    assert len(record.comparators) == 1
    comp = record.comparators[0]
    assert comp == ComparatorSummary(
        config_path="comparator.json",
        challenge_module="Challenge",
        solution_module="Main",
        theorem_names=("riemann_hypothesis",),
        permitted_axioms=("propext", "Classical.choice", "Quot.sound"),
        external_kernels={},
        definition_names=(),
        enable_nanoda=True,
    )
    assert list(comp.to_dict().keys()) == [
        "challenge_module",
        "config_path",
        "definition_names",
        "enable_nanoda",
        "external_kernels",
        "permitted_axioms",
        "solution_module",
        "theorem_names",
    ]
    assert list(record.note_digests.keys()) == [
        "CONTENTS.md",
        "GUIDE.md",
        "INDEX.md",
        "README.md",
        "REFERENCES.md",
    ]
    assert ProvenanceRecord.from_dict(record.to_dict()) == record


def test_comparator_summary_optional_fields_and_strict_enable_nanoda() -> None:
    minimal: dict[str, Any] = {
        "config_path": "comparator.json",
        "challenge_module": "Challenge",
        "solution_module": "Main",
        "theorem_names": ["thm"],
        "permitted_axioms": ["propext"],
    }
    parsed = ComparatorSummary.from_dict(minimal)
    assert parsed.external_kernels == {}
    assert parsed.definition_names == ()
    assert parsed.enable_nanoda is None
    assert sorted(parsed.to_dict().keys()) == [
        "challenge_module",
        "config_path",
        "definition_names",
        "enable_nanoda",
        "external_kernels",
        "permitted_axioms",
        "solution_module",
        "theorem_names",
    ]

    for bad_bool in (0, 1, "true"):
        with pytest.raises(ValueError, match="boolean"):
            ComparatorSummary.from_dict({**minimal, "enable_nanoda": bad_bool})


def test_collect_provenance_condensed_fail_closed_cases(tmp_path: Path) -> None:
    no_docs, _ = _make_condensed_upstream_repo(tmp_path / "no-docs")
    (no_docs / "GUIDE.md").unlink()
    with pytest.raises(AuditInputError, match=r"verification/NOTES\.md or GUIDE\.md"):
        collect_provenance(no_docs)

    half_verif, _ = _make_condensed_upstream_repo(tmp_path / "half-verif")
    (half_verif / "verification").mkdir()
    (half_verif / "verification" / "NOTES.md").write_text("notes\n", encoding="utf-8")
    with pytest.raises(AuditInputError, match=r"verification/static-scan\.txt"):
        collect_provenance(half_verif)

    bad_comp_path, _ = _make_condensed_upstream_repo(tmp_path / "comp-file")
    (bad_comp_path / "comparator").write_text("not a dir\n", encoding="utf-8")
    with pytest.raises(AuditInputError, match="not a directory"):
        collect_provenance(bad_comp_path)

    bad_verif_path, _ = _make_condensed_upstream_repo(tmp_path / "verif-file")
    (bad_verif_path / "verification").write_text("not a dir\n", encoding="utf-8")
    with pytest.raises(AuditInputError, match="not a directory"):
        collect_provenance(bad_verif_path)

    unknown_key_repo, _ = _make_condensed_upstream_repo(tmp_path / "unknown-key")
    _write_json(
        unknown_key_repo / "comparator.json",
        {
            "challenge_module": "Challenge",
            "solution_module": "Main",
            "theorem_names": ["riemann_hypothesis"],
            "permitted_axioms": ["propext"],
            "enable_nanoda": True,
            "unexpected_option": 123,
        },
    )
    with pytest.raises(AuditInputError, match="unknown keys"):
        collect_provenance(unknown_key_repo)

    bare_comp_repo, _ = _make_condensed_upstream_repo(tmp_path / "bare-comp")
    _write_json(
        bare_comp_repo / "comparator.json",
        {
            "challenge_module": "Challenge",
            "solution_module": "Main",
            "theorem_names": ["riemann_hypothesis"],
            "permitted_axioms": ["propext"],
        },
    )
    with pytest.raises(AuditInputError, match="must include at least one of"):
        collect_provenance(bare_comp_repo)
