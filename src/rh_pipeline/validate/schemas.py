"""Strict schema and cross-file commit validation for repository JSON artifacts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from rh_pipeline.graph.stage_map import load_stage_map
from rh_pipeline.models.audit import (
    AUDIT_SCHEMA_VERSION,
    ProvenanceRecord,
    VerificationStatus,
)
from rh_pipeline.models.dag import DagGraph, StageMap
from rh_pipeline.models.validation import ValidationFinding, sort_findings

__all__ = ["validate_repo_artifacts"]

_PROVENANCE_REL_PATH = "audit/provenance.json"
_VERIFICATION_REL_PATH = "audit/verification-status.json"
_STAGE_MAP_REL_PATH = "dag/stage-map.json"
_DAG_REL_PATH = "dag/dag.json"


def _read_json_file(repo_root: Path, rel_path: str) -> tuple[object, ValidationFinding | None]:
    path = repo_root / rel_path
    if not path.exists():
        return None, ValidationFinding(
            "schema-missing", rel_path, 0, f"Required artifact is missing: {rel_path}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, ValidationFinding("schema-json", rel_path, 0, f"Unable to read JSON: {exc}")
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, ValidationFinding(
            "schema-json", rel_path, max(exc.lineno, 0), f"Invalid JSON: {exc.msg}"
        )


def _validate_artifact[T](
    repo_root: Path,
    rel_path: str,
    parser: Callable[[object, Path], T],
) -> tuple[T | None, ValidationFinding | None]:
    raw, read_finding = _read_json_file(repo_root, rel_path)
    if read_finding is not None:
        return None, read_finding
    try:
        return parser(raw, repo_root / rel_path), None
    except ValueError as exc:
        return None, ValidationFinding("schema-invalid", rel_path, 0, str(exc))


def _parse_provenance(raw: object, _path: Path) -> ProvenanceRecord:
    record = ProvenanceRecord.from_dict(raw)
    if record.schema_version != AUDIT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must equal {AUDIT_SCHEMA_VERSION!r}, got {record.schema_version!r}"
        )
    return record


def _parse_verification_status(raw: object, _path: Path) -> VerificationStatus:
    status = VerificationStatus.from_dict(raw)
    if status.schema_version != AUDIT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must equal {AUDIT_SCHEMA_VERSION!r}, got {status.schema_version!r}"
        )
    return status


def _parse_stage_map(raw: object, path: Path) -> StageMap:
    stage_map = StageMap.from_dict(raw)
    # Delegate structural invariants (unique IDs, consecutive order, unique paths/modules)
    # to load_stage_map so the validator never drifts from the stage-map loader.
    load_stage_map(path)
    return stage_map


def _parse_dag(raw: object, _path: Path) -> DagGraph:
    return DagGraph.from_dict(raw)


def _commit_mismatch(
    rel_path: str, actual: str, expected_source: str, expected: str
) -> ValidationFinding | None:
    if actual == expected:
        return None
    return ValidationFinding(
        "schema-commit-mismatch",
        rel_path,
        0,
        f"upstream_commit {actual!r} does not match {expected_source} {expected!r}",
    )


def _check_verification_commit(
    provenance: ProvenanceRecord | None,
    verification: VerificationStatus | None,
) -> ValidationFinding | None:
    if provenance is None or verification is None:
        return None
    return _commit_mismatch(
        _VERIFICATION_REL_PATH,
        verification.upstream_commit,
        f"{_PROVENANCE_REL_PATH} upstream.head_commit",
        provenance.upstream.head_commit,
    )


def _check_dag_commit(
    provenance: ProvenanceRecord | None,
    verification: VerificationStatus | None,
    dag: DagGraph | None,
) -> ValidationFinding | None:
    if dag is None:
        return None
    if verification is not None:
        return _commit_mismatch(
            _DAG_REL_PATH,
            dag.upstream_commit,
            f"{_VERIFICATION_REL_PATH} upstream_commit",
            verification.upstream_commit,
        )
    if provenance is not None:
        return _commit_mismatch(
            _DAG_REL_PATH,
            dag.upstream_commit,
            f"{_PROVENANCE_REL_PATH} upstream.head_commit",
            provenance.upstream.head_commit,
        )
    return None


def validate_repo_artifacts(repo_root: Path) -> tuple[ValidationFinding, ...]:
    """Validate repository JSON artifacts against strict schemas and commit consistency."""
    provenance, prov_finding = _validate_artifact(
        repo_root, _PROVENANCE_REL_PATH, _parse_provenance
    )
    verification, verif_finding = _validate_artifact(
        repo_root, _VERIFICATION_REL_PATH, _parse_verification_status
    )
    _, stage_finding = _validate_artifact(repo_root, _STAGE_MAP_REL_PATH, _parse_stage_map)
    dag, dag_finding = _validate_artifact(repo_root, _DAG_REL_PATH, _parse_dag)

    raw_findings: list[ValidationFinding | None] = [
        prov_finding,
        verif_finding,
        stage_finding,
        dag_finding,
        _check_verification_commit(provenance, verification),
        _check_dag_commit(provenance, verification, dag),
    ]
    return sort_findings(finding for finding in raw_findings if finding is not None)
