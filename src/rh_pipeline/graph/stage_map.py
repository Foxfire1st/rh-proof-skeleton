"""Stage-map loading, import-driven DAG construction, and atomic skeleton extraction."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from rh_pipeline.lean.parser import extract_declarations
from rh_pipeline.lean.stub_emitter import render_skeleton_file
from rh_pipeline.models.dag import (
    DAG_SCHEMA_VERSION,
    DagDeclarationRef,
    DagGraph,
    DagNode,
    StageDefinition,
    StageFileMapping,
    StageMap,
)
from rh_pipeline.models.lean import ExtractedModule

__all__ = [
    "StageMapError",
    "build_dag",
    "extract_and_write",
    "lean_module_name_for_path",
    "load_stage_map",
]

_MODULE_ROOT_PREFIXES: tuple[str, ...] = ("src/", "comparator/")
_LANDMARK_KINDS: frozenset[str] = frozenset({"theorem", "lemma"})


class StageMapError(ValueError):
    """Raised when stage-map config, upstream inputs, or DAG assembly are invalid."""


def lean_module_name_for_path(upstream_path: str) -> str:
    """Resolve a repository-relative Lean source path to its Lean module name."""
    if (
        not upstream_path.endswith(".lean")
        or upstream_path.startswith("/")
        or "\\" in upstream_path
    ):
        raise StageMapError(f"Invalid upstream Lean path: {upstream_path!r}")
    stem = upstream_path.removesuffix(".lean")
    for prefix in _MODULE_ROOT_PREFIXES:
        if stem.startswith(prefix):
            stem = stem.removeprefix(prefix)
            break
    parts = stem.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StageMapError(f"Cannot derive Lean module name from {upstream_path!r}")
    return ".".join(parts)


def _validate_file_mapping(
    mapping: StageFileMapping,
    seen_upstream: set[str],
    seen_skeleton: set[str],
    module_to_node: dict[str, str],
) -> None:
    if not mapping.skeleton_file.endswith(".lean"):
        raise StageMapError(f"skeleton_file must end with '.lean': {mapping.skeleton_file!r}")
    node_id = mapping.skeleton_file.removesuffix(".lean")
    if not node_id or node_id.endswith("/"):
        raise StageMapError(f"Invalid skeleton_file stem: {mapping.skeleton_file!r}")
    module_name = lean_module_name_for_path(mapping.upstream_path)
    if mapping.upstream_path in seen_upstream:
        raise StageMapError(f"Duplicate upstream_path in stage map: {mapping.upstream_path!r}")
    if mapping.skeleton_file in seen_skeleton:
        raise StageMapError(f"Duplicate skeleton_file in stage map: {mapping.skeleton_file!r}")
    if module_name in module_to_node:
        raise StageMapError(
            f"Duplicate Lean module name {module_name!r} for {mapping.upstream_path!r}"
        )
    seen_upstream.add(mapping.upstream_path)
    seen_skeleton.add(mapping.skeleton_file)
    module_to_node[module_name] = node_id


def _index_stage_files(ordered: tuple[StageDefinition, ...]) -> dict[str, str]:
    seen_upstream: set[str] = set()
    seen_skeleton: set[str] = set()
    module_to_node: dict[str, str] = {}
    for stage in ordered:
        for mapping in stage.files:
            _validate_file_mapping(mapping, seen_upstream, seen_skeleton, module_to_node)
    return module_to_node


def _validate_stages(
    stages: tuple[StageDefinition, ...],
) -> tuple[tuple[StageDefinition, ...], dict[str, str]]:
    if not stages:
        raise StageMapError("Stage map must define at least one stage")
    if len({stage.stage_id for stage in stages}) != len(stages):
        raise StageMapError("Duplicate stage_id in stage map")
    if len({stage.order for stage in stages}) != len(stages):
        raise StageMapError("Duplicate stage order in stage map")
    ordered = tuple(sorted(stages, key=lambda stage: stage.order))
    actual_orders = [stage.order for stage in ordered]
    if actual_orders != list(range(len(ordered))):
        raise StageMapError(
            f"Stage order values must be consecutive 0..{len(ordered) - 1}, got {actual_orders}"
        )
    return ordered, _index_stage_files(ordered)


def load_stage_map(path: Path) -> tuple[StageDefinition, ...]:
    """Read, parse, and validate dag/stage-map.json, returning stages ordered by index."""
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        stage_map = StageMap.from_dict(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise StageMapError(f"Invalid stage map {path}: {exc}") from exc
    ordered, _ = _validate_stages(stage_map.stages)
    return ordered


def _validate_unique_decl_names(refs: tuple[DagDeclarationRef, ...], skeleton_file: str) -> None:
    seen_names: set[str] = set()
    for ref in refs:
        if ref.name in seen_names:
            raise StageMapError(f"Duplicate declaration {ref.name!r} in {skeleton_file!r}")
        seen_names.add(ref.name)


def _build_dag_node(
    stage_id: str,
    mapping: StageFileMapping,
    module: ExtractedModule,
    refs: tuple[DagDeclarationRef, ...],
    module_to_node: Mapping[str, str],
) -> DagNode:
    _validate_unique_decl_names(refs, mapping.skeleton_file)
    node_id = mapping.skeleton_file.removesuffix(".lean")
    # Edges come strictly from resolved Lean import statements among mapped nodes.
    depends_on = tuple(
        sorted(
            {
                dep_id
                for imp in module.imports
                if (dep_id := module_to_node.get(imp)) is not None and dep_id != node_id
            }
        )
    )
    title = Path(mapping.upstream_path).stem
    is_landmark = any(ref.kind in _LANDMARK_KINDS for ref in refs)
    try:
        return DagNode(
            id=node_id,
            stage_id=stage_id,
            title=title,
            skeleton_file=mapping.skeleton_file,
            upstream_path=mapping.upstream_path,
            declarations=refs,
            depends_on=depends_on,
            is_landmark=is_landmark,
        )
    except ValueError as exc:
        raise StageMapError(f"Invalid DAG node for {mapping.upstream_path!r}: {exc}") from exc


def build_dag(
    stages: tuple[StageDefinition, ...],
    modules: Mapping[str, ExtractedModule],
    decl_refs: Mapping[str, tuple[DagDeclarationRef, ...]],
    *,
    upstream_commit: str,
) -> DagGraph:
    """Build a deterministic proof DAG from validated stages, parsed modules, and decl refs."""
    if not upstream_commit or not upstream_commit.strip():
        raise StageMapError("upstream_commit must not be empty")
    ordered_stages, module_to_node = _validate_stages(stages)
    nodes: list[DagNode] = []
    for stage in ordered_stages:
        for mapping in stage.files:
            module = modules.get(mapping.upstream_path)
            if module is None:
                raise StageMapError(f"Missing extracted module for {mapping.upstream_path!r}")
            if module.upstream_path != mapping.upstream_path:
                raise StageMapError(
                    f"Module path mismatch for {mapping.upstream_path!r}: {module.upstream_path!r}"
                )
            refs = decl_refs.get(mapping.upstream_path)
            if refs is None:
                raise StageMapError(f"Missing declaration refs for {mapping.upstream_path!r}")
            nodes.append(_build_dag_node(stage.stage_id, mapping, module, refs, module_to_node))
    return DagGraph(
        schema_version=DAG_SCHEMA_VERSION,
        upstream_commit=upstream_commit,
        stages=ordered_stages,
        nodes=tuple(nodes),
    )


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise StageMapError(f"Failed to invoke git for {repo_root}: {exc}") from exc
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output:
        detail = completed.stderr.strip() or "empty or non-zero git response"
        raise StageMapError(f"git {' '.join(args)} failed in {repo_root}: {detail}")
    return output


def _inspect_upstream_repo(upstream_root: Path) -> tuple[Path, str]:
    if not upstream_root.is_dir():
        raise StageMapError(f"upstream_root directory does not exist: {upstream_root}")
    resolved = upstream_root.resolve()
    toplevel = Path(_run_git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if toplevel != resolved:
        raise StageMapError(
            f"upstream_root must be a Git repository root, got {resolved} (toplevel={toplevel})"
        )
    return resolved, _run_git(resolved, "rev-parse", "--verify", "HEAD")


def _read_upstream_source(resolved_root: Path, upstream_path: str) -> str:
    candidate = (resolved_root / upstream_path).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise StageMapError(f"Mapped upstream path escapes repository root: {upstream_path!r}")
    if not candidate.is_file():
        raise StageMapError(f"Missing mapped upstream Lean file: {upstream_path!r}")
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StageMapError(f"Unable to read upstream file {upstream_path!r}: {exc}") from exc


def _write_temp_file(target_dir: Path, prefix: str, content: str) -> Path:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target_dir, prefix=prefix, suffix=".tmp", delete=False
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return tmp_path
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _write_outputs_atomically(outputs: Sequence[tuple[Path, str]]) -> None:
    if len({target.resolve() for target, _ in outputs}) != len(outputs):
        raise StageMapError("Duplicate output target paths detected")
    staged: list[tuple[Path, Path]] = []
    try:
        for target_path, content in outputs:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = _write_temp_file(target_path.parent, f".{target_path.name}.", content)
            staged.append((tmp_path, target_path))
        for tmp_path, target_path in staged:
            os.replace(tmp_path, target_path)
    except BaseException:
        for tmp_path, _ in staged:
            tmp_path.unlink(missing_ok=True)
        raise


def extract_and_write(
    *,
    upstream_root: Path,
    stage_map_path: Path,
    out_skeleton_dir: Path,
    out_dag_path: Path,
) -> DagGraph:
    """Parse all mapped upstream files, build the DAG in memory, then write outputs atomically."""
    stages = load_stage_map(stage_map_path)
    resolved_root, upstream_commit = _inspect_upstream_repo(upstream_root)
    modules: dict[str, ExtractedModule] = {}
    decl_refs: dict[str, tuple[DagDeclarationRef, ...]] = {}
    rendered_outputs: list[tuple[Path, str]] = []

    for stage in stages:
        for mapping in stage.files:
            source = _read_upstream_source(resolved_root, mapping.upstream_path)
            module = extract_declarations(
                source, rel_path=mapping.upstream_path, commit=upstream_commit
            )
            skeleton_text, refs = render_skeleton_file(module, preserve_defs=mapping.preserve_defs)
            modules[mapping.upstream_path] = module
            decl_refs[mapping.upstream_path] = refs
            rendered_outputs.append((out_skeleton_dir / mapping.skeleton_file, skeleton_text))

    dag = build_dag(stages, modules, decl_refs, upstream_commit=upstream_commit)
    _write_outputs_atomically([*rendered_outputs, (out_dag_path, dag.to_json())])
    return dag
