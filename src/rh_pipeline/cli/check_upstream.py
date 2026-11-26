"""Thin CLI entry point for read-only upstream freshness triage (rh-check-upstream)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from rh_pipeline.audit.triage import (
    EXIT_INPUT_ERROR,
    ReplayTarget,
    TriageInputError,
    check_upstream,
    format_triage_report,
)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser for rh-check-upstream."""
    parser = argparse.ArgumentParser(
        prog="rh-check-upstream",
        description="Read-only freshness triage comparing an upstream Git ref to provenance.",
    )
    parser.add_argument(
        "--upstream",
        required=True,
        type=Path,
        help="Path to the local read-only riemann-hypothesis Git checkout.",
    )
    parser.add_argument(
        "--ref",
        default="HEAD",
        help="Target Git ref or commit to compare against audit/provenance.json (default: HEAD).",
    )
    parser.add_argument(
        "--provenance",
        default=Path("audit/provenance.json"),
        type=Path,
        help="Path to audit/provenance.json (default: audit/provenance.json).",
    )
    parser.add_argument(
        "--stage-map",
        default=Path("dag/stage-map.json"),
        type=Path,
        help="Path to dag/stage-map.json (default: dag/stage-map.json).",
    )
    parser.add_argument(
        "--replay",
        default=None,
        type=Path,
        help="Optional path to the local read-only replay metadata Git checkout.",
    )
    parser.add_argument(
        "--replay-ref",
        default="HEAD",
        help="Target Git ref or commit in --replay to inspect (default: HEAD).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic sorted-key JSON output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run read-only upstream freshness triage and return its classification exit code."""
    args = _build_parser().parse_args(argv)
    replay_root = cast(Path | None, args.replay)
    replay = None if replay_root is None else ReplayTarget(replay_root, cast(str, args.replay_ref))

    try:
        report = check_upstream(
            cast(Path, args.upstream),
            ref=cast(str, args.ref),
            provenance_path=cast(Path, args.provenance),
            stage_map_path=cast(Path, args.stage_map),
            replay=replay,
        )
    except TriageInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    output = report.to_json() if cast(bool, args.json) else format_triage_report(report)
    sys.stdout.write(output)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
