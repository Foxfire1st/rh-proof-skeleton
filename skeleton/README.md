# Skeleton Directory

This directory holds generated, deterministically formatted `.lean` signature stubs grouped by proof stage (`00_statement/` … `20_grh/`).

- Generated exclusively by `rh_pipeline.lean` and `rh_pipeline.graph`.
- Proof bodies are stripped to provenance-preserving stubs pointing back to exact upstream source commits and line ranges; compilation/verification truth lives in `audit/verification-status.json`.
