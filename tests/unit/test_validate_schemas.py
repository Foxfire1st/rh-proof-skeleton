"""Unit tests for repository JSON artifact schema validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rh_pipeline.models.audit import AUDIT_SCHEMA_VERSION
from rh_pipeline.models.dag import DAG_SCHEMA_VERSION, STAGE_MAP_SCHEMA_VERSION
from rh_pipeline.validate.schemas import validate_repo_artifacts

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMIT_A = "a" * 40
_COMMIT_B = "b" * 40
_COMMIT_C = "c" * 40
_DIGEST = "0" * 64


def _file_mapping(
    upstream_path: str = "src/Statements.lean",
    skeleton_file: str = "00_statement/Statements.lean",
    preserve_defs: bool = True,
) -> dict[str, Any]:
    return {
        "upstream_path": upstream_path,
        "skeleton_file": skeleton_file,
        "preserve_defs": preserve_defs,
    }


def _stage(
    stage_id: str,
    order: int,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "order": order,
        "title": stage_id,
        "summary": f"Summary for {stage_id}.",
        "files": [] if files is None else files,
    }


def _valid_provenance(commit: str = _COMMIT_A) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "upstream": {
            "repo_name": "riemann-hypothesis",
            "head_commit": commit,
            "branch": "main",
            "lean_toolchain": "leanprover/lean4:v4.37.0-rc1",
            "mathlib_rev": "1" * 40,
        },
        "replay_pin": {
            "head_commit": "2" * 40,
            "parts_sha256_digest": _DIGEST,
            "lean_version": "4.33.1",
            "lean_commit": "3" * 40,
            "mathlib_commit": "4" * 40,
            "module_count": 10,
        },
        "comparators": [],
        "comparator_file_digests": {},
        "note_digests": {},
    }


def _valid_verification_status(commit: str = _COMMIT_A) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "upstream_commit": commit,
        "lean_toolchain": "leanprover/lean4:v4.37.0-rc1",
        "mathlib_rev": "1" * 40,
        "build_status": "unverified-locally",
        "scan_scope": "entry",
        "files_scanned": ["src/Main.lean"],
        "solution_forbidden_token_count": 0,
        "challenge_sorry_count": 1,
        "theorem_names": ["riemannHypothesis"],
        "permitted_axioms": ["propext"],
        "community_verification_notes_sha256": _DIGEST,
        "upstream_static_scan_sha256": _DIGEST,
        "scan": {
            "scan_scope": "entry",
            "files_scanned": ["src/Main.lean"],
            "solution_findings": [],
            "challenge_findings": [
                {
                    "rel_path": "src/Challenge.lean",
                    "line": 10,
                    "token": "sorry",
                    "role": "challenge",
                }
            ],
            "challenge_sorry_count": 1,
        },
    }


def _valid_stage_map() -> dict[str, Any]:
    return {
        "schema_version": STAGE_MAP_SCHEMA_VERSION,
        "stages": [_stage("00_statement", 0, [_file_mapping()])],
    }


def _valid_dag(commit: str = _COMMIT_A) -> dict[str, Any]:
    return {
        "schema_version": DAG_SCHEMA_VERSION,
        "upstream_commit": commit,
        "stages": _valid_stage_map()["stages"],
        "nodes": [
            {
                "id": "00_statement/Statements",
                "stage_id": "00_statement",
                "title": "Statements",
                "skeleton_file": "00_statement/Statements.lean",
                "upstream_path": "src/Statements.lean",
                "declarations": [
                    {
                        "name": "riemannHypothesis",
                        "kind": "theorem",
                        "start_line": 10,
                        "end_line": 12,
                        "upstream_path": "src/Statements.lean",
                        "upstream_start_line": 20,
                        "upstream_end_line": 22,
                    }
                ],
                "depends_on": [],
                "is_landmark": True,
            }
        ],
    }


def _write_json(root: Path, rel_path: str, payload: object) -> None:
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_valid_repo(
    root: Path,
    *,
    provenance: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    stage_map: dict[str, Any] | None = None,
    dag: dict[str, Any] | None = None,
) -> None:
    _write_json(
        root, "audit/provenance.json", _valid_provenance() if provenance is None else provenance
    )
    _write_json(
        root,
        "audit/verification-status.json",
        _valid_verification_status() if verification is None else verification,
    )
    _write_json(root, "dag/stage-map.json", _valid_stage_map() if stage_map is None else stage_map)
    _write_json(root, "dag/dag.json", _valid_dag() if dag is None else dag)


def test_real_repo_artifacts_pass_cleanly() -> None:
    assert validate_repo_artifacts(_REPO_ROOT) == ()


def test_synthetic_valid_repo_passes_cleanly(tmp_path: Path) -> None:
    _write_valid_repo(tmp_path)
    assert validate_repo_artifacts(tmp_path) == ()


def test_missing_files_emit_schema_missing_sorted(tmp_path: Path) -> None:
    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-missing", "audit/provenance.json", 0),
        ("schema-missing", "audit/verification-status.json", 0),
        ("schema-missing", "dag/dag.json", 0),
        ("schema-missing", "dag/stage-map.json", 0),
    ]


def test_invalid_json_reports_line_number_and_unreadable_reports_line_zero(tmp_path: Path) -> None:
    _write_valid_repo(tmp_path)
    (tmp_path / "audit/provenance.json").write_text(
        '{\n  "schema_version": "1.0.0",\n  broken\n}\n', encoding="utf-8"
    )
    (tmp_path / "dag/dag.json").write_bytes(b"\xff\xfe")

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-json", "audit/provenance.json", 3),
        ("schema-json", "dag/dag.json", 0),
    ]


@pytest.mark.parametrize(
    "rel_path",
    [
        "audit/provenance.json",
        "audit/verification-status.json",
        "dag/stage-map.json",
        "dag/dag.json",
    ],
)
def test_unknown_key_emits_schema_invalid(tmp_path: Path, rel_path: str) -> None:
    _write_valid_repo(tmp_path)
    payload = json.loads((tmp_path / rel_path).read_text(encoding="utf-8"))
    payload["unexpected_key"] = "extra"
    _write_json(tmp_path, rel_path, payload)

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [("schema-invalid", rel_path, 0)]
    assert "unexpected_key" in findings[0].message


@pytest.mark.parametrize(
    ("rel_path", "missing_key"),
    [
        ("audit/provenance.json", "upstream"),
        ("audit/verification-status.json", "scan"),
        ("dag/stage-map.json", "stages"),
        ("dag/dag.json", "nodes"),
    ],
)
def test_missing_required_field_emits_schema_invalid(
    tmp_path: Path, rel_path: str, missing_key: str
) -> None:
    _write_valid_repo(tmp_path)
    payload = json.loads((tmp_path / rel_path).read_text(encoding="utf-8"))
    del payload[missing_key]
    _write_json(tmp_path, rel_path, payload)

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [("schema-invalid", rel_path, 0)]
    assert missing_key in findings[0].message


def test_bool_where_int_expected_emits_schema_invalid(tmp_path: Path) -> None:
    provenance = _valid_provenance()
    provenance["replay_pin"]["module_count"] = True
    verification = _valid_verification_status()
    verification["challenge_sorry_count"] = True
    stage_map = _valid_stage_map()
    stage_map["stages"][0]["order"] = True
    dag = _valid_dag()
    dag["nodes"][0]["declarations"][0]["start_line"] = True
    _write_valid_repo(
        tmp_path, provenance=provenance, verification=verification, stage_map=stage_map, dag=dag
    )

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-invalid", "audit/provenance.json", 0),
        ("schema-invalid", "audit/verification-status.json", 0),
        ("schema-invalid", "dag/dag.json", 0),
        ("schema-invalid", "dag/stage-map.json", 0),
    ]


@pytest.mark.parametrize(
    "rel_path",
    [
        "audit/provenance.json",
        "audit/verification-status.json",
        "dag/stage-map.json",
        "dag/dag.json",
    ],
)
def test_wrong_schema_version_emits_schema_invalid(tmp_path: Path, rel_path: str) -> None:
    _write_valid_repo(tmp_path)
    payload = json.loads((tmp_path / rel_path).read_text(encoding="utf-8"))
    payload["schema_version"] = "9.9.9"
    _write_json(tmp_path, rel_path, payload)

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [("schema-invalid", rel_path, 0)]
    assert "schema_version" in findings[0].message


@pytest.mark.parametrize(
    "stages",
    [
        [],
        [_stage("dup", 0), _stage("dup", 1)],
        [_stage("s0", 0), _stage("s2", 2)],
        [_stage("s0", 0, [_file_mapping("src/A.lean", "00_statement/A.txt")])],
        [_stage("s0", 0, [_file_mapping("src/A.txt", "00_statement/A.lean")])],
        [
            _stage(
                "s0",
                0,
                [
                    _file_mapping("src/A.lean", "00_statement/A1.lean"),
                    _file_mapping("src/A.lean", "00_statement/A2.lean"),
                ],
            )
        ],
        [
            _stage(
                "s0",
                0,
                [
                    _file_mapping("src/A.lean", "00_statement/Same.lean"),
                    _file_mapping("src/B.lean", "00_statement/Same.lean"),
                ],
            )
        ],
        [
            _stage(
                "s0",
                0,
                [
                    _file_mapping("src/Dup.lean", "00_statement/Dup1.lean"),
                    _file_mapping("comparator/Dup.lean", "00_statement/Dup2.lean"),
                ],
            )
        ],
    ],
    ids=[
        "empty-stages",
        "duplicate-stage-id",
        "non-consecutive-order",
        "skeleton-not-lean",
        "upstream-not-lean",
        "duplicate-upstream-path",
        "duplicate-skeleton-file",
        "duplicate-module-name",
    ],
)
def test_invalid_stage_map_structure_emits_schema_invalid(
    tmp_path: Path, stages: list[dict[str, Any]]
) -> None:
    _write_valid_repo(
        tmp_path,
        stage_map={"schema_version": STAGE_MAP_SCHEMA_VERSION, "stages": stages},
    )

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-invalid", "dag/stage-map.json", 0)
    ]


def test_commit_mismatch_provenance_vs_verification_only(tmp_path: Path) -> None:
    _write_valid_repo(
        tmp_path,
        provenance=_valid_provenance(_COMMIT_A),
        verification=_valid_verification_status(_COMMIT_B),
        dag=_valid_dag(_COMMIT_B),
    )
    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-commit-mismatch", "audit/verification-status.json", 0)
    ]


def test_commit_mismatch_dag_vs_verification(tmp_path: Path) -> None:
    _write_valid_repo(
        tmp_path,
        provenance=_valid_provenance(_COMMIT_A),
        verification=_valid_verification_status(_COMMIT_A),
        dag=_valid_dag(_COMMIT_B),
    )
    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-commit-mismatch", "dag/dag.json", 0)
    ]


def test_commit_mismatch_all_three_different(tmp_path: Path) -> None:
    _write_valid_repo(
        tmp_path,
        provenance=_valid_provenance(_COMMIT_A),
        verification=_valid_verification_status(_COMMIT_B),
        dag=_valid_dag(_COMMIT_C),
    )
    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-commit-mismatch", "audit/verification-status.json", 0),
        ("schema-commit-mismatch", "dag/dag.json", 0),
    ]


def test_commit_mismatch_dag_falls_back_to_provenance_when_verification_missing(
    tmp_path: Path,
) -> None:
    _write_valid_repo(
        tmp_path,
        provenance=_valid_provenance(_COMMIT_A),
        dag=_valid_dag(_COMMIT_B),
    )
    (tmp_path / "audit/verification-status.json").unlink()

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-missing", "audit/verification-status.json", 0),
        ("schema-commit-mismatch", "dag/dag.json", 0),
    ]


def test_commit_mismatch_dag_vs_verification_when_provenance_missing(tmp_path: Path) -> None:
    _write_valid_repo(
        tmp_path,
        verification=_valid_verification_status(_COMMIT_A),
        dag=_valid_dag(_COMMIT_B),
    )
    (tmp_path / "audit/provenance.json").unlink()

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [
        ("schema-missing", "audit/provenance.json", 0),
        ("schema-commit-mismatch", "dag/dag.json", 0),
    ]


def test_json_null_payload_emits_schema_invalid(tmp_path: Path) -> None:
    _write_valid_repo(tmp_path)
    (tmp_path / "dag/dag.json").write_text("null\n", encoding="utf-8")

    findings = validate_repo_artifacts(tmp_path)
    assert [(f.rule, f.path, f.line) for f in findings] == [("schema-invalid", "dag/dag.json", 0)]
