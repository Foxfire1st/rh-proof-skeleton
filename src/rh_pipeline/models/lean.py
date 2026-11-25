"""Immutable Lean declaration and extracted-module domain models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

LeanDeclarationKind = Literal[
    "theorem",
    "lemma",
    "def",
    "abbrev",
    "structure",
    "inductive",
    "class",
    "instance",
    "opaque",
    "axiom",
]

VALID_DECLARATION_KINDS: frozenset[str] = frozenset(
    {
        "theorem",
        "lemma",
        "def",
        "abbrev",
        "structure",
        "inductive",
        "class",
        "instance",
        "opaque",
        "axiom",
    }
)

_DECL_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "kind",
        "signature",
        "body",
        "start_line",
        "end_line",
        "upstream_path",
        "upstream_commit",
        "body_lines",
        "body_sha256",
    }
)
_MODULE_KEYS: frozenset[str] = frozenset(
    {"upstream_path", "upstream_commit", "imports", "declarations"}
)


def _as_object(data: object, *, label: str, required_keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    raw = cast(Mapping[object, object], data)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{label} keys must be strings")
    str_data = cast(Mapping[str, object], raw)
    actual = frozenset(str_data.keys())
    missing = sorted(required_keys - actual)
    extra = sorted(actual - required_keys)
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


def _require_kind(value: object, *, field: str = "kind") -> LeanDeclarationKind:
    text = _require_str(value, field=field)
    if text not in VALID_DECLARATION_KINDS:
        raise ValueError(f"{field} must be one of {sorted(VALID_DECLARATION_KINDS)}, got {text!r}")
    return cast(LeanDeclarationKind, text)


def _require_str_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of strings")
    items = cast(list[object] | tuple[object, ...], value)
    return tuple(_require_str(item, field=f"{field}[{idx}]") for idx, item in enumerate(items))


def _require_tuple_of[T](value: object, *, field: str, item_type: type[T]) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple of {item_type.__name__}")
    items = cast(tuple[object, ...], value)
    if not all(isinstance(item, item_type) for item in items):
        raise ValueError(f"{field} elements must be {item_type.__name__}")


@dataclass(frozen=True, slots=True)
class LeanDeclaration:
    """One extracted top-level Lean declaration with upstream provenance metadata."""

    name: str
    kind: LeanDeclarationKind
    signature: str
    body: str
    start_line: int
    end_line: int
    upstream_path: str
    upstream_commit: str
    body_lines: int
    body_sha256: str

    def __post_init__(self) -> None:
        _require_str(self.name, field="name")
        _require_kind(self.kind, field="kind")
        _require_str(self.signature, field="signature")
        _require_str(self.body, field="body", allow_empty=True)
        _require_int(self.start_line, field="start_line", minimum=1)
        _require_int(self.end_line, field="end_line", minimum=self.start_line)
        _require_str(self.upstream_path, field="upstream_path")
        _require_str(self.upstream_commit, field="upstream_commit")
        min_lines = 0 if self.body == "" else 1
        lines = _require_int(self.body_lines, field="body_lines", minimum=min_lines)
        if self.body == "" and lines != 0:
            raise ValueError("body_lines must be 0 when body is empty")
        _require_str(self.body_sha256, field="body_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "body_lines": self.body_lines,
            "body_sha256": self.body_sha256,
            "end_line": self.end_line,
            "kind": self.kind,
            "name": self.name,
            "signature": self.signature,
            "start_line": self.start_line,
            "upstream_commit": self.upstream_commit,
            "upstream_path": self.upstream_path,
        }

    @classmethod
    def from_dict(cls, data: object) -> LeanDeclaration:
        obj = _as_object(data, label="LeanDeclaration", required_keys=_DECL_KEYS)
        start_line = _require_int(obj["start_line"], field="start_line", minimum=1)
        body = _require_str(obj["body"], field="body", allow_empty=True)
        min_lines = 0 if body == "" else 1
        body_lines = _require_int(obj["body_lines"], field="body_lines", minimum=min_lines)
        return cls(
            name=_require_str(obj["name"], field="name"),
            kind=_require_kind(obj["kind"], field="kind"),
            signature=_require_str(obj["signature"], field="signature"),
            body=body,
            start_line=start_line,
            end_line=_require_int(obj["end_line"], field="end_line", minimum=start_line),
            upstream_path=_require_str(obj["upstream_path"], field="upstream_path"),
            upstream_commit=_require_str(obj["upstream_commit"], field="upstream_commit"),
            body_lines=body_lines,
            body_sha256=_require_str(obj["body_sha256"], field="body_sha256"),
        )


@dataclass(frozen=True, slots=True)
class ExtractedModule:
    """Extracted imports and declarations for one upstream Lean source module."""

    upstream_path: str
    upstream_commit: str
    imports: tuple[str, ...]
    declarations: tuple[LeanDeclaration, ...]

    def __post_init__(self) -> None:
        _require_str(self.upstream_path, field="upstream_path")
        _require_str(self.upstream_commit, field="upstream_commit")
        _require_tuple_of(self.imports, field="imports", item_type=str)
        for idx, imp in enumerate(self.imports):
            _require_str(imp, field=f"imports[{idx}]")
        _require_tuple_of(self.declarations, field="declarations", item_type=LeanDeclaration)

    def to_dict(self) -> dict[str, Any]:
        return {
            "declarations": [decl.to_dict() for decl in self.declarations],
            "imports": list(self.imports),
            "upstream_commit": self.upstream_commit,
            "upstream_path": self.upstream_path,
        }

    @classmethod
    def from_dict(cls, data: object) -> ExtractedModule:
        obj = _as_object(data, label="ExtractedModule", required_keys=_MODULE_KEYS)
        raw_decls = obj["declarations"]
        if not isinstance(raw_decls, (list, tuple)):
            raise ValueError("declarations must be a list")
        decl_items = cast(list[object] | tuple[object, ...], raw_decls)
        return cls(
            upstream_path=_require_str(obj["upstream_path"], field="upstream_path"),
            upstream_commit=_require_str(obj["upstream_commit"], field="upstream_commit"),
            imports=_require_str_tuple(obj["imports"], field="imports"),
            declarations=tuple(LeanDeclaration.from_dict(item) for item in decl_items),
        )
