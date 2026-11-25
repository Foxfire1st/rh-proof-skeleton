"""Shared immutable validation-finding model and deterministic sorting helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


def _require_nonempty_str(value: object, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")


def _require_nonnegative_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """One deterministic validation diagnostic formatted as rule:path:line:message."""

    rule: str
    path: str
    line: int
    message: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.rule, "rule")
        _require_nonempty_str(self.path, "path")
        _require_nonnegative_int(self.line, "line")
        _require_nonempty_str(self.message, "message")

    def format(self) -> str:
        """Render the canonical diagnostic line `rule:path:line:message`."""
        return f"{self.rule}:{self.path}:{self.line}:{self.message}"

    def sort_key(self) -> tuple[str, int, str, str]:
        """Return the canonical sort key `(path, line, rule, message)`."""
        return (self.path, self.line, self.rule, self.message)


def sort_findings(findings: Iterable[ValidationFinding]) -> tuple[ValidationFinding, ...]:
    """Return findings sorted deterministically by `(path, line, rule, message)`."""
    return tuple(sorted(findings, key=lambda item: item.sort_key()))
