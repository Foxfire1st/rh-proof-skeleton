"""Pure Markdown and SVG renderers for VitePress stage, concept, and index pages."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rh_pipeline.models.audit import VerificationStatus
from rh_pipeline.models.concepts import ConceptCard
from rh_pipeline.models.dag import DagDeclarationRef, DagGraph, DagNode, StageDefinition
from rh_pipeline.site.markdown_transforms import mask_code_spans, render_page_glossary

__all__ = [
    "UPSTREAM_REPO_URL",
    "Backlink",
    "RenderContext",
    "SiteBuildError",
    "expand_stage_markers",
    "inject_verification_banner",
    "render_concept_page",
    "render_concepts_index",
    "render_landing_index",
    "render_stage_minimap",
    "render_verification_banner",
]

UPSTREAM_REPO_URL = "https://github.com/leanprover-community/riemann-hypothesis"

_STAGE_MARKER_RE = re.compile(r"<!--\s*rh:(lean-node|stage-minimap):([^\s>]+)\s*-->")
_H1_RE = re.compile(r"^#\s+.+$")
_BACKTICK_RUN_RE = re.compile(r"`+")


class SiteBuildError(ValueError):
    """Raised when site inputs, anchors, markers, or assets fail fail-closed validation."""


@dataclass(frozen=True, slots=True)
class Backlink:
    """One incoming link from a Stage or Concept page to a Concept page."""

    label: str
    md_href: str
    kind: Literal["Stage", "Concept"]


@dataclass(frozen=True, slots=True)
class RenderContext:
    """Immutable validated inputs needed to render all generated VitePress Markdown pages."""

    dag: DagGraph
    verification: VerificationStatus
    registry: Mapping[str, ConceptCard]
    nodes_by_id: Mapping[str, DagNode]
    stages_by_id: Mapping[str, StageDefinition]
    decls_by_anchor: Mapping[tuple[str, str], DagDeclarationRef]
    skeleton_lines: Mapping[str, tuple[str, ...]]
    stage_page_stems: Mapping[str, frozenset[str]]
    node_page_stems: Mapping[str, tuple[str, str]]
    base_url: str
    banner_tag: str


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def upstream_blob_url(
    commit: str, upstream_path: str, start_line: int | None = None, end_line: int | None = None
) -> str:
    """Build an exact upstream GitHub blob URL, optionally anchored to a line range."""
    base = f"{UPSTREAM_REPO_URL}/blob/{commit}/{upstream_path}"
    return f"{base}#L{start_line}-L{end_line}" if start_line and end_line else base


def render_verification_banner(status: VerificationStatus) -> str:
    """Render the `<VerificationBanner />` Vue component tag from verified audit metadata."""
    repo_layout = (
        "legacy" if status.community_verification_notes_sha256 is not None else "condensed"
    )
    attrs = (
        f'build-status="{_esc(status.build_status)}"',
        f'upstream-commit="{_esc(status.upstream_commit)}"',
        f'upstream-repo="{_esc(UPSTREAM_REPO_URL)}"',
        f'repo-layout="{repo_layout}"',
        f'lean-toolchain="{_esc(status.lean_toolchain)}"',
        f'mathlib-rev="{_esc(status.mathlib_rev)}"',
        f'scan-scope="{_esc(status.scan_scope)}"',
        f':solution-forbidden-count="{status.solution_forbidden_token_count}"',
        f':challenge-sorry-count="{status.challenge_sorry_count}"',
        f'permitted-axioms="{_esc(", ".join(status.permitted_axioms))}"',
        f':theorem-count="{len(status.theorem_names)}"',
    )
    return f"<VerificationBanner {' '.join(attrs)} />"


def inject_verification_banner(markdown: str, banner_tag: str) -> str:
    """Insert `<VerificationBanner ... />` directly after the first top-level `# ` heading."""
    if "<VerificationBanner" in markdown:
        return markdown
    lines = markdown.split("\n")
    for idx, masked in enumerate(mask_code_spans(markdown).split("\n")):
        if _H1_RE.match(masked):
            return "\n".join([*lines[: idx + 1], "", banner_tag, *lines[idx + 1 :]])
    return f"{banner_tag}\n\n{markdown}"


def _extract_snippet(
    skeleton_lines: Mapping[str, tuple[str, ...]],
    skeleton_file: str,
    start_line: int,
    end_line: int,
) -> str:
    lines = skeleton_lines.get(skeleton_file)
    if lines is None:
        raise SiteBuildError(f"Skeleton file not loaded: {skeleton_file!r}")
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise SiteBuildError(
            f"Invalid line range {start_line}..{end_line} for {skeleton_file!r} "
            f"(lines={len(lines)})"
        )
    return "\n".join(lines[start_line - 1 : end_line])


def render_lean_declaration(decl: DagDeclarationRef, snippet: str, *, commit: str) -> str:
    """Render one verbatim Lean skeleton declaration paired with its upstream GitHub link."""
    url = upstream_blob_url(
        commit, decl.upstream_path, decl.upstream_start_line, decl.upstream_end_line
    )
    longest = max((len(m.group(0)) for m in _BACKTICK_RUN_RE.finditer(snippet)), default=0)
    fence = "`" * max(3, longest + 1)
    loc = f"`{decl.upstream_path}` L{decl.upstream_start_line}-L{decl.upstream_end_line}"
    return (
        f'<div class="rh-lean-decl" data-decl="{_esc(decl.name)}">\n\n'
        f"**`{decl.kind} {decl.name}`** — [Upstream source ({loc})]({url})\n\n"
        f"{fence}lean\n{snippet}\n{fence}\n\n"
        "</div>"
    )


def render_lean_node(node: DagNode, ctx: RenderContext) -> str:
    """Render all verbatim Lean declarations belonging to one `DagNode`."""
    if not node.declarations:
        url = upstream_blob_url(ctx.dag.upstream_commit, node.upstream_path)
        return f"No extracted declarations for `{node.id}` ([upstream file]({url}))."
    blocks = [
        render_lean_declaration(
            decl,
            _extract_snippet(
                ctx.skeleton_lines, node.skeleton_file, decl.start_line, decl.end_line
            ),
            commit=ctx.dag.upstream_commit,
        )
        for decl in node.declarations
    ]
    return "\n\n".join(blocks)


def _node_links(node: DagNode, ctx: RenderContext) -> tuple[str, str]:
    mapped = ctx.node_page_stems.get(node.id)
    if mapped is not None:
        stage_id, page_stem = mapped
        return (
            f"{ctx.base_url}stages/{stage_id}/{page_stem}",
            f"/stages/{stage_id}/{page_stem}",
        )
    stem = Path(node.skeleton_file).stem
    stems = ctx.stage_page_stems.get(node.stage_id, frozenset())
    if stem in stems:
        return f"{ctx.base_url}stages/{node.stage_id}/{stem}", f"/stages/{node.stage_id}/{stem}"
    if "overview" in stems:
        return (
            f"{ctx.base_url}stages/{node.stage_id}/overview",
            f"/stages/{node.stage_id}/overview",
        )
    upstream = upstream_blob_url(ctx.dag.upstream_commit, node.upstream_path)
    return upstream, upstream


def _compute_node_layers(stage_nodes: Sequence[DagNode]) -> dict[str, int]:
    in_stage = {node.id for node in stage_nodes}
    layers = {node.id: 0 for node in stage_nodes}
    for _ in range(len(stage_nodes)):
        changed = False
        for node in stage_nodes:
            deps = [layers[dep] for dep in node.depends_on if dep in in_stage]
            if deps and (target := max(deps) + 1) > layers[node.id]:
                layers[node.id], changed = target, True
        if not changed:
            break
    return layers


def _layout_stage_nodes(
    stage_nodes: Sequence[DagNode],
) -> tuple[dict[str, tuple[int, int]], int, int]:
    layers = _compute_node_layers(stage_nodes)
    columns: dict[int, list[DagNode]] = {}
    for node in stage_nodes:
        columns.setdefault(layers[node.id], []).append(node)
    box_w, box_h, gap_x, gap_y, pad = 310, 62, 64, 22, 24
    positions = {
        node.id: (pad + col_idx * (box_w + gap_x), pad + row_idx * (box_h + gap_y))
        for col_idx in sorted(columns)
        for row_idx, node in enumerate(columns[col_idx])
    }
    max_rows = max((len(col) for col in columns.values()), default=1)
    width = pad * 2 + max(len(columns), 1) * box_w + max(len(columns) - 1, 0) * gap_x
    height = pad * 2 + max_rows * box_h + max(max_rows - 1, 0) * gap_y
    return positions, width, height


def _render_minimap_svg(
    stage: StageDefinition, stage_nodes: Sequence[DagNode], ctx: RenderContext
) -> str:
    positions, width, height = _layout_stage_nodes(stage_nodes)
    box_w, box_h = 310, 62
    t_id, d_id = f"rh-minimap-title-{stage.stage_id}", f"rh-minimap-desc-{stage.stage_id}"
    lines = [
        '<figure class="rh-stage-minimap">',
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
        f'aria-labelledby="{_esc(t_id)} {_esc(d_id)}" xmlns="http://www.w3.org/2000/svg">',
        f'  <title id="{_esc(t_id)}">{html.escape(stage.title)} dependency mini-map</title>',
        f'  <desc id="{_esc(d_id)}">Proof graph for stage {html.escape(stage.stage_id)}.</desc>',
        '  <defs><marker id="rh-minimap-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#3f4a56" /></marker></defs>',
    ]
    for node in stage_nodes:
        x2, y2 = positions[node.id]
        for dep_id in node.depends_on:
            if dep_id in positions:
                x1, y1 = positions[dep_id]
                lines.append(
                    f'  <line class="rh-minimap-edge" x1="{x1 + box_w}" y1="{y1 + box_h // 2}" '
                    f'x2="{x2}" y2="{y2 + box_h // 2}" stroke="#3f4a56" stroke-width="2" '
                    'marker-end="url(#rh-minimap-arrow)" />'
                )
    for node in stage_nodes:
        x, y = positions[node.id]
        svg_href, _ = _node_links(node, ctx)
        cls = "rh-minimap-node rh-minimap-landmark" if node.is_landmark else "rh-minimap-node"
        stroke = "#1e40af" if node.is_landmark else "#3f4a56"
        fill = "#edf4ff" if node.is_landmark else "#faf9f5"
        badge = " [landmark]" if node.is_landmark else ""
        lines.append(
            f'  <a href="{_esc(svg_href)}"><g class="{cls}" data-node="{_esc(node.id)}">'
            f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2" />'
            f'<text x="{x + 12}" y="{y + 25}" fill="#161b22" font-size="12.5" font-weight="600">'
            f"{html.escape(node.title)}{badge}</text>"
            f'<text x="{x + 12}" y="{y + 46}" fill="#3f4a56" font-size="10.5" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, monospace">'
            f"{html.escape(node.upstream_path)}</text></g></a>"
        )
    lines.append("</svg>")
    lines.append(
        f"<figcaption>{html.escape(stage.title)}: solid blue border marks landmark theorem "
        "modules; arrows trace Lean import dependencies.</figcaption>"
    )
    lines.append("</figure>")
    return "\n".join(lines)


def render_stage_minimap(stage_id: str, ctx: RenderContext) -> str:
    """Render an accessible inline SVG dependency mini-map plus ordered Markdown dependency list."""
    stage = ctx.stages_by_id.get(stage_id)
    if stage is None:
        raise SiteBuildError(f"Unknown stage_id in minimap marker: {stage_id!r}")
    stage_nodes = [node for node in ctx.dag.nodes if node.stage_id == stage_id]
    svg = _render_minimap_svg(stage, stage_nodes, ctx)
    items: list[str] = []
    for idx, node in enumerate(stage_nodes, start=1):
        _, md_href = _node_links(node, ctx)
        upstream = upstream_blob_url(ctx.dag.upstream_commit, node.upstream_path)
        deps = ", ".join(f"`{d}`" for d in node.depends_on) if node.depends_on else "none"
        mark = " *(landmark)*" if node.is_landmark else ""
        items.append(
            f"{idx}. [{node.title}]({md_href}){mark} — "
            f"[`{node.upstream_path}`]({upstream}) (depends on: {deps})"
        )
    dep_list = "\n".join(items) if items else "1. No extracted Lean nodes in this stage."
    return f"{svg}\n\n{dep_list}"


def expand_stage_markers(markdown: str, ctx: RenderContext) -> str:
    """Expand `<!-- rh:lean-node:... -->` and `<!-- rh:stage-minimap:... -->` outside code."""
    parts: list[str] = []
    cursor = 0
    for match in _STAGE_MARKER_RE.finditer(mask_code_spans(markdown)):
        kind, arg = match.group(1), match.group(2)
        parts.append(markdown[cursor : match.start()])
        if kind == "lean-node":
            node = ctx.nodes_by_id.get(arg)
            if node is None:
                raise SiteBuildError(f"Unknown DAG node in lean-node marker: {arg!r}")
            parts.append(render_lean_node(node, ctx))
        else:
            parts.append(render_stage_minimap(arg, ctx))
        cursor = match.end()
    parts.append(markdown[cursor:])
    return "".join(parts)


def _render_concept_anchors(card: ConceptCard, ctx: RenderContext) -> str:
    if not card.lean_anchors:
        return ""
    blocks = ["## Formal Lean Anchors"]
    for anchor in card.lean_anchors:
        skeleton_rel, decl_name = anchor.split("#", 1)
        decl = ctx.decls_by_anchor.get((skeleton_rel, decl_name))
        if decl is None:
            raise SiteBuildError(f"Concept '{card.slug}' anchor '{anchor}' not in dag/dag.json")
        snippet = _extract_snippet(ctx.skeleton_lines, skeleton_rel, decl.start_line, decl.end_line)
        blocks.append(render_lean_declaration(decl, snippet, commit=ctx.dag.upstream_commit))
    return "\n\n".join(blocks)


def _render_concept_figure(card: ConceptCard) -> str:
    if card.visual_ref is None:
        return ""
    src = _esc(f"/{card.visual_ref}")
    alt = _esc(f"Diagram illustrating {card.term}")
    cap = html.escape(f"Diagram illustrating {card.term}.")
    return (
        '<figure class="rh-concept-figure">\n'
        f'  <img src="{src}" alt="{alt}" />\n'
        f"  <figcaption>{cap}</figcaption>\n"
        "</figure>"
    )


def _render_backlinks_section(backlinks: Sequence[Backlink]) -> str:
    if not backlinks:
        return "## Referenced By\n\n_No references yet._"
    lines = [f"- **{b.kind}:** [{b.label}]({b.md_href})" for b in backlinks]
    return "## Referenced By\n\n" + "\n".join(lines)


def _render_related_section(card: ConceptCard, ctx: RenderContext) -> str:
    if not card.child_concepts:
        return ""
    rel = [
        f"- [{ctx.registry[s].term}](/concepts/{s}) — {ctx.registry[s].inline_glimpse}"
        for s in card.child_concepts
    ]
    return "## Related Concepts\n\n" + "\n".join(rel)


def _render_sources_section(card: ConceptCard) -> str:
    if not card.sources:
        return ""
    srcs = [f"- **{s.title}** (*{s.kind}*) — {s.citation}" for s in card.sources]
    return "## Authoritative Sources\n\n" + "\n".join(srcs)


def _concept_footer_sections(
    card: ConceptCard, body_refs: tuple[str, ...], backlinks: Sequence[Backlink], ctx: RenderContext
) -> list[str]:
    other = [slug for slug in body_refs if slug != card.slug]
    candidates = (
        _render_concept_anchors(card, ctx),
        _render_related_section(card, ctx),
        _render_backlinks_section(backlinks),
        _render_sources_section(card),
        render_page_glossary(other, ctx.registry, base_url=ctx.base_url),
    )
    return [part for part in candidates if part]


def _strip_leading_h1(body: str) -> str:
    stripped = body.strip()
    lines = stripped.split("\n")
    if lines and _H1_RE.match(lines[0]):
        return "\n".join(lines[1:]).strip()
    return stripped


def render_concept_page(
    card: ConceptCard,
    expanded_body: str,
    body_refs: tuple[str, ...],
    backlinks: Sequence[Backlink],
    ctx: RenderContext,
) -> str:
    """Render one complete Concept Card VitePress Markdown page with backlinks and Lean anchors."""
    sections: list[str] = [f"# {card.term}", ctx.banner_tag]
    if card.aliases:
        sections.append(f"*Also known as: {', '.join(card.aliases)}.*")
    sections.append(f"> {card.inline_glimpse}")
    if figure := _render_concept_figure(card):
        sections.append(figure)
    if cleaned_body := _strip_leading_h1(expanded_body):
        sections.append(cleaned_body)
    sections.append(
        "## Where this analogy breaks\n\n"
        f'<div class="rh-analogy-breaks">\n\n{card.where_analogy_breaks}\n\n</div>'
    )
    sections.extend(_concept_footer_sections(card, body_refs, backlinks, ctx))
    return "\n\n".join(sections) + "\n"


def render_concepts_index(ctx: RenderContext) -> str:
    """Render the alphabetical Concept Index page (`concepts/index.md`)."""
    cards = sorted(ctx.registry.values(), key=lambda c: (c.term.lower(), c.slug))
    rows = [f"- [{c.term}](/concepts/{c.slug}) — {c.inline_glimpse}" for c in cards]
    return "\n\n".join(["# Concept Index", ctx.banner_tag, "\n".join(rows)]) + "\n"


def _stage_page_sort_key(item: tuple[str, str]) -> tuple[int, str]:
    stem = item[0]
    if stem == "overview":
        return (0, stem)
    if stem == "proof-journey":
        return (1, stem)
    return (2, stem)


def _render_stage_roadmap_entry(
    stage: StageDefinition,
    node_count: int,
    published_pages: Sequence[tuple[str, str]],
) -> str:
    lines = [f"### Stage {stage.order:02d} — {stage.title}"]
    if stage.summary:
        lines.extend(("", stage.summary))
    lines.extend(("", f"- **Extracted landmarks:** {node_count}"))
    if published_pages:
        links = " · ".join(
            f"[{title}](/stages/{stage.stage_id}/{stem})"
            for stem, title in sorted(published_pages, key=_stage_page_sort_key)
        )
        lines.append(f"- **Published pages:** {links}")
    else:
        status = "Skeleton extracted — essay upcoming" if node_count > 0 else "Upcoming"
        lines.append(f"- **Status:** {status}")
    return "\n".join(lines)


def _render_proof_roadmap(stage_titles: Mapping[tuple[str, str], str], ctx: RenderContext) -> str:
    node_counts: dict[str, int] = {}
    for node in ctx.dag.nodes:
        node_counts[node.stage_id] = node_counts.get(node.stage_id, 0) + 1
    pages_by_stage: dict[str, list[tuple[str, str]]] = {}
    for (stage_id, stem), title in sorted(stage_titles.items()):
        pages_by_stage.setdefault(stage_id, []).append((stem, title))
    ordered_stages = sorted(ctx.dag.stages, key=lambda s: (s.order, s.stage_id))
    entries = [
        _render_stage_roadmap_entry(
            stage,
            node_counts.get(stage.stage_id, 0),
            pages_by_stage.get(stage.stage_id, ()),
        )
        for stage in ordered_stages
    ]
    return "## 8-Pillar Proof Roadmap\n\n" + "\n\n".join(entries)


def render_landing_index(stage_titles: Mapping[tuple[str, str], str], ctx: RenderContext) -> str:
    """Render `index.md` with intro links, 8-pillar roadmap, mini-map, concepts, and provenance."""
    has_journey = ("00_statement", "proof-journey") in stage_titles
    excluded_landmarks = {("00_statement", "overview"), ("00_statement", "proof-journey")}
    page_links = [
        f"- [{stage_titles[k]}](/stages/{k[0]}/{k[1]})"
        for k in sorted(stage_titles)
        if k not in excluded_landmarks
    ]
    cards = sorted(ctx.registry.values(), key=lambda c: (c.term.lower(), c.slug))
    concept_links = [f"- [{c.term}](/concepts/{c.slug})" for c in cards]
    upstream_tree = f"{UPSTREAM_REPO_URL}/tree/{ctx.dag.upstream_commit}"
    intro = (
        "Start with the [Stage 00 Overview](/stages/00_statement/overview) for the statement and "
        "entry-point architecture of the formalization."
    )
    if has_journey:
        intro += (
            " Explore the [Full Proof Journey & Alpöge's 20-Step Map]"
            "(/stages/00_statement/proof-journey) for the complete 8-pillar architecture."
        )
    sections = ["# Riemann Hypothesis Formal Proof Skeleton", ctx.banner_tag, intro]
    if ctx.dag.stages:
        sections.append(_render_proof_roadmap(stage_titles, ctx))
    if page_links:
        sections.append("## Landmark Pages\n\n" + "\n".join(page_links))
    sections.append("## Stage 00 Dependency Map\n\n" + render_stage_minimap("00_statement", ctx))
    sections.append(
        f"## Concept Index\n\nBrowse all {len(cards)} canonical concepts in the "
        f"[Concept Index](/concepts/).\n\n" + "\n".join(concept_links)
    )
    sections.append(
        "## Upstream Provenance\n\n"
        f"- [Upstream repository at `{ctx.dag.upstream_commit[:12]}`]({upstream_tree})"
    )
    return "\n\n".join(sections) + "\n"
