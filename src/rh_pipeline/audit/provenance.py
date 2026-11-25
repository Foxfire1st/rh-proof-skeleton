"""Read-only upstream and replay provenance collection for Phase 0 audit."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast

from rh_pipeline.models.audit import (
    AUDIT_SCHEMA_VERSION,
    ComparatorSummary,
    ProvenanceRecord,
    ReplayPin,
    UpstreamRepoPin,
)

__all__ = ["AuditInputError", "collect_provenance"]

_COMPARATOR_CONFIG_PATHS: tuple[str, ...] = (
    "comparator.json",
    "comparator/formal-conjectures.json",
)
_NOTE_PATHS: tuple[str, ...] = (
    "verification/NOTES.md",
    "verification/static-scan.txt",
)
_COMPARATOR_KEYS: tuple[str, ...] = (
    "challenge_module",
    "solution_module",
    "theorem_names",
    "permitted_axioms",
    "external_kernels",
)
_REPLAY_SUMMARY_KEYS: tuple[str, ...] = (
    "lean",
    "lean_commit",
    "mathlib_commit",
    "module_count",
)


class AuditInputError(ValueError):
    """Raised when required upstream or replay provenance inputs are missing or invalid."""


def _require_dir(path: Path, *, label: str) -> Path:
    if not path.is_dir():
        raise AuditInputError(f"{label} directory does not exist: {path}")
    return path.resolve()


def _require_file(path: Path, *, label: str) -> Path:
    if not path.is_file():
        raise AuditInputError(f"Missing required {label} file: {path}")
    return path


def _require_keys(obj: dict[str, object], keys: tuple[str, ...], *, label: str) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        raise AuditInputError(f"{label} missing required keys: {missing}")


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise AuditInputError(f"Failed to invoke git for {repo_root}: {exc}") from exc
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output:
        detail = completed.stderr.strip() or "empty or non-zero git response"
        raise AuditInputError(f"git {' '.join(args)} failed in {repo_root}: {detail}")
    return output


def _inspect_git_root(root: Path, *, label: str) -> tuple[Path, str, str]:
    resolved = _require_dir(root, label=label)
    toplevel = Path(_run_git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if toplevel != resolved:
        raise AuditInputError(
            f"{label} must be a Git repository root, got {resolved} (toplevel={toplevel})"
        )
    head_commit = _run_git(resolved, "rev-parse", "--verify", "HEAD")
    branch = _run_git(resolved, "rev-parse", "--abbrev-ref", "HEAD")
    return resolved, head_commit, branch


def _sha256_file(path: Path, *, label: str) -> str:
    _require_file(path, label=label)
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise AuditInputError(f"Unable to hash {label} ({path}): {exc}") from exc


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    _require_file(path, label=label)
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditInputError(f"Invalid JSON in {label} ({path}): {exc}") from exc
    if not isinstance(parsed, dict):
        raise AuditInputError(f"{label} must contain a JSON object: {path}")
    return cast(dict[str, object], parsed)


def _read_lean_toolchain(upstream_root: Path) -> str:
    path = _require_file(upstream_root / "lean-toolchain", label="lean-toolchain")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise AuditInputError(f"Unable to read lean-toolchain ({path}): {exc}") from exc
    if not value:
        raise AuditInputError(f"lean-toolchain must not be empty: {path}")
    return value


def _find_mathlib_entry(packages: list[object]) -> dict[str, object]:
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        pkg = cast(dict[object, object], entry)
        if pkg.get("name") == "mathlib":
            return cast(dict[str, object], pkg)
    raise AuditInputError("lake-manifest.json is missing a 'mathlib' package entry")


def _read_mathlib_rev(upstream_root: Path) -> str:
    manifest = _read_json_object(upstream_root / "lake-manifest.json", label="lake-manifest.json")
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise AuditInputError("lake-manifest.json 'packages' must be a list")
    entry = _find_mathlib_entry(cast(list[object], packages))
    rev = entry.get("rev")
    if not isinstance(rev, str) or not rev:
        raise AuditInputError("lake-manifest.json mathlib 'rev' must be a non-empty string")
    return rev


def _read_comparator_summary(upstream_root: Path, rel_path: str) -> ComparatorSummary:
    raw = _read_json_object(upstream_root / rel_path, label=rel_path)
    _require_keys(raw, _COMPARATOR_KEYS, label=rel_path)
    payload: dict[str, object] = {
        "config_path": rel_path,
        **{key: raw[key] for key in _COMPARATOR_KEYS},
    }
    try:
        return ComparatorSummary.from_dict(payload)
    except ValueError as exc:
        raise AuditInputError(f"Invalid comparator config {rel_path}: {exc}") from exc


def _collect_comparator_digests(upstream_root: Path) -> dict[str, str]:
    comp_dir = _require_dir(upstream_root / "comparator", label="comparator")
    digests: dict[str, str] = {}
    for path in comp_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(upstream_root).as_posix()
            digests[rel] = _sha256_file(path, label=rel)
    return dict(sorted(digests.items()))


def _collect_note_digests(upstream_root: Path) -> dict[str, str]:
    return {rel: _sha256_file(upstream_root / rel, label=rel) for rel in _NOTE_PATHS}


def _collect_replay_pin(replay_root: Path) -> ReplayPin:
    resolved, head_commit, _ = _inspect_git_root(replay_root, label="replay_root")
    parts_digest = _sha256_file(resolved / "parts.sha256", label="parts.sha256")
    summary = _read_json_object(resolved / "tracker" / "summary.json", label="tracker/summary.json")
    _require_keys(summary, _REPLAY_SUMMARY_KEYS, label="tracker/summary.json")
    payload: dict[str, object] = {
        "head_commit": head_commit,
        "parts_sha256_digest": parts_digest,
        "lean_version": summary["lean"],
        "lean_commit": summary["lean_commit"],
        "mathlib_commit": summary["mathlib_commit"],
        "module_count": summary["module_count"],
    }
    try:
        return ReplayPin.from_dict(payload)
    except ValueError as exc:
        raise AuditInputError(f"Invalid tracker/summary.json: {exc}") from exc


def collect_provenance(
    upstream_root: Path,
    *,
    replay_root: Path | None = None,
) -> ProvenanceRecord:
    """Collect deterministic read-only provenance from an upstream checkout."""
    resolved, head_commit, branch = _inspect_git_root(upstream_root, label="upstream_root")
    upstream = UpstreamRepoPin(
        repo_name=resolved.name,
        head_commit=head_commit,
        branch=branch,
        lean_toolchain=_read_lean_toolchain(resolved),
        mathlib_rev=_read_mathlib_rev(resolved),
    )
    comparators = tuple(
        _read_comparator_summary(resolved, rel_path) for rel_path in _COMPARATOR_CONFIG_PATHS
    )
    replay_pin = None if replay_root is None else _collect_replay_pin(replay_root)
    return ProvenanceRecord(
        schema_version=AUDIT_SCHEMA_VERSION,
        upstream=upstream,
        replay_pin=replay_pin,
        comparators=comparators,
        comparator_file_digests=_collect_comparator_digests(resolved),
        note_digests=_collect_note_digests(resolved),
    )
