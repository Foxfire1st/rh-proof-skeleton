"""Thin CLI entry point for deterministic Phase 0 upstream auditing (rh-audit)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from rh_pipeline.audit.provenance import AuditInputError, collect_provenance
from rh_pipeline.audit.static_scan import AuditScanError, scan_upstream_repo
from rh_pipeline.audit.status import build_verification_status, write_audit_artifacts
from rh_pipeline.models.audit import ScanScope


def _build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser for rh-audit."""
    parser = argparse.ArgumentParser(
        prog="rh-audit",
        description="Emit deterministic Phase 0 upstream provenance and verification status JSON.",
    )
    parser.add_argument(
        "--upstream",
        required=True,
        type=Path,
        help="Path to the read-only upstream riemann-hypothesis checkout.",
    )
    parser.add_argument(
        "--out",
        default=Path("audit"),
        type=Path,
        help="Output directory for provenance.json and verification-status.json (default: audit).",
    )
    parser.add_argument(
        "--scope",
        choices=("entry", "full"),
        default="entry",
        help="Static scan scope: 'entry' (default) or 'full'.",
    )
    parser.add_argument(
        "--replay",
        default=None,
        type=Path,
        help="Optional path to the read-only alpoge-rh-replay metadata checkout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Phase 0 audit pipeline and emit provenance and verification-status JSON."""
    args = _build_parser().parse_args(argv)
    upstream_root = cast(Path, args.upstream)
    out_dir = cast(Path, args.out)
    scope = cast(ScanScope, args.scope)
    replay_root = cast(Path | None, args.replay)

    try:
        provenance = collect_provenance(upstream_root, replay_root=replay_root)
        scan = scan_upstream_repo(upstream_root, scope=scope)
        status = build_verification_status(provenance, scan)
    except (AuditInputError, AuditScanError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    write_audit_artifacts(out_dir, provenance, status)
    print(
        f"Audited {status.upstream_commit[:12]} (scope={status.scan_scope}, "
        f"files={len(status.files_scanned)}, "
        f"solution_forbidden={status.solution_forbidden_token_count}, "
        f"challenge_sorry={status.challenge_sorry_count}) -> {out_dir}"
    )
    return 1 if status.solution_forbidden_token_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
