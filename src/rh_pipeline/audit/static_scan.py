"""Deterministic Lean 4 lexical static scanner for Phase 0 upstream audits."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rh_pipeline.audit.provenance import AuditInputError
from rh_pipeline.models.audit import ScanRole, ScanScope, StaticScanFinding, StaticScanSummary

__all__ = [
    "ATTRIBUTE_TOKENS",
    "CHALLENGE_FILES",
    "ENTRY_SCAN_FILES",
    "FORBIDDEN_TOKENS",
    "AuditScanError",
    "scan_lean_source",
    "scan_upstream_repo",
]

ENTRY_SCAN_FILES: tuple[str, ...] = (
    "comparator/FormalConjecturesBridge.lean",
    "comparator/FormalConjecturesChallenge.lean",
    "comparator/FormalConjecturesSolution.lean",
    "comparator/FormalConjecturesStatement.lean",
    "src/Challenge.lean",
    "src/Main.lean",
    "src/Statements.lean",
)
CHALLENGE_FILES: frozenset[str] = frozenset(
    {"Challenge.lean", "src/Challenge.lean", "comparator/FormalConjecturesChallenge.lean"}
)
_PRIMARY_CHALLENGE_CANDIDATES: tuple[str, ...] = ("Challenge.lean", "src/Challenge.lean")
_REQUIRED_ENTRY_FILES: tuple[str, ...] = ("src/Statements.lean", "src/Main.lean")
FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        "sorry",
        "admit",
        "axiom",
        "opaque",
        "unsafe",
        "skipKernelTC",
        "native_decide",
        "ofReduceBool",
        "trustCompiler",
    }
)
ATTRIBUTE_TOKENS: frozenset[str] = frozenset({"extern", "implemented_by"})
_VALID_SCOPES: frozenset[str] = frozenset({"entry", "full"})

# Lean identifiers allow Unicode letters and trailing primes (e.g. `hχ`, `hre'`),
# while `.` acts as a boundary so qualified names like `Lean.ofReduceBool` expose their leaf token.
_IDENT_RE = re.compile(r"[^\W\d][\w']*")

# Match only a well-formed single-line Lean char literal so identifier primes never desync lexing.
_CHAR_LITERAL_RE = re.compile(
    r"'(?:\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{[0-9a-fA-F]+\}|[^\n\r])|[^'\\\n\r])'"
)


class AuditScanError(ValueError):
    """Raised when a Lean source file cannot be lexed cleanly (e.g. unclosed block comment)."""


@dataclass(frozen=True, slots=True)
class _Token:
    kind: Literal["ident", "sym", "lit"]
    text: str
    line: int


def _finding_sort_key(finding: StaticScanFinding) -> tuple[str, int, str]:
    return (finding.rel_path, finding.line, finding.token)


def _skip_line_comment(source: str, idx: int, line: int) -> tuple[int, int]:
    newline_idx = source.find("\n", idx + 2)
    return (len(source), line) if newline_idx == -1 else (newline_idx + 1, line + 1)


def _skip_block_comment(source: str, idx: int, line: int, rel_path: str) -> tuple[int, int]:
    open_lines = [line]
    pos = idx + 2
    length = len(source)
    while pos < length and open_lines:
        if source.startswith("/-", pos):
            open_lines.append(line)
            pos += 2
        elif source.startswith("-/", pos):
            open_lines.pop()
            pos += 2
        else:
            line += int(source[pos] == "\n")
            pos += 1
    if open_lines:
        raise AuditScanError(f"Unclosed block comment at {rel_path}:{open_lines[-1]}")
    return pos, line


def _try_skip_raw_string(source: str, idx: int, line: int) -> tuple[int, int] | None:
    pos = idx + 1
    length = len(source)
    while pos < length and source[pos] == "#":
        pos += 1
    hashes = pos - (idx + 1)
    if hashes == 0 or pos >= length or source[pos] != '"':
        return None
    closing = '"' + "#" * hashes
    end_idx = source.find(closing, pos + 1)
    if end_idx == -1:
        return length, line + source.count("\n", pos + 1)
    close_end = end_idx + len(closing)
    return close_end, line + source.count("\n", pos + 1, close_end)


def _skip_string(source: str, idx: int, line: int) -> tuple[int, int]:
    pos = idx + 1
    length = len(source)
    while pos < length:
        ch = source[pos]
        if ch == "\\" and pos + 1 < length:
            line += int(source[pos + 1] == "\n")
            pos += 2
        elif ch == '"':
            return pos + 1, line
        else:
            line += int(ch == "\n")
            pos += 1
    return length, line


def _try_skip_char_literal(source: str, idx: int) -> int | None:
    match = _CHAR_LITERAL_RE.match(source, idx)
    return None if match is None else match.end()


def _try_skip_comment_or_literal(
    source: str, idx: int, line: int, rel_path: str
) -> tuple[int, int, _Token | None] | None:
    if source.startswith("--", idx):
        return (*_skip_line_comment(source, idx, line), None)
    if source.startswith("/-", idx):
        return (*_skip_block_comment(source, idx, line, rel_path), None)
    ch = source[idx]
    if ch == '"':
        return (*_skip_string(source, idx, line), _Token("lit", "", line))
    if ch == "r" and (raw := _try_skip_raw_string(source, idx, line)) is not None:
        return (raw[0], raw[1], _Token("lit", "", line))
    if ch == "'" and (char_end := _try_skip_char_literal(source, idx)) is not None:
        return (char_end, line, _Token("lit", "", line))
    return None


def _tokenize_lean(source: str, rel_path: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos, line, length = 0, 1, len(source)
    while pos < length:
        ch = source[pos]
        if ch.isspace():
            line += int(ch == "\n")
            pos += 1
            continue
        skipped = _try_skip_comment_or_literal(source, pos, line, rel_path)
        if skipped is not None:
            pos, line, tok = skipped
            if tok is not None:
                tokens.append(tok)
            continue
        ident = _IDENT_RE.match(source, pos)
        if ident is not None:
            tokens.append(_Token("ident", ident.group(0), line))
            pos = ident.end()
            continue
        tokens.append(_Token("sym", ch, line))
        pos += 1
    return tokens


def _is_attr_block_start(tokens: list[_Token], idx: int) -> bool:
    if idx + 1 >= len(tokens):
        return False
    nxt = tokens[idx + 1]
    if (nxt.kind, nxt.text) != ("sym", "["):
        return False
    cur = tokens[idx]
    if (cur.kind, cur.text) == ("sym", "@"):
        return True
    if (cur.kind, cur.text) != ("ident", "attribute"):
        return False
    return idx == 0 or (tokens[idx - 1].kind, tokens[idx - 1].text) != ("sym", ".")


def _update_attr_depth(attr_depth: int, tok: _Token) -> int:
    if tok.kind != "sym":
        return attr_depth
    if tok.text == "[":
        return attr_depth + 1
    if tok.text == "]":
        return attr_depth - 1
    return attr_depth


def _is_flagged_ident(text: str, attr_depth: int) -> bool:
    return text in FORBIDDEN_TOKENS or (attr_depth == 1 and text in ATTRIBUTE_TOKENS)


def _collect_findings(
    tokens: list[_Token], *, rel_path: str, role: ScanRole
) -> tuple[StaticScanFinding, ...]:
    findings: list[StaticScanFinding] = []
    attr_depth, idx, total = 0, 0, len(tokens)
    while idx < total:
        tok = tokens[idx]
        if attr_depth == 0 and _is_attr_block_start(tokens, idx):
            attr_depth = 1
            idx += 2
            continue
        if attr_depth > 0:
            attr_depth = _update_attr_depth(attr_depth, tok)
        if tok.kind == "ident" and _is_flagged_ident(tok.text, attr_depth):
            findings.append(
                StaticScanFinding(rel_path=rel_path, line=tok.line, token=tok.text, role=role)
            )
        idx += 1
    return tuple(sorted(findings, key=_finding_sort_key))


def scan_lean_source(
    source: str, *, rel_path: str, role: ScanRole
) -> tuple[StaticScanFinding, ...]:
    """Lex a Lean 4 source string and return deterministic forbidden/attribute token findings."""
    return _collect_findings(_tokenize_lean(source, rel_path), rel_path=rel_path, role=role)


def _resolve_primary_challenge_file(upstream_root: Path) -> str:
    present = [
        rel_path
        for rel_path in _PRIMARY_CHALLENGE_CANDIDATES
        if (upstream_root / rel_path).is_file()
    ]
    if not present:
        raise AuditInputError(
            "Required challenge Lean file missing: expected Challenge.lean or src/Challenge.lean"
        )
    if len(present) > 1:
        raise AuditInputError(
            "Ambiguous challenge Lean files: both Challenge.lean and src/Challenge.lean exist"
        )
    return present[0]


def _collect_optional_comparator_files(
    upstream_root: Path, *, pattern: str, recursive: bool
) -> list[str]:
    comp_dir = upstream_root / "comparator"
    if comp_dir.is_dir():
        matches = comp_dir.rglob(pattern) if recursive else comp_dir.glob(pattern)
        return [p.relative_to(upstream_root).as_posix() for p in matches if p.is_file()]
    if comp_dir.exists() or comp_dir.is_symlink():
        raise AuditInputError(f"Upstream comparator path is not a directory: {comp_dir}")
    return []


def _resolve_entry_files(upstream_root: Path) -> tuple[str, ...]:
    targets: set[str] = set()
    for rel_path in _REQUIRED_ENTRY_FILES:
        if not (upstream_root / rel_path).is_file():
            raise AuditInputError(f"Required entry Lean file missing: {rel_path}")
        targets.add(rel_path)
    targets.add(_resolve_primary_challenge_file(upstream_root))
    targets.update(
        _collect_optional_comparator_files(
            upstream_root, pattern="FormalConjectures*.lean", recursive=False
        )
    )
    return tuple(sorted(targets))


def _resolve_full_files(upstream_root: Path) -> tuple[str, ...]:
    src_dir = upstream_root / "src"
    if not src_dir.is_dir():
        raise AuditInputError("Required upstream directory missing: src")
    challenge_rel = _resolve_primary_challenge_file(upstream_root)
    rel_paths: set[str] = {
        p.relative_to(upstream_root).as_posix() for p in src_dir.rglob("*.lean") if p.is_file()
    }
    rel_paths.add(challenge_rel)
    rel_paths.update(
        _collect_optional_comparator_files(upstream_root, pattern="*.lean", recursive=True)
    )
    return tuple(sorted(rel_paths))


def _resolve_scan_targets(upstream_root: Path, scope: ScanScope) -> tuple[str, ...]:
    if not upstream_root.is_dir():
        raise AuditInputError(f"Upstream root directory not found: {upstream_root}")
    if scope not in _VALID_SCOPES:
        raise AuditInputError(f"Invalid scan scope: {scope!r}")
    if scope == "entry":
        return _resolve_entry_files(upstream_root)
    return _resolve_full_files(upstream_root)


def scan_upstream_repo(upstream_root: Path, *, scope: ScanScope = "entry") -> StaticScanSummary:
    """Scan upstream Lean files in entry or full scope and partition challenge vs solution hits."""
    files_scanned = _resolve_scan_targets(upstream_root, scope)
    solution_findings: list[StaticScanFinding] = []
    challenge_findings: list[StaticScanFinding] = []
    for rel_path in files_scanned:
        source = (upstream_root / rel_path).read_text(encoding="utf-8")
        role: ScanRole = "challenge" if rel_path in CHALLENGE_FILES else "solution"
        for finding in scan_lean_source(source, rel_path=rel_path, role=role):
            if finding.role == "challenge" and finding.token == "sorry":
                challenge_findings.append(finding)
            else:
                solution_findings.append(finding)
    sorted_solution = tuple(sorted(solution_findings, key=_finding_sort_key))
    sorted_challenge = tuple(sorted(challenge_findings, key=_finding_sort_key))
    return StaticScanSummary(
        scan_scope=scope,
        files_scanned=files_scanned,
        solution_findings=sorted_solution,
        challenge_findings=sorted_challenge,
        challenge_sorry_count=len(sorted_challenge),
    )
