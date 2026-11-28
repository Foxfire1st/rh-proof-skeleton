# Audit Directory

Machine-generated provenance and verification artifacts:

- `provenance.json`: pinned upstream repository commits, archive hashes, Lean toolchain version, and Mathlib commit.
- `verification-status.json`: Phase 0 lexical static-scan counts (`scan_scope: entry`) and local build status (`build_status: unverified-locally`) rendered into the site's global verification banner (replay provenance is recorded in `provenance.json`, with no local axiom-audit or comparator-execution claim).
