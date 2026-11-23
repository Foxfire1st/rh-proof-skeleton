# rh-proof-skeleton

> **Verification & Epistemic Status:** Machine-generated provenance and verification facts will live in `audit/verification-status.json`. No mathematical claim in this explainer stands without an anchored Lean declaration or cited canonical mathematical source.

A deterministic Lean 4 proof-skeleton extractor, validator suite, and static **VitePress** explainer pipeline for Levent Alpöge & Claude's 2026 Lean 4 proof of the Riemann Hypothesis and Generalized Riemann Hypothesis (GRH).

## Upstream Sources

- **Community Lean 4 Proof Repository:** [`leanprover-community/riemann-hypothesis`](https://github.com/leanprover-community/riemann-hypothesis)
- **Independent Replay Archive:** [`rohanarun/alpoge-rh-replay`](https://github.com/rohanarun/alpoge-rh-replay)
- **Paired Agents Remember Memory Repository:** [`Foxfire1st/ar-rh-proof-skeleton`](https://github.com/Foxfire1st/ar-rh-proof-skeleton)

## Repository Layout & Code Ownership

Every directory and Python package owns one bounded responsibility:

| Path | Owner / Responsibility |
|---|---|
| `src/rh_pipeline/models/` | Immutable data models & JSON schemas (provenance, verification status, DAG nodes, concept cards) |
| `src/rh_pipeline/audit/` | Phase 0: upstream pinning, static scans, replay/comparator receipt ingestion, `audit/verification-status.json` |
| `src/rh_pipeline/lean/` | Phase 1A: `.lean` declaration parsing, `.olean`/Lean environment adapter, reachability pruning, provenance stub emission |
| `src/rh_pipeline/graph/` | Phase 1B: DAG condensation, landmark selection, deterministic `dag/stage-map.json` owner |
| `src/rh_pipeline/validate/` | Deterministic quality gates (file-size rail, schema checks, Lean anchor resolution, concept-slug & citation checks, jargon-order lint) |
| `src/rh_pipeline/site/` | Phase 4: static compiler reading `ar-rh-proof-skeleton` + `dag/` + `audit/` into VitePress docs |
| `src/rh_pipeline/cli/` | Thin command wiring only (zero business logic) |
| `skeleton/` | Generated deterministic `.lean` signature stubs grouped by proof stage (`00_statement/` … `20_grh/`) |
| `dag/` | Authoritative `stage-map.json` + generated `dag.json` |
| `audit/` | Machine-generated `provenance.json` + `verification-status.json` |
| `site/` | VitePress static site shell, design tokens, and interactive concept/zoom/DAG components |
| `tests/` | Focused unit tests and synthetic Lean 4 / concept fixtures |

## Quality Gates

- Python formatting & linting: `ruff check .` and `ruff format --check .`
- Strict static typing: `pyright`
- Complexity visibility: `radon cc src -s -a -n B`
- File-size rail (300 healthy / 600 watchful / 900 soft / 1200 hard): `python -m rh_pipeline.validate.file_size`
- Unit tests: `pytest`
