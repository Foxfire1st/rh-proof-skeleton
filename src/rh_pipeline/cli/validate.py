"""Thin CLI entry point for deterministic schema, concept, and jargon validation (rh-validate)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from rh_pipeline.models.validation import ValidationFinding, sort_findings
from rh_pipeline.validate.concepts import validate_concept_corpus
from rh_pipeline.validate.jargon import validate_explainer_docs
from rh_pipeline.validate.schemas import validate_repo_artifacts


def _build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser for rh-validate."""
    parser = argparse.ArgumentParser(
        prog="rh-validate",
        description=(
            "Validate repository JSON schemas, Concept Cards, Lean anchors, "
            "and Level-1 jargon order."
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
        default=None,
        type=Path,
        help="Optional path to the paired memory repository root containing docs/.",
    )
    return parser


def _run_memory_checks(repo_root: Path, memory_root: Path | None) -> tuple[ValidationFinding, ...]:
    if memory_root is None:
        print("INFO: memory checks skipped (no --memory path)")
        return ()
    if not memory_root.is_dir():
        return (
            ValidationFinding(
                "cli-missing-memory",
                str(memory_root),
                0,
                "memory directory does not exist",
            ),
        )
    docs_root = memory_root / "docs"
    concepts_dir = docs_root / "concepts"
    has_cards = concepts_dir.is_dir() and any(path.is_file() for path in concepts_dir.glob("*.md"))
    if not has_cards:
        print(f"INFO: no concept cards found under {concepts_dir} (concept corpus checks skipped)")
        return validate_explainer_docs(docs_root, registry={})
    registry, concept_findings = validate_concept_corpus(
        docs_root, skeleton_root=repo_root / "skeleton"
    )
    if any(f.rule == "concept-term-collision" for f in concept_findings):
        return concept_findings
    jargon_findings = validate_explainer_docs(docs_root, registry=registry)
    return (*concept_findings, *jargon_findings)


def main(argv: list[str] | None = None) -> int:
    """Run deterministic repository and optional memory validation checks."""
    args = _build_parser().parse_args(argv)
    repo_root = cast(Path, args.repo)
    memory_root = cast(Path | None, args.memory)

    if not repo_root.is_dir():
        finding = ValidationFinding(
            "cli-missing-repo", str(repo_root), 0, "repository directory does not exist"
        )
        print(finding.format())
        return 1

    schema_findings = validate_repo_artifacts(repo_root)
    memory_findings = _run_memory_checks(repo_root, memory_root)
    findings = sort_findings((*schema_findings, *memory_findings))

    if findings:
        for finding in findings:
            print(finding.format())
        return 1

    print("OK: rh-validate passed (0 findings).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
