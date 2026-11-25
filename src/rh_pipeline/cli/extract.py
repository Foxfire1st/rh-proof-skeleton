"""Thin CLI entry point for deterministic Lean skeleton and DAG extraction (rh-extract)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from rh_pipeline.graph.stage_map import StageMapError, extract_and_write
from rh_pipeline.lean.parser import UnsupportedLeanFormError


def _build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser for rh-extract."""
    parser = argparse.ArgumentParser(
        prog="rh-extract",
        description="Extract deterministic Lean skeleton stubs and dag/dag.json from upstream.",
    )
    parser.add_argument(
        "--upstream",
        required=True,
        type=Path,
        help="Path to the read-only upstream riemann-hypothesis checkout.",
    )
    parser.add_argument(
        "--stage-map",
        default=Path("dag/stage-map.json"),
        type=Path,
        help="Path to the authoritative stage-map JSON (default: dag/stage-map.json).",
    )
    parser.add_argument(
        "--out-skeleton",
        default=Path("skeleton"),
        type=Path,
        help="Output root directory for generated skeleton .lean stubs (default: skeleton).",
    )
    parser.add_argument(
        "--out-dag",
        default=Path("dag/dag.json"),
        type=Path,
        help="Output path for generated DAG JSON (default: dag/dag.json).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run deterministic skeleton extraction and emit skeleton stubs plus dag.json."""
    args = _build_parser().parse_args(argv)
    upstream_root = cast(Path, args.upstream)
    stage_map_path = cast(Path, args.stage_map)
    out_skeleton = cast(Path, args.out_skeleton)
    out_dag = cast(Path, args.out_dag)

    try:
        dag = extract_and_write(
            upstream_root=upstream_root,
            stage_map_path=stage_map_path,
            out_skeleton_dir=out_skeleton,
            out_dag_path=out_dag,
        )
    except (StageMapError, UnsupportedLeanFormError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"Extracted {len(dag.nodes)} node(s) across {len(dag.stages)} stage(s) "
        f"at {dag.upstream_commit[:12]} -> {out_skeleton}, {out_dag}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
