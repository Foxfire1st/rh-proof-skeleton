"""Assemble self-contained verification status and write Phase 0 audit artifacts atomically."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from rh_pipeline.audit.provenance import AuditInputError
from rh_pipeline.models.audit import (
    ProvenanceRecord,
    StaticScanSummary,
    VerificationStatus,
)

NOTES_DIGEST_KEY = "verification/NOTES.md"
STATIC_SCAN_DIGEST_KEY = "verification/static-scan.txt"


def _dedupe_preserve_order(values: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate strings while preserving first-seen order."""
    return tuple(dict.fromkeys(values))


def _resolve_verification_digests(
    note_digests: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """Return paired verification digests or (None, None) when verification notes are absent."""
    has_notes = NOTES_DIGEST_KEY in note_digests
    has_scan = STATIC_SCAN_DIGEST_KEY in note_digests
    if not has_notes and not has_scan:
        return None, None
    if not has_notes:
        raise AuditInputError(f"Missing required verification note digest: {NOTES_DIGEST_KEY}")
    if not has_scan:
        raise AuditInputError(
            f"Missing required verification note digest: {STATIC_SCAN_DIGEST_KEY}"
        )
    notes_digest = note_digests[NOTES_DIGEST_KEY]
    scan_digest = note_digests[STATIC_SCAN_DIGEST_KEY]
    if not notes_digest:
        raise AuditInputError(f"Empty verification note digest: {NOTES_DIGEST_KEY}")
    if not scan_digest:
        raise AuditInputError(f"Empty verification note digest: {STATIC_SCAN_DIGEST_KEY}")
    return notes_digest, scan_digest


def build_verification_status(
    provenance: ProvenanceRecord,
    scan: StaticScanSummary,
) -> VerificationStatus:
    """Build a self-contained VerificationStatus from provenance and static-scan results."""
    theorem_names = _dedupe_preserve_order(
        name for comp in provenance.comparators for name in comp.theorem_names
    )
    permitted_axioms = _dedupe_preserve_order(
        axiom for comp in provenance.comparators for axiom in comp.permitted_axioms
    )
    notes_sha256, static_scan_sha256 = _resolve_verification_digests(provenance.note_digests)

    return VerificationStatus(
        schema_version=provenance.schema_version,
        upstream_commit=provenance.upstream.head_commit,
        lean_toolchain=provenance.upstream.lean_toolchain,
        mathlib_rev=provenance.upstream.mathlib_rev,
        build_status="unverified-locally",
        scan_scope=scan.scan_scope,
        files_scanned=scan.files_scanned,
        solution_forbidden_token_count=len(scan.solution_findings),
        challenge_sorry_count=scan.challenge_sorry_count,
        theorem_names=theorem_names,
        permitted_axioms=permitted_axioms,
        community_verification_notes_sha256=notes_sha256,
        upstream_static_scan_sha256=static_scan_sha256,
        scan=scan,
    )


def _write_temp_file(audit_dir: Path, prefix: str, content: str) -> Path:
    """Write UTF-8 text to a flushed, fsynced temporary file inside audit_dir."""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=audit_dir,
            prefix=prefix,
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return tmp_path
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def write_audit_artifacts(
    audit_dir: Path,
    provenance: ProvenanceRecord,
    status: VerificationStatus,
) -> None:
    """Stage both audit JSON files in audit_dir before atomically replacing targets."""
    provenance_json = provenance.to_json()
    status_json = status.to_json()
    audit_dir.mkdir(parents=True, exist_ok=True)

    staged: list[tuple[Path, Path]] = []
    try:
        prov_tmp = _write_temp_file(audit_dir, ".provenance.", provenance_json)
        staged.append((prov_tmp, audit_dir / "provenance.json"))
        status_tmp = _write_temp_file(audit_dir, ".verification-status.", status_json)
        staged.append((status_tmp, audit_dir / "verification-status.json"))
        for tmp_path, target_path in staged:
            os.replace(tmp_path, target_path)
    except BaseException:
        for tmp_path, _ in staged:
            tmp_path.unlink(missing_ok=True)
        raise
