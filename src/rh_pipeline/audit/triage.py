"""Read-only upstream freshness triage against pinned provenance and stage-map files."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from rh_pipeline.graph.stage_map import StageMapError, load_stage_map
from rh_pipeline.models.audit import ProvenanceRecord, format_canonical_json

__all__ = [
    "EXIT_HISTORY_SWITCHED",
    "EXIT_INPUT_ERROR",
    "EXIT_MAPPED_DRIFT",
    "EXIT_UNMAPPED_ONLY",
    "EXIT_UP_TO_DATE",
    "ReplayTarget",
    "ReplayTriageStatus",
    "TriageInputError",
    "TriageStatus",
    "UpstreamTriageReport",
    "check_upstream",
    "format_triage_report",
]

TriageStatus = Literal["up-to-date", "unmapped-only", "mapped-drift", "history-switched"]
ReplayTriageStatus = Literal["not-checked", "not-pinned", "up-to-date", "moved", "history-switched"]

EXIT_UP_TO_DATE = 0
EXIT_UNMAPPED_ONLY = 0
EXIT_INPUT_ERROR = 2
EXIT_MAPPED_DRIFT = 10
EXIT_HISTORY_SWITCHED = 11

_AUDIT_TRACKED_FILES: frozenset[str] = frozenset(
    {
        "comparator.json",
        "lean-toolchain",
        "lake-manifest.json",
        "Challenge.lean",
        "src/Challenge.lean",
        "src/Main.lean",
        "src/Statements.lean",
        "README.md",
        "GUIDE.md",
        "INDEX.md",
        "CONTENTS.md",
        "REFERENCES.md",
    }
)
_AUDIT_TRACKED_PREFIXES: tuple[str, ...] = ("comparator/", "verification/")


class TriageInputError(ValueError):
    """Raised when triage inputs, Git repositories, refs, or schemas are invalid."""


@dataclass(frozen=True, slots=True)
class ReplayTarget:
    """Optional local replay repository and Git ref for informational freshness checks."""

    root: Path
    ref: str = "HEAD"


@dataclass(frozen=True, slots=True)
class _ReplayResult:
    status: ReplayTriageStatus
    pinned_commit: str | None
    target_commit: str | None
    ref: str | None


@dataclass(frozen=True, slots=True)
class _UpstreamDiffResult:
    status: TriageStatus
    exit_code: int
    commit_delta: int | None
    changed_file_count: int
    changed_audit_paths: tuple[str, ...] = ()
    changed_mapped_paths: tuple[str, ...] = ()
    affected_stages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UpstreamTriageReport:
    """Deterministic freshness report comparing a local upstream ref to pinned provenance."""

    status: TriageStatus
    exit_code: int
    pinned_commit: str
    target_commit: str
    ref: str
    commit_delta: int | None
    changed_file_count: int
    changed_audit_paths: tuple[str, ...]
    changed_mapped_paths: tuple[str, ...]
    affected_stages: tuple[str, ...]
    replay_status: ReplayTriageStatus
    replay_pinned_commit: str | None
    replay_target_commit: str | None
    replay_ref: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "affected_stages": list(self.affected_stages),
            "changed_audit_paths": list(self.changed_audit_paths),
            "changed_file_count": self.changed_file_count,
            "changed_mapped_paths": list(self.changed_mapped_paths),
            "commit_delta": self.commit_delta,
            "exit_code": self.exit_code,
            "pinned_commit": self.pinned_commit,
            "ref": self.ref,
            "replay_pinned_commit": self.replay_pinned_commit,
            "replay_ref": self.replay_ref,
            "replay_status": self.replay_status,
            "replay_target_commit": self.replay_target_commit,
            "status": self.status,
            "target_commit": self.target_commit,
        }

    def to_json(self) -> str:
        return format_canonical_json(self.to_dict())


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        raise TriageInputError(f"Failed to invoke git for {repo_root}: {exc}") from exc


def _run_git_required(repo_root: Path, *args: str) -> str:
    completed = _run_git(repo_root, *args)
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output:
        detail = completed.stderr.strip() or "empty or non-zero git response"
        raise TriageInputError(f"git {' '.join(args)} failed in {repo_root}: {detail}")
    return output


def _require_git_root(root: Path, *, label: str) -> Path:
    if not root.is_dir():
        raise TriageInputError(f"{label} directory does not exist: {root}")
    resolved = root.resolve()
    toplevel = Path(_run_git_required(resolved, "rev-parse", "--show-toplevel")).resolve()
    if toplevel != resolved:
        raise TriageInputError(
            f"{label} must be a Git repository root, got {resolved} (toplevel={toplevel})"
        )
    return resolved


def _resolve_commit(repo_root: Path, ref: str, *, label: str) -> str:
    if not ref.strip() or ref.startswith("-"):
        raise TriageInputError(f"Invalid {label} git ref: {ref!r}")
    return _run_git_required(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _commit_exists(repo_root: Path, commit: str) -> bool:
    if not commit or commit.startswith("-"):
        return False
    return _run_git(repo_root, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    if not _commit_exists(repo_root, ancestor):
        return False
    completed = _run_git(repo_root, "merge-base", "--is-ancestor", ancestor, descendant)
    if completed.returncode in (0, 1):
        return completed.returncode == 0
    detail = completed.stderr.strip() or f"exit code {completed.returncode}"
    raise TriageInputError(f"git merge-base failed in {repo_root}: {detail}")


def _list_changed_paths(repo_root: Path, pinned: str, target: str) -> tuple[str, ...]:
    # Keep --no-renames so renamed mapped or audit-tracked files still appear under old paths.
    completed = _run_git(
        repo_root, "diff", "--no-renames", "--name-only", "-z", f"{pinned}..{target}"
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise TriageInputError(f"git diff failed in {repo_root}: {detail}")
    return tuple(sorted({entry for entry in completed.stdout.split("\0") if entry}))


def _load_provenance(path: Path) -> ProvenanceRecord:
    if not path.is_file():
        raise TriageInputError(f"Missing provenance file: {path}")
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        return ProvenanceRecord.from_dict(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TriageInputError(f"Invalid provenance file {path}: {exc}") from exc


def _load_mapped_paths(path: Path) -> dict[str, str]:
    try:
        stages = load_stage_map(path)
    except StageMapError as exc:
        raise TriageInputError(str(exc)) from exc
    return {mapping.upstream_path: stage.stage_id for stage in stages for mapping in stage.files}


def _is_audit_tracked(rel_path: str) -> bool:
    return rel_path in _AUDIT_TRACKED_FILES or rel_path.startswith(_AUDIT_TRACKED_PREFIXES)


def _partition_changed_paths(
    changed: tuple[str, ...], mapped_paths: dict[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    audit_paths = tuple(path for path in changed if _is_audit_tracked(path))
    mapped = tuple(path for path in changed if path in mapped_paths)
    affected = tuple(sorted({mapped_paths[path] for path in mapped}))
    return audit_paths, mapped, affected


def _classify_upstream(
    repo_root: Path, pinned: str, target: str, mapped_paths: dict[str, str]
) -> _UpstreamDiffResult:
    if target == pinned:
        return _UpstreamDiffResult("up-to-date", EXIT_UP_TO_DATE, 0, 0)
    if not _is_ancestor(repo_root, pinned, target):
        return _UpstreamDiffResult("history-switched", EXIT_HISTORY_SWITCHED, None, 0)

    delta = int(_run_git_required(repo_root, "rev-list", "--count", f"{pinned}..{target}"))
    changed = _list_changed_paths(repo_root, pinned, target)
    audit_paths, mapped, affected = _partition_changed_paths(changed, mapped_paths)
    if audit_paths or mapped:
        return _UpstreamDiffResult(
            "mapped-drift", EXIT_MAPPED_DRIFT, delta, len(changed), audit_paths, mapped, affected
        )
    return _UpstreamDiffResult("unmapped-only", EXIT_UNMAPPED_ONLY, delta, len(changed))


def _triage_replay(provenance: ProvenanceRecord, replay: ReplayTarget | None) -> _ReplayResult:
    pinned = None if provenance.replay_pin is None else provenance.replay_pin.head_commit
    if replay is None:
        return _ReplayResult("not-checked", pinned, None, None)

    replay_root = _require_git_root(replay.root, label="replay_root")
    target = _resolve_commit(replay_root, replay.ref, label="replay_ref")
    if pinned is None:
        return _ReplayResult("not-pinned", None, target, replay.ref)
    if target == pinned:
        return _ReplayResult("up-to-date", pinned, target, replay.ref)
    status: ReplayTriageStatus = (
        "moved" if _is_ancestor(replay_root, pinned, target) else "history-switched"
    )
    return _ReplayResult(status, pinned, target, replay.ref)


def check_upstream(
    upstream_root: Path,
    *,
    ref: str = "HEAD",
    provenance_path: Path = Path("audit/provenance.json"),
    stage_map_path: Path = Path("dag/stage-map.json"),
    replay: ReplayTarget | None = None,
) -> UpstreamTriageReport:
    """Compare a local upstream Git ref against pinned provenance and stage-map files."""
    provenance = _load_provenance(provenance_path)
    mapped_paths = _load_mapped_paths(stage_map_path)
    resolved_upstream = _require_git_root(upstream_root, label="upstream_root")
    target_commit = _resolve_commit(resolved_upstream, ref, label="ref")
    diff = _classify_upstream(
        resolved_upstream, provenance.upstream.head_commit, target_commit, mapped_paths
    )
    replay_res = _triage_replay(provenance, replay)
    return UpstreamTriageReport(
        status=diff.status,
        exit_code=diff.exit_code,
        pinned_commit=provenance.upstream.head_commit,
        target_commit=target_commit,
        ref=ref,
        commit_delta=diff.commit_delta,
        changed_file_count=diff.changed_file_count,
        changed_audit_paths=diff.changed_audit_paths,
        changed_mapped_paths=diff.changed_mapped_paths,
        affected_stages=diff.affected_stages,
        replay_status=replay_res.status,
        replay_pinned_commit=replay_res.pinned_commit,
        replay_target_commit=replay_res.target_commit,
        replay_ref=replay_res.ref,
    )


def format_triage_report(report: UpstreamTriageReport) -> str:
    """Render a deterministic human-readable summary of an UpstreamTriageReport."""
    delta = "n/a" if report.commit_delta is None else str(report.commit_delta)
    replay_detail = report.replay_status
    if report.replay_target_commit is not None:
        pinned = "none" if report.replay_pinned_commit is None else report.replay_pinned_commit[:12]
        replay_detail = (
            f"{report.replay_status} (ref={report.replay_ref}, "
            f"pinned={pinned}, target={report.replay_target_commit[:12]})"
        )
    return (
        "\n".join(
            (
                f"Status: {report.status} (exit {report.exit_code})",
                f"Ref: {report.ref}",
                f"Pinned commit: {report.pinned_commit}",
                f"Target commit: {report.target_commit}",
                f"Commit delta: {delta} (changed files: {report.changed_file_count})",
                f"Changed audit paths: {', '.join(report.changed_audit_paths) or 'none'}",
                f"Changed mapped paths: {', '.join(report.changed_mapped_paths) or 'none'}",
                f"Affected stages: {', '.join(report.affected_stages) or 'none'}",
                f"Replay status: {replay_detail}",
            )
        )
        + "\n"
    )
