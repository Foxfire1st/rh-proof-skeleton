"""Fail-closed static VitePress site compiler with atomic staging and stale pruning."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from rh_pipeline.models.audit import VerificationStatus
from rh_pipeline.models.concepts import ConceptCard
from rh_pipeline.models.dag import DagDeclarationRef, DagGraph
from rh_pipeline.site.markdown_transforms import (
    SiteDirectiveError,
    expand_concept_directives,
    mask_code_spans,
    mask_fenced_code,
    normalize_base_url,
    render_page_glossary,
    split_zoom_levels,
)
from rh_pipeline.site.renderers import (
    Backlink,
    RenderContext,
    SiteBuildError,
    expand_stage_markers,
    inject_verification_banner,
    render_concept_page,
    render_concepts_index,
    render_landing_index,
    render_verification_banner,
)
from rh_pipeline.validate.concepts import validate_concept_corpus
from rh_pipeline.validate.jargon import validate_explainer_docs

__all__ = ["SiteBuildConfig", "SiteBuildError", "SiteBuildReport", "compile_site"]

_H1_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
_LEAN_NODE_MARKER_RE = re.compile(r"<!--\s*rh:lean-node:([^\s>]+)\s*-->")
_MD_ASSET_RE = re.compile(r"(!?\[[^\]]*\]\()(\s*)(<[^>]+>|[^)\s]+)([^)]*\))")
_HTML_ASSET_RE = re.compile(r"""((?<![\w-])(?:src|href)\s*=\s*)(["'])([^"']+)(\2)""")


@dataclass(frozen=True, slots=True)
class SiteBuildConfig:
    """Configuration inputs for a deterministic VitePress site build."""

    repo_root: Path
    memory_root: Path
    out_docs_dir: Path
    base_url: str = "/ar-rh-proof-skeleton/"


@dataclass(frozen=True, slots=True)
class SiteBuildReport:
    """Summary counts returned after a successful atomic site build."""

    stages_built: int
    concepts_built: int
    assets_copied: int


def _load_json_object(path: Path, label: str) -> object:
    if not path.is_file():
        raise SiteBuildError(f"Missing required {label}: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteBuildError(f"Failed to load {label} ({path}): {exc}") from exc


def _load_verification_and_dag(repo_root: Path) -> tuple[VerificationStatus, DagGraph]:
    ver_raw = _load_json_object(
        repo_root / "audit" / "verification-status.json", "audit/verification-status.json"
    )
    dag_raw = _load_json_object(repo_root / "dag" / "dag.json", "dag/dag.json")
    try:
        verification = VerificationStatus.from_dict(ver_raw)
        dag = DagGraph.from_dict(dag_raw)
    except ValueError as exc:
        raise SiteBuildError(f"Invalid repository JSON schema: {exc}") from exc
    if verification.upstream_commit != dag.upstream_commit:
        raise SiteBuildError(
            "Upstream commit mismatch between audit/verification-status.json "
            f"({verification.upstream_commit}) and dag/dag.json ({dag.upstream_commit})"
        )
    return verification, dag


def _load_skeleton_lines(repo_root: Path, dag: DagGraph) -> dict[str, tuple[str, ...]]:
    skeleton_root = (repo_root / "skeleton").resolve()
    loaded: dict[str, tuple[str, ...]] = {}
    for node in dag.nodes:
        rel = node.skeleton_file
        if rel in loaded:
            continue
        target = (skeleton_root / rel).resolve()
        if not target.is_relative_to(skeleton_root) or not target.is_file():
            raise SiteBuildError(f"Missing or unsafe skeleton file: {rel!r}")
        try:
            text = target.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except (OSError, UnicodeDecodeError) as exc:
            raise SiteBuildError(f"Unable to read skeleton file {rel!r}: {exc}") from exc
        loaded[rel] = tuple(text.split("\n"))
    return loaded


def _read_stage_file(md_path: Path, resolved_root: Path) -> str:
    resolved = md_path.resolve()
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise SiteBuildError(f"Unsafe or non-regular stage file: {md_path}")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SiteBuildError(f"Unable to read stage file {md_path}: {exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _load_stage_docs(docs_root: Path) -> dict[tuple[str, str], str]:
    resolved_root = docs_root.resolve()
    stages_dir = docs_root / "stages"
    pages: dict[tuple[str, str], str] = {}
    for stage_dir in sorted(stages_dir.iterdir()) if stages_dir.is_dir() else ():
        if stage_dir.is_dir():
            for md_path in sorted(stage_dir.glob("*.md")):
                pages[(stage_dir.name, md_path.stem)] = _read_stage_file(md_path, resolved_root)
    if ("00_statement", "overview") not in pages:
        raise SiteBuildError("Required Stage 00 page 'stages/00_statement/overview.md' is missing")
    return pages


def _load_assets(docs_root: Path) -> dict[str, bytes]:
    resolved_root = docs_root.resolve()
    assets_dir = docs_root / "assets"
    if not assets_dir.exists() and not assets_dir.is_symlink():
        return {}
    if not assets_dir.is_dir() or not assets_dir.resolve().is_relative_to(resolved_root):
        raise SiteBuildError(f"Invalid assets directory under docs root: {assets_dir}")
    assets: dict[str, bytes] = {}
    for path in sorted(assets_dir.rglob("*")):
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise SiteBuildError(f"Asset path escapes docs root: {path}")
        if path.is_dir():
            continue
        if not resolved.is_file():
            raise SiteBuildError(f"Unsupported non-regular asset entry: {path}")
        rel = path.relative_to(docs_root).as_posix()
        try:
            assets[rel] = resolved.read_bytes()
        except OSError as exc:
            raise SiteBuildError(f"Failed to read asset {rel!r}: {exc}") from exc
    return assets


def _extract_asset_rel(url: str, *, base_url: str) -> tuple[str, str] | None:
    stripped = url[1:-1] if url.startswith("<") and url.endswith(">") else url
    core, sep, suffix = stripped.partition("#")
    path_only, q_sep, query = core.partition("?")
    tail = (f"?{query}" if q_sep else "") + (f"#{suffix}" if sep else "")
    for prefix in (f"{base_url}assets/", "/assets/", "./assets/", "assets/"):
        if path_only.startswith(prefix):
            return f"assets/{path_only.removeprefix(prefix)}", tail
    return None


def _validate_asset_rel(asset_rel: str, available: AbstractSet[str], rel_path: str) -> None:
    if any(part in {"", ".", ".."} for part in asset_rel.split("/")) or "\\" in asset_rel:
        raise SiteBuildError(f"Unsafe asset path {asset_rel!r} referenced in {rel_path}")
    if asset_rel not in available:
        raise SiteBuildError(f"Missing asset {asset_rel!r} referenced in {rel_path}")


def _collect_asset_replacements(
    markdown: str,
    masked: str,
    pattern: re.Pattern[str],
    available: AbstractSet[str],
    *,
    base_url: str,
    rel_path: str,
    html_mode: bool,
) -> list[tuple[int, int, str]]:
    edits: list[tuple[int, int, str]] = []
    for match in pattern.finditer(masked):
        raw_url = markdown[match.start(3) : match.end(3)]
        extracted = _extract_asset_rel(raw_url, base_url=base_url)
        if extracted is None:
            continue
        asset_rel, tail = extracted
        _validate_asset_rel(asset_rel, available, rel_path)
        if html_mode:
            rewritten = f"/{asset_rel}{tail}"
        else:
            rewritten = (
                f"</{asset_rel}{tail}>" if raw_url.startswith("<") else f"/{asset_rel}{tail}"
            )
        prefix, suffix = (
            markdown[match.start(1) : match.end(2)],
            markdown[match.start(4) : match.end(4)],
        )
        edits.append((match.start(), match.end(), f"{prefix}{rewritten}{suffix}"))
    return edits


def _rewrite_assets(
    markdown: str, available: AbstractSet[str], *, base_url: str, rel_path: str
) -> str:
    masked = mask_code_spans(markdown)
    edits = _collect_asset_replacements(
        markdown,
        masked,
        _MD_ASSET_RE,
        available,
        base_url=base_url,
        rel_path=rel_path,
        html_mode=False,
    )
    edits.extend(
        _collect_asset_replacements(
            markdown,
            masked,
            _HTML_ASSET_RE,
            available,
            base_url=base_url,
            rel_path=rel_path,
            html_mode=True,
        )
    )
    if not edits:
        return markdown
    out: list[str] = []
    cursor = 0
    for start, end, text in sorted(edits):
        if start < cursor:
            continue
        out.extend((markdown[cursor:start], text))
        cursor = end
    out.append(markdown[cursor:])
    return "".join(out)


def _extract_title(markdown: str, fallback: str) -> str:
    orig_lines = markdown.split("\n")
    for idx, masked in enumerate(mask_fenced_code(markdown).split("\n")):
        if _H1_TITLE_RE.match(masked) is not None:
            match = _H1_TITLE_RE.match(orig_lines[idx])
            if match is not None:
                return match.group(1)
    return fallback


def _build_render_context(
    dag: DagGraph,
    verification: VerificationStatus,
    registry: Mapping[str, ConceptCard],
    skeleton_lines: Mapping[str, tuple[str, ...]],
    stage_pages: Mapping[tuple[str, str], str],
    base_url: str,
) -> RenderContext:
    stage_stems: dict[str, set[str]] = {}
    node_page_stems: dict[str, tuple[str, str]] = {}
    for (stage_id, stem), raw_md in sorted(stage_pages.items()):
        stage_stems.setdefault(stage_id, set()).add(stem)
        for match in _LEAN_NODE_MARKER_RE.finditer(mask_code_spans(raw_md)):
            node_page_stems.setdefault(match.group(1), (stage_id, stem))
    decls_by_anchor: dict[tuple[str, str], DagDeclarationRef] = {
        (node.skeleton_file, decl.name): decl for node in dag.nodes for decl in node.declarations
    }
    return RenderContext(
        dag=dag,
        verification=verification,
        registry=registry,
        nodes_by_id={node.id: node for node in dag.nodes},
        stages_by_id={stage.stage_id: stage for stage in dag.stages},
        decls_by_anchor=decls_by_anchor,
        skeleton_lines=skeleton_lines,
        stage_page_stems={k: frozenset(v) for k, v in stage_stems.items()},
        node_page_stems=node_page_stems,
        base_url=base_url,
        banner_tag=render_verification_banner(verification),
    )


def _write_temp_bytes(target_dir: Path, prefix: str, payload: bytes) -> Path:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target_dir, prefix=prefix, suffix=".tmp", delete=False
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return tmp_path
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _write_outputs_atomically(outputs: Sequence[tuple[Path, bytes]]) -> set[Path]:
    resolved_targets = {target.resolve() for target, _ in outputs}
    if len(resolved_targets) != len(outputs):
        raise SiteBuildError("Duplicate output target paths detected")
    staged: list[tuple[Path, Path]] = []
    try:
        for target_path, payload in outputs:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = _write_temp_bytes(target_path.parent, f".{target_path.name}.", payload)
            staged.append((tmp_path, target_path))
        for tmp_path, target_path in staged:
            os.replace(tmp_path, target_path)
    except BaseException:
        for tmp_path, _ in staged:
            tmp_path.unlink(missing_ok=True)
        raise
    return resolved_targets


def _prune_managed_dirs(out_docs_dir: Path, written: AbstractSet[Path]) -> None:
    managed = (
        out_docs_dir / "stages",
        out_docs_dir / "concepts",
        out_docs_dir / "assets",
        out_docs_dir / "public" / "assets",
    )
    for root in managed:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                if not any(path.iterdir()):
                    path.rmdir()
            elif path.resolve() not in written:
                path.unlink()


def _validate_corpus(repo_root: Path, docs_root: Path) -> dict[str, ConceptCard]:
    registry, concept_findings = validate_concept_corpus(
        docs_root, skeleton_root=repo_root / "skeleton"
    )
    if concept_findings:
        summary = "; ".join(f.format() for f in concept_findings[:5])
        raise SiteBuildError(f"Concept corpus validation failed: {summary}")
    if not registry:
        raise SiteBuildError("No Concept Cards found under docs/concepts/")
    explainer_findings = validate_explainer_docs(docs_root, registry=registry)
    if explainer_findings:
        summary = "; ".join(f.format() for f in explainer_findings[:5])
        raise SiteBuildError(f"Explainer validation failed: {summary}")
    return registry


def _compile_stage_pages(
    stage_pages: Mapping[tuple[str, str], str],
    asset_keys: AbstractSet[str],
    ctx: RenderContext,
    backlinks: Mapping[str, dict[str, Backlink]],
    out_docs_dir: Path,
) -> tuple[list[tuple[Path, bytes]], dict[tuple[str, str], str]]:
    outputs: list[tuple[Path, bytes]] = []
    stage_titles: dict[tuple[str, str], str] = {}
    for (stage_id, stem), raw_md in sorted(stage_pages.items()):
        rel = f"stages/{stage_id}/{stem}.md"
        expanded, refs = expand_concept_directives(raw_md, ctx.registry, base_url=ctx.base_url)
        with_markers = expand_stage_markers(expanded, ctx)
        with_assets = _rewrite_assets(with_markers, asset_keys, base_url=ctx.base_url, rel_path=rel)
        rendered = inject_verification_banner(
            split_zoom_levels(with_assets, page_id=f"{stage_id}/{stem}"), ctx.banner_tag
        )
        if glossary := render_page_glossary(refs, ctx.registry, base_url=ctx.base_url):
            rendered = f"{rendered.rstrip()}\n\n{glossary}\n"
        title = _extract_title(raw_md, f"{stage_id}/{stem}")
        stage_titles[(stage_id, stem)] = title
        md_href = f"/stages/{stage_id}/{stem}"
        for slug in refs:
            backlinks[slug][md_href] = Backlink(label=title, md_href=md_href, kind="Stage")
        outputs.append(
            (out_docs_dir / "stages" / stage_id / f"{stem}.md", rendered.encode("utf-8"))
        )
    return outputs, stage_titles


def _compile_concept_pages(
    asset_keys: AbstractSet[str],
    ctx: RenderContext,
    backlinks: Mapping[str, dict[str, Backlink]],
    out_docs_dir: Path,
) -> list[tuple[Path, bytes]]:
    expanded_map: dict[str, tuple[str, tuple[str, ...]]] = {}
    for slug, card in sorted(ctx.registry.items()):
        expanded, refs = expand_concept_directives(
            card.body_markdown, ctx.registry, base_url=ctx.base_url
        )
        rewritten = _rewrite_assets(
            expanded, asset_keys, base_url=ctx.base_url, rel_path=f"concepts/{slug}.md"
        )
        expanded_map[slug] = (rewritten, refs)
        md_href = f"/concepts/{slug}"
        for target_slug in (*refs, *card.child_concepts):
            if target_slug != slug:
                backlinks[target_slug][md_href] = Backlink(card.term, md_href, "Concept")
    outputs: list[tuple[Path, bytes]] = []
    for slug, card in sorted(ctx.registry.items()):
        ordered = sorted(
            backlinks[slug].values(),
            key=lambda b: (0 if b.kind == "Stage" else 1, b.label.lower(), b.md_href),
        )
        body_md, body_refs = expanded_map[slug]
        page = render_concept_page(card, body_md, body_refs, ordered, ctx)
        outputs.append((out_docs_dir / "concepts" / f"{slug}.md", page.encode("utf-8")))
    return outputs


def _compile_all_outputs(
    stage_pages: Mapping[tuple[str, str], str],
    assets: Mapping[str, bytes],
    ctx: RenderContext,
    out_docs_dir: Path,
) -> list[tuple[Path, bytes]]:
    backlinks: dict[str, dict[str, Backlink]] = {slug: {} for slug in ctx.registry}
    stage_out, stage_titles = _compile_stage_pages(
        stage_pages, assets.keys(), ctx, backlinks, out_docs_dir
    )
    concept_out = _compile_concept_pages(assets.keys(), ctx, backlinks, out_docs_dir)
    index_out = [
        (out_docs_dir / "concepts" / "index.md", render_concepts_index(ctx).encode("utf-8")),
        (out_docs_dir / "index.md", render_landing_index(stage_titles, ctx).encode("utf-8")),
    ]
    asset_out = [
        pair
        for rel, payload in sorted(assets.items())
        for pair in ((out_docs_dir / rel, payload), (out_docs_dir / "public" / rel, payload))
    ]
    return [*stage_out, *concept_out, *index_out, *asset_out]


def compile_site(config: SiteBuildConfig) -> SiteBuildReport:
    """Validate all inputs in memory, then atomically emit the compiled VitePress docs tree."""
    try:
        base_url = normalize_base_url(config.base_url)
    except SiteDirectiveError as exc:
        raise SiteBuildError(str(exc)) from exc
    docs_root = config.memory_root / "docs"
    verification, dag = _load_verification_and_dag(config.repo_root)
    registry = _validate_corpus(config.repo_root, docs_root)
    stage_pages = _load_stage_docs(docs_root)
    assets = _load_assets(docs_root)
    for card in registry.values():
        if card.visual_ref is not None and card.visual_ref not in assets:
            raise SiteBuildError(
                f"Concept '{card.slug}' visual_ref '{card.visual_ref}' not found under docs/assets/"
            )
    skeleton_lines = _load_skeleton_lines(config.repo_root, dag)
    ctx = _build_render_context(dag, verification, registry, skeleton_lines, stage_pages, base_url)
    try:
        outputs = _compile_all_outputs(stage_pages, assets, ctx, config.out_docs_dir)
    except SiteDirectiveError as exc:
        raise SiteBuildError(str(exc)) from exc
    written = _write_outputs_atomically(outputs)
    _prune_managed_dirs(config.out_docs_dir, written)
    return SiteBuildReport(len(stage_pages), len(registry), len(assets))
