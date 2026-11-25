"""Thin CLI entry point for deterministic VitePress site compilation (rh-build-site)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from rh_pipeline.site import (
    SiteBuildConfig,
    SiteBuildError,
    SiteDirectiveError,
    compile_site,
)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser for rh-build-site."""
    parser = argparse.ArgumentParser(
        prog="rh-build-site",
        description=(
            "Compile validated Concept Cards, stage explainers, and DAG data for VitePress."
        ),
    )
    parser.add_argument(
        "--repo",
        required=True,
        type=Path,
        help="Path to the rh-proof-skeleton code repository root.",
    )
    parser.add_argument(
        "--memory",
        required=True,
        type=Path,
        help="Path to the paired memory repository root containing docs/.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output VitePress docs directory (e.g. site/docs).",
    )
    parser.add_argument(
        "--base-url",
        default="/ar-rh-proof-skeleton/",
        type=str,
        help="Deployed site base URL path (default: /ar-rh-proof-skeleton/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Compile the VitePress docs tree, failing closed with exit code 1 on any error."""
    args = _build_parser().parse_args(argv)
    config = SiteBuildConfig(
        repo_root=cast(Path, args.repo),
        memory_root=cast(Path, args.memory),
        out_docs_dir=cast(Path, args.out),
        base_url=cast(str, args.base_url),
    )
    try:
        report = compile_site(config)
    except (SiteBuildError, SiteDirectiveError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"Built site -> {config.out_docs_dir} "
        f"(stages={report.stages_built}, concepts={report.concepts_built}, "
        f"assets={report.assets_copied})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
