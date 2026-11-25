"""Immutable stage-map and proof-DAG domain models with strict serialization."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any, cast

from rh_pipeline.models.lean import VALID_DECLARATION_KINDS, LeanDeclarationKind

STAGE_MAP_SCHEMA_VERSION = "rh-stage-map/v1"
DAG_SCHEMA_VERSION = "rh-dag/v1"


def format_canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize a mapping to deterministic sorted-key JSON with a trailing newline."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _as_obj(data: object, cls: type[Any]) -> Mapping[str, object]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{cls.__name__} must be a JSON object")
    raw = cast(Mapping[object, object], data)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{cls.__name__} keys must be strings")
    str_data = cast(Mapping[str, object], raw)
    required = frozenset(field.name for field in fields(cls))
    actual = frozenset(str_data.keys())
    missing, extra = sorted(required - actual), sorted(actual - required)
    if missing or extra:
        raise ValueError(f"{cls.__name__} key mismatch (missing={missing}, extra={extra})")
    return str_data


def _construct[T](cls: type[T], values: Mapping[str, object]) -> T:
    return cls(**cast(dict[str, Any], values))


def _req_str(value: object, field: str, *, exact: str | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if exact is not None and value != exact:
        raise ValueError(f"{field} must equal {exact!r}, got {value!r}")
    return value


def _req_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _req_int(value: object, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def _req_kind(value: object, field: str = "kind") -> LeanDeclarationKind:
    text = _req_str(value, field)
    if text not in VALID_DECLARATION_KINDS:
        raise ValueError(f"{field} must be one of {sorted(VALID_DECLARATION_KINDS)}, got {text!r}")
    return cast(LeanDeclarationKind, text)


def _req_path(value: object, field: str, *, skeleton: bool = False) -> str:
    path = _req_str(value, field)
    if path.startswith("/") or "\\" in path:
        raise ValueError(f"{field} must be a relative POSIX path, got {path!r}")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"{field} contains invalid path segments: {path!r}")
    if skeleton and path.startswith("skeleton/"):
        raise ValueError(f"{field} must not start with 'skeleton/': {path!r}")
    return path


def _req_items[T](value: object, field: str, parser: Callable[[object], T]) -> tuple[T, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    items = cast(list[object] | tuple[object, ...], value)
    return tuple(parser(item) for item in items)


def _req_tuple[T](value: object, field: str, item_type: type[T]) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple of {item_type.__name__}")
    items = cast(tuple[object, ...], value)
    if not all(isinstance(item, item_type) for item in items):
        raise ValueError(f"{field} elements must be {item_type.__name__}")


@dataclass(frozen=True, slots=True)
class StageFileMapping:
    """Mapping from one upstream Lean file to its generated skeleton file."""

    upstream_path: str
    skeleton_file: str
    preserve_defs: bool

    def __post_init__(self) -> None:
        _req_path(self.upstream_path, "upstream_path")
        _req_path(self.skeleton_file, "skeleton_file", skeleton=True)
        _req_bool(self.preserve_defs, "preserve_defs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "preserve_defs": self.preserve_defs,
            "skeleton_file": self.skeleton_file,
            "upstream_path": self.upstream_path,
        }

    @classmethod
    def from_dict(cls, data: object) -> StageFileMapping:
        return _construct(cls, _as_obj(data, cls))


@dataclass(frozen=True, slots=True)
class StageDefinition:
    """One ordered stage grouping related upstream and skeleton Lean files."""

    stage_id: str
    order: int
    title: str
    summary: str
    files: tuple[StageFileMapping, ...]

    def __post_init__(self) -> None:
        _req_str(self.stage_id, "stage_id")
        _req_int(self.order, "order", 0)
        _req_str(self.title, "title")
        _req_str(self.summary, "summary")
        _req_tuple(self.files, "files", StageFileMapping)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [item.to_dict() for item in self.files],
            "order": self.order,
            "stage_id": self.stage_id,
            "summary": self.summary,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, data: object) -> StageDefinition:
        obj = _as_obj(data, cls)
        files = _req_items(obj["files"], "files", StageFileMapping.from_dict)
        return _construct(cls, {**obj, "files": files})


@dataclass(frozen=True, slots=True)
class StageMap:
    """Authoritative stage-to-file mapping stored in dag/stage-map.json."""

    schema_version: str
    stages: tuple[StageDefinition, ...]

    def __post_init__(self) -> None:
        _req_str(self.schema_version, "schema_version", exact=STAGE_MAP_SCHEMA_VERSION)
        _req_tuple(self.stages, "stages", StageDefinition)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "stages": [s.to_dict() for s in self.stages]}

    def to_json(self) -> str:
        return format_canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: object) -> StageMap:
        obj = _as_obj(data, cls)
        stages = _req_items(obj["stages"], "stages", StageDefinition.from_dict)
        return _construct(cls, {**obj, "stages": stages})


@dataclass(frozen=True, slots=True)
class DagDeclarationRef:
    """Line-anchored reference linking a skeleton declaration to its upstream source."""

    name: str
    kind: LeanDeclarationKind
    start_line: int
    end_line: int
    upstream_path: str
    upstream_start_line: int
    upstream_end_line: int

    def __post_init__(self) -> None:
        _req_str(self.name, "name")
        _req_kind(self.kind)
        _req_int(self.start_line, "start_line", 1)
        _req_int(self.end_line, "end_line", self.start_line)
        _req_path(self.upstream_path, "upstream_path")
        _req_int(self.upstream_start_line, "upstream_start_line", 1)
        _req_int(self.upstream_end_line, "upstream_end_line", self.upstream_start_line)

    def to_dict(self) -> dict[str, Any]:
        return {
            "end_line": self.end_line,
            "kind": self.kind,
            "name": self.name,
            "start_line": self.start_line,
            "upstream_end_line": self.upstream_end_line,
            "upstream_path": self.upstream_path,
            "upstream_start_line": self.upstream_start_line,
        }

    @classmethod
    def from_dict(cls, data: object) -> DagDeclarationRef:
        return _construct(cls, _as_obj(data, cls))


@dataclass(frozen=True, slots=True)
class DagNode:
    """One module-level node in the condensed proof dependency DAG."""

    id: str
    stage_id: str
    title: str
    skeleton_file: str
    upstream_path: str
    declarations: tuple[DagDeclarationRef, ...]
    depends_on: tuple[str, ...]
    is_landmark: bool

    def __post_init__(self) -> None:
        _req_str(self.id, "id")
        _req_str(self.stage_id, "stage_id")
        _req_str(self.title, "title")
        _req_path(self.skeleton_file, "skeleton_file", skeleton=True)
        _req_path(self.upstream_path, "upstream_path")
        _req_tuple(self.declarations, "declarations", DagDeclarationRef)
        _req_tuple(self.depends_on, "depends_on", str)
        for dep in self.depends_on:
            _req_str(dep, "depends_on")
        _req_bool(self.is_landmark, "is_landmark")

    def to_dict(self) -> dict[str, Any]:
        return {
            "declarations": [decl.to_dict() for decl in self.declarations],
            "depends_on": list(self.depends_on),
            "id": self.id,
            "is_landmark": self.is_landmark,
            "skeleton_file": self.skeleton_file,
            "stage_id": self.stage_id,
            "title": self.title,
            "upstream_path": self.upstream_path,
        }

    @classmethod
    def from_dict(cls, data: object) -> DagNode:
        obj = _as_obj(data, cls)
        declarations = _req_items(obj["declarations"], "declarations", DagDeclarationRef.from_dict)
        depends_on = _req_items(
            obj["depends_on"], "depends_on", lambda v: _req_str(v, "depends_on")
        )
        return _construct(cls, {**obj, "declarations": declarations, "depends_on": depends_on})


@dataclass(frozen=True, slots=True)
class DagGraph:
    """Generated proof dependency DAG emitted to dag/dag.json."""

    schema_version: str
    upstream_commit: str
    stages: tuple[StageDefinition, ...]
    nodes: tuple[DagNode, ...]

    def __post_init__(self) -> None:
        _req_str(self.schema_version, "schema_version", exact=DAG_SCHEMA_VERSION)
        _req_str(self.upstream_commit, "upstream_commit")
        _req_tuple(self.stages, "stages", StageDefinition)
        _req_tuple(self.nodes, "nodes", DagNode)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "schema_version": self.schema_version,
            "stages": [stage.to_dict() for stage in self.stages],
            "upstream_commit": self.upstream_commit,
        }

    def to_json(self) -> str:
        return format_canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: object) -> DagGraph:
        obj = _as_obj(data, cls)
        stages = _req_items(obj["stages"], "stages", StageDefinition.from_dict)
        nodes = _req_items(obj["nodes"], "nodes", DagNode.from_dict)
        return _construct(cls, {**obj, "stages": stages, "nodes": nodes})
