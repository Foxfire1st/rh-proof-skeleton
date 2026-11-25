"""Immutable Phase 0 audit domain models with strict serialization helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

AUDIT_SCHEMA_VERSION = "1.0.0"

ScanScope = Literal["entry", "full"]
ScanRole = Literal["solution", "challenge"]
BuildStatus = Literal["unverified-locally"]

_VALID_SCOPES: frozenset[str] = frozenset({"entry", "full"})
_VALID_ROLES: frozenset[str] = frozenset({"solution", "challenge"})
_VALID_BUILD_STATUSES: frozenset[str] = frozenset({"unverified-locally"})


def format_canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize a mapping to deterministic sorted-key JSON with a trailing newline."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _as_object(data: object, *, label: str, required_keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    raw = cast(Mapping[object, object], data)
    keys = set(raw.keys())
    if not all(isinstance(key, str) for key in keys):
        raise ValueError(f"{label} keys must be strings")
    str_data = cast(Mapping[str, object], raw)
    actual_keys = frozenset(str_data.keys())
    missing = sorted(required_keys - actual_keys)
    extra = sorted(actual_keys - required_keys)
    if missing or extra:
        raise ValueError(f"{label} key mismatch (missing={missing}, extra={extra})")
    return str_data


def _require_str(value: object, *, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _require_int(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def _require_str_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of strings")
    items = cast(list[object] | tuple[object, ...], value)
    return tuple(_require_str(item, field=f"{field}[{idx}]") for idx, item in enumerate(items))


def _require_str_mapping(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object of string digests")
    raw = cast(Mapping[object, object], value)
    result: dict[str, str] = {}
    for key, val in raw.items():
        k = _require_str(key, field=f"{field} key")
        result[k] = _require_str(val, field=f"{field}[{k}]")
    return dict(sorted(result.items()))


def _require_kernel_mapping(value: object, *, field: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object mapping kernel names to command lists")
    raw = cast(Mapping[object, object], value)
    result: dict[str, tuple[str, ...]] = {}
    for key, val in raw.items():
        k = _require_str(key, field=f"{field} key")
        result[k] = _require_str_tuple(val, field=f"{field}[{k}]")
    return dict(sorted(result.items()))


def _require_literal(value: object, *, field: str, allowed: frozenset[str]) -> str:
    text = _require_str(value, field=field)
    if text not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}, got {text!r}")
    return text


@dataclass(frozen=True, slots=True)
class UpstreamRepoPin:
    """Exact Git and toolchain pin for the upstream riemann-hypothesis checkout."""

    repo_name: str
    head_commit: str
    branch: str
    lean_toolchain: str
    mathlib_rev: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "head_commit": self.head_commit,
            "lean_toolchain": self.lean_toolchain,
            "mathlib_rev": self.mathlib_rev,
            "repo_name": self.repo_name,
        }

    @classmethod
    def from_dict(cls, data: object) -> UpstreamRepoPin:
        obj = _as_object(
            data,
            label="UpstreamRepoPin",
            required_keys=frozenset(
                {"repo_name", "head_commit", "branch", "lean_toolchain", "mathlib_rev"}
            ),
        )
        return cls(
            repo_name=_require_str(obj["repo_name"], field="repo_name"),
            head_commit=_require_str(obj["head_commit"], field="head_commit"),
            branch=_require_str(obj["branch"], field="branch"),
            lean_toolchain=_require_str(obj["lean_toolchain"], field="lean_toolchain"),
            mathlib_rev=_require_str(obj["mathlib_rev"], field="mathlib_rev"),
        )


@dataclass(frozen=True, slots=True)
class ReplayPin:
    """Optional provenance pin for the alpoge-rh-replay metadata checkout."""

    head_commit: str
    parts_sha256_digest: str
    lean_version: str
    lean_commit: str
    mathlib_commit: str
    module_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "head_commit": self.head_commit,
            "lean_commit": self.lean_commit,
            "lean_version": self.lean_version,
            "mathlib_commit": self.mathlib_commit,
            "module_count": self.module_count,
            "parts_sha256_digest": self.parts_sha256_digest,
        }

    @classmethod
    def from_dict(cls, data: object) -> ReplayPin:
        obj = _as_object(
            data,
            label="ReplayPin",
            required_keys=frozenset(
                {
                    "head_commit",
                    "parts_sha256_digest",
                    "lean_version",
                    "lean_commit",
                    "mathlib_commit",
                    "module_count",
                }
            ),
        )
        return cls(
            head_commit=_require_str(obj["head_commit"], field="head_commit"),
            parts_sha256_digest=_require_str(
                obj["parts_sha256_digest"], field="parts_sha256_digest"
            ),
            lean_version=_require_str(obj["lean_version"], field="lean_version"),
            lean_commit=_require_str(obj["lean_commit"], field="lean_commit"),
            mathlib_commit=_require_str(obj["mathlib_commit"], field="mathlib_commit"),
            module_count=_require_int(obj["module_count"], field="module_count", minimum=0),
        )


@dataclass(frozen=True, slots=True)
class ComparatorSummary:
    """Summary of one upstream Comparator configuration file."""

    config_path: str
    challenge_module: str
    solution_module: str
    theorem_names: tuple[str, ...]
    permitted_axioms: tuple[str, ...]
    external_kernels: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        frozen_kernels = MappingProxyType(
            {k: tuple(v) for k, v in sorted(self.external_kernels.items())}
        )
        object.__setattr__(self, "external_kernels", frozen_kernels)

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_module": self.challenge_module,
            "config_path": self.config_path,
            "external_kernels": {k: list(v) for k, v in sorted(self.external_kernels.items())},
            "permitted_axioms": list(self.permitted_axioms),
            "solution_module": self.solution_module,
            "theorem_names": list(self.theorem_names),
        }

    @classmethod
    def from_dict(cls, data: object) -> ComparatorSummary:
        obj = _as_object(
            data,
            label="ComparatorSummary",
            required_keys=frozenset(
                {
                    "config_path",
                    "challenge_module",
                    "solution_module",
                    "theorem_names",
                    "permitted_axioms",
                    "external_kernels",
                }
            ),
        )
        return cls(
            config_path=_require_str(obj["config_path"], field="config_path"),
            challenge_module=_require_str(obj["challenge_module"], field="challenge_module"),
            solution_module=_require_str(obj["solution_module"], field="solution_module"),
            theorem_names=_require_str_tuple(obj["theorem_names"], field="theorem_names"),
            permitted_axioms=_require_str_tuple(obj["permitted_axioms"], field="permitted_axioms"),
            external_kernels=_require_kernel_mapping(
                obj["external_kernels"], field="external_kernels"
            ),
        )


@dataclass(frozen=True, slots=True)
class StaticScanFinding:
    """One deterministic forbidden/audit token occurrence in a Lean source file."""

    rel_path: str
    line: int
    token: str
    role: ScanRole

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "rel_path": self.rel_path,
            "role": self.role,
            "token": self.token,
        }

    @classmethod
    def from_dict(cls, data: object) -> StaticScanFinding:
        obj = _as_object(
            data,
            label="StaticScanFinding",
            required_keys=frozenset({"rel_path", "line", "token", "role"}),
        )
        role = cast(ScanRole, _require_literal(obj["role"], field="role", allowed=_VALID_ROLES))
        return cls(
            rel_path=_require_str(obj["rel_path"], field="rel_path"),
            line=_require_int(obj["line"], field="line", minimum=1),
            token=_require_str(obj["token"], field="token"),
            role=role,
        )


@dataclass(frozen=True, slots=True)
class StaticScanSummary:
    """Aggregated deterministic static-scan findings across scanned Lean files."""

    scan_scope: ScanScope
    files_scanned: tuple[str, ...]
    solution_findings: tuple[StaticScanFinding, ...]
    challenge_findings: tuple[StaticScanFinding, ...]
    challenge_sorry_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_findings": [item.to_dict() for item in self.challenge_findings],
            "challenge_sorry_count": self.challenge_sorry_count,
            "files_scanned": list(self.files_scanned),
            "scan_scope": self.scan_scope,
            "solution_findings": [item.to_dict() for item in self.solution_findings],
        }

    @classmethod
    def from_dict(cls, data: object) -> StaticScanSummary:
        obj = _as_object(
            data,
            label="StaticScanSummary",
            required_keys=frozenset(
                {
                    "scan_scope",
                    "files_scanned",
                    "solution_findings",
                    "challenge_findings",
                    "challenge_sorry_count",
                }
            ),
        )
        scope = cast(
            ScanScope,
            _require_literal(obj["scan_scope"], field="scan_scope", allowed=_VALID_SCOPES),
        )
        sol_raw = obj["solution_findings"]
        ch_raw = obj["challenge_findings"]
        if not isinstance(sol_raw, (list, tuple)) or not isinstance(ch_raw, (list, tuple)):
            raise ValueError("solution_findings and challenge_findings must be lists")
        sol_items = cast(list[object] | tuple[object, ...], sol_raw)
        ch_items = cast(list[object] | tuple[object, ...], ch_raw)
        return cls(
            scan_scope=scope,
            files_scanned=_require_str_tuple(obj["files_scanned"], field="files_scanned"),
            solution_findings=tuple(StaticScanFinding.from_dict(item) for item in sol_items),
            challenge_findings=tuple(StaticScanFinding.from_dict(item) for item in ch_items),
            challenge_sorry_count=_require_int(
                obj["challenge_sorry_count"], field="challenge_sorry_count", minimum=0
            ),
        )


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """Machine-generated upstream provenance record emitted to audit/provenance.json."""

    schema_version: str
    upstream: UpstreamRepoPin
    replay_pin: ReplayPin | None
    comparators: tuple[ComparatorSummary, ...]
    comparator_file_digests: Mapping[str, str]
    note_digests: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "comparator_file_digests",
            MappingProxyType(dict(sorted(self.comparator_file_digests.items()))),
        )
        object.__setattr__(
            self, "note_digests", MappingProxyType(dict(sorted(self.note_digests.items())))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparator_file_digests": dict(sorted(self.comparator_file_digests.items())),
            "comparators": [item.to_dict() for item in self.comparators],
            "note_digests": dict(sorted(self.note_digests.items())),
            "replay_pin": None if self.replay_pin is None else self.replay_pin.to_dict(),
            "schema_version": self.schema_version,
            "upstream": self.upstream.to_dict(),
        }

    def to_json(self) -> str:
        return format_canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: object) -> ProvenanceRecord:
        obj = _as_object(
            data,
            label="ProvenanceRecord",
            required_keys=frozenset(
                {
                    "schema_version",
                    "upstream",
                    "replay_pin",
                    "comparators",
                    "comparator_file_digests",
                    "note_digests",
                }
            ),
        )
        raw_comparators = obj["comparators"]
        if not isinstance(raw_comparators, (list, tuple)):
            raise ValueError("comparators must be a list")
        comp_items = cast(list[object] | tuple[object, ...], raw_comparators)
        replay_raw = obj["replay_pin"]
        replay = None if replay_raw is None else ReplayPin.from_dict(replay_raw)
        return cls(
            schema_version=_require_str(obj["schema_version"], field="schema_version"),
            upstream=UpstreamRepoPin.from_dict(obj["upstream"]),
            replay_pin=replay,
            comparators=tuple(ComparatorSummary.from_dict(item) for item in comp_items),
            comparator_file_digests=_require_str_mapping(
                obj["comparator_file_digests"], field="comparator_file_digests"
            ),
            note_digests=_require_str_mapping(obj["note_digests"], field="note_digests"),
        )


@dataclass(frozen=True, slots=True)
class VerificationStatus:
    """Self-contained verification status record emitted to audit/verification-status.json."""

    schema_version: str
    upstream_commit: str
    lean_toolchain: str
    mathlib_rev: str
    build_status: BuildStatus
    scan_scope: ScanScope
    files_scanned: tuple[str, ...]
    solution_forbidden_token_count: int
    challenge_sorry_count: int
    theorem_names: tuple[str, ...]
    permitted_axioms: tuple[str, ...]
    community_verification_notes_sha256: str
    upstream_static_scan_sha256: str
    scan: StaticScanSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_status": self.build_status,
            "challenge_sorry_count": self.challenge_sorry_count,
            "community_verification_notes_sha256": self.community_verification_notes_sha256,
            "files_scanned": list(self.files_scanned),
            "lean_toolchain": self.lean_toolchain,
            "mathlib_rev": self.mathlib_rev,
            "permitted_axioms": list(self.permitted_axioms),
            "scan": self.scan.to_dict(),
            "scan_scope": self.scan_scope,
            "schema_version": self.schema_version,
            "solution_forbidden_token_count": self.solution_forbidden_token_count,
            "theorem_names": list(self.theorem_names),
            "upstream_commit": self.upstream_commit,
            "upstream_static_scan_sha256": self.upstream_static_scan_sha256,
        }

    def to_json(self) -> str:
        return format_canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: object) -> VerificationStatus:
        obj = _as_object(
            data,
            label="VerificationStatus",
            required_keys=frozenset(
                {
                    "schema_version",
                    "upstream_commit",
                    "lean_toolchain",
                    "mathlib_rev",
                    "build_status",
                    "scan_scope",
                    "files_scanned",
                    "solution_forbidden_token_count",
                    "challenge_sorry_count",
                    "theorem_names",
                    "permitted_axioms",
                    "community_verification_notes_sha256",
                    "upstream_static_scan_sha256",
                    "scan",
                }
            ),
        )
        build_status = cast(
            BuildStatus,
            _require_literal(
                obj["build_status"], field="build_status", allowed=_VALID_BUILD_STATUSES
            ),
        )
        scope = cast(
            ScanScope,
            _require_literal(obj["scan_scope"], field="scan_scope", allowed=_VALID_SCOPES),
        )
        return cls(
            schema_version=_require_str(obj["schema_version"], field="schema_version"),
            upstream_commit=_require_str(obj["upstream_commit"], field="upstream_commit"),
            lean_toolchain=_require_str(obj["lean_toolchain"], field="lean_toolchain"),
            mathlib_rev=_require_str(obj["mathlib_rev"], field="mathlib_rev"),
            build_status=build_status,
            scan_scope=scope,
            files_scanned=_require_str_tuple(obj["files_scanned"], field="files_scanned"),
            solution_forbidden_token_count=_require_int(
                obj["solution_forbidden_token_count"],
                field="solution_forbidden_token_count",
                minimum=0,
            ),
            challenge_sorry_count=_require_int(
                obj["challenge_sorry_count"], field="challenge_sorry_count", minimum=0
            ),
            theorem_names=_require_str_tuple(obj["theorem_names"], field="theorem_names"),
            permitted_axioms=_require_str_tuple(obj["permitted_axioms"], field="permitted_axioms"),
            community_verification_notes_sha256=_require_str(
                obj["community_verification_notes_sha256"],
                field="community_verification_notes_sha256",
            ),
            upstream_static_scan_sha256=_require_str(
                obj["upstream_static_scan_sha256"], field="upstream_static_scan_sha256"
            ),
            scan=StaticScanSummary.from_dict(obj["scan"]),
        )
