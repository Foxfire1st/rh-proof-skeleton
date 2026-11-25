"""Deterministic file-size rail for Python source and test trees.

Enforces the repository file-size budget:
- 0..300 lines: healthy default
- 301..600 lines: watchful
- 601..900 lines: refactor pressure
- 901..1200 lines: soft limit exceeded
- >1200 lines: hard limit failure
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HARD_LIMIT = 1200
DEFAULT_SOFT_LIMIT = 900
DEFAULT_WATCHFUL_LIMIT = 600
DEFAULT_HEALTHY_LIMIT = 300
DEFAULT_ROOTS = ("src", "tests")


@dataclass(frozen=True, slots=True)
class FileSizeFinding:
    """Line-count classification for one measured Python file."""

    path: Path
    lines: int
    band: str

    @property
    def exceeds_hard_limit(self) -> bool:
        return self.band == "hard-limit-exceeded"


def classify_line_count(
    lines: int,
    *,
    watchful_limit: int = DEFAULT_WATCHFUL_LIMIT,
    soft_limit: int = DEFAULT_SOFT_LIMIT,
    hard_limit: int = DEFAULT_HARD_LIMIT,
) -> str:
    """Classify a file's line count into its budget band."""
    if lines > hard_limit:
        return "hard-limit-exceeded"
    if lines > soft_limit:
        return "soft-limit-exceeded"
    if lines > watchful_limit:
        return "refactor-pressure"
    if lines > DEFAULT_HEALTHY_LIMIT:
        return "watchful"
    return "healthy"


def measure_tree(
    roots: tuple[Path, ...],
    *,
    watchful_limit: int = DEFAULT_WATCHFUL_LIMIT,
    soft_limit: int = DEFAULT_SOFT_LIMIT,
    hard_limit: int = DEFAULT_HARD_LIMIT,
) -> tuple[FileSizeFinding, ...]:
    """Measure all Python files under the given roots in deterministic sorted order."""
    findings: list[FileSizeFinding] = []
    for root in roots:
        if not root.exists():
            continue
        for file_path in sorted(root.rglob("*.py")):
            if not file_path.is_file():
                continue
            line_count = len(file_path.read_text(encoding="utf-8").splitlines())
            band = classify_line_count(
                line_count,
                watchful_limit=watchful_limit,
                soft_limit=soft_limit,
                hard_limit=hard_limit,
            )
            findings.append(FileSizeFinding(path=file_path, lines=line_count, band=band))
    return tuple(findings)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point returning non-zero when any measured file exceeds the hard limit."""
    parser = argparse.ArgumentParser(description="Check Python file-size budget rails.")
    parser.add_argument(
        "roots",
        nargs="*",
        default=list(DEFAULT_ROOTS),
        help="Directories to scan for Python files (default: src tests).",
    )
    parser.add_argument(
        "--hard-limit",
        type=int,
        default=DEFAULT_HARD_LIMIT,
        help=f"Maximum allowed lines per file (default: {DEFAULT_HARD_LIMIT}).",
    )
    args = parser.parse_args(argv)
    findings = measure_tree(
        tuple(Path(root) for root in args.roots),
        hard_limit=args.hard_limit,
    )
    violations = [item for item in findings if item.exceeds_hard_limit]
    for item in findings:
        if item.band != "healthy":
            print(f"{item.band}: {item.path} ({item.lines} lines)")
    if violations:
        print(
            f"FAILED: {len(violations)} file(s) exceeded hard limit of {args.hard_limit} lines.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(findings)} Python file(s) checked; 0 exceeded {args.hard_limit} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
