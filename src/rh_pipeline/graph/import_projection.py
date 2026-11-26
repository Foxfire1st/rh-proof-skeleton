"""Nearest-landmark Lean import projection across unmapped upstream modules."""

from __future__ import annotations

import unicodedata
from collections import deque
from collections.abc import Mapping
from pathlib import Path

from rh_pipeline.models.dag import StageDefinition
from rh_pipeline.models.lean import ExtractedModule

__all__ = [
    "project_nearest_landmarks",
    "scan_lean_header_imports",
]

_HEADER_ONLY_KEYWORDS: frozenset[str] = frozenset({"module", "prelude"})
_IMPORT_PREFIX_WORDS: frozenset[str] = frozenset({"public", "meta", "import"})
_CANDIDATE_PREFIXES: tuple[str, ...] = ("src/", "")


def _is_id_first(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_id_rest(ch: str) -> bool:
    return ch in "_'?!" or ch.isalnum() or unicodedata.category(ch) in {"No", "Mn", "Mc"}


def _find_block_comment_end(src: str, pos: int) -> int:
    nest, end = 1, len(src)
    while pos < end and nest > 0:
        pair = src[pos : pos + 2]
        if pair in {"/-", "-/"}:
            nest += 1 if pair == "/-" else -1
            pos += 2
        else:
            pos += 1
    return pos


def _skip_ws_and_comments(src: str, pos: int) -> int:
    end = len(src)
    while pos < end:
        ch = src[pos]
        if ch.isspace():
            pos += 1
        elif src[pos : pos + 2] == "--":
            nl = src.find("\n", pos + 2)
            pos = end if nl == -1 else nl + 1
        elif src[pos : pos + 2] == "/-":
            pos = _find_block_comment_end(src, pos + 2)
        else:
            break
    return pos


def _read_ident_part(src: str, pos: int) -> int:
    end = len(src)
    if src[pos] == "«":
        close = src.find("»", pos + 1)
        return end if close == -1 else close + 1
    pos += 1
    while pos < end and _is_id_rest(src[pos]):
        pos += 1
    return pos


def _read_dotted_ident(src: str, pos: int) -> tuple[str | None, int]:
    end = len(src)
    if pos >= end or (src[pos] != "«" and not _is_id_first(src[pos])):
        return None, pos
    cur = _read_ident_part(src, pos)
    while cur + 1 < end and src[cur] == ".":
        nxt = src[cur + 1]
        if nxt != "«" and not _is_id_first(nxt):
            break
        cur = _read_ident_part(src, cur + 1)
    return src[pos:cur], cur


def _next_token(src: str, pos: int) -> tuple[str | None, int, bool]:
    start = pos
    pos = _skip_ws_and_comments(src, pos)
    crossed_newline = "\n" in src[start:pos]
    word, end = _read_dotted_ident(src, pos)
    return word, end, crossed_newline


def _parse_import_target(src: str, first_word: str, pos: int) -> tuple[str | None, int]:
    word: str | None = first_word
    if word == "public":
        word, pos, _ = _next_token(src, pos)
    if word == "meta":
        word, pos, _ = _next_token(src, pos)
    if word != "import":
        return None, pos
    target, pos, _ = _next_token(src, pos)
    if target is None:
        return None, pos
    if target == "all":
        maybe_mod, next_pos, crossed_nl = _next_token(src, pos)
        if maybe_mod is not None and not crossed_nl:
            return maybe_mod, next_pos
    return target, pos


def scan_lean_header_imports(source: str) -> tuple[str, ...]:
    """Extract sorted unique module names imported in the leading Lean 4 file header."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    imports: set[str] = set()
    pos = 0
    while True:
        word, pos, _ = _next_token(text, pos)
        if word is None:
            break
        if word in _HEADER_ONLY_KEYWORDS:
            continue
        if word not in _IMPORT_PREFIX_WORDS:
            break
        target, pos = _parse_import_target(text, word, pos)
        if target is None:
            break
        imports.add(target)
    return tuple(sorted(imports))


def _resolve_unmapped_module(root: Path, module_name: str) -> Path | None:
    parts = module_name.split(".")
    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts):
        return None
    rel_lean = "/".join(parts) + ".lean"
    for prefix in _CANDIDATE_PREFIXES:
        candidate = (root / f"{prefix}{rel_lean}").resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    return None


def _load_unmapped_imports(
    root: Path,
    module_name: str,
    cache: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    cached = cache.get(module_name)
    if cached is not None:
        return cached
    path = _resolve_unmapped_module(root, module_name)
    if path is None:
        cache[module_name] = ()
        return ()
    imports = scan_lean_header_imports(path.read_text(encoding="utf-8"))
    cache[module_name] = imports
    return imports


def _project_node_dependencies(
    node_id: str,
    direct_imports: tuple[str, ...],
    module_to_node: Mapping[str, str],
    root: Path,
    cache: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    deps: set[str] = set()
    visited_unmapped: set[str] = set()
    queue: deque[str] = deque(direct_imports)
    while queue:
        imp = queue.popleft()
        dep_id = module_to_node.get(imp)
        if dep_id is not None:
            if dep_id != node_id:
                deps.add(dep_id)
            continue
        if imp in visited_unmapped:
            continue
        visited_unmapped.add(imp)
        queue.extend(_load_unmapped_imports(root, imp, cache))
    return tuple(sorted(deps))


def project_nearest_landmarks(
    resolved_root: Path,
    stages: tuple[StageDefinition, ...],
    modules: Mapping[str, ExtractedModule],
    module_to_node: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    """Project each mapped node's imports to nearest mapped landmarks via unmapped files."""
    root = resolved_root.resolve()
    cache: dict[str, tuple[str, ...]] = {}
    projected: dict[str, tuple[str, ...]] = {}
    for stage in stages:
        for mapping in stage.files:
            node_id = mapping.skeleton_file.removesuffix(".lean")
            module = modules[mapping.upstream_path]
            projected[node_id] = _project_node_dependencies(
                node_id, module.imports, module_to_node, root, cache
            )
    return projected
