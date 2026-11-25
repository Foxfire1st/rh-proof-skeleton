"""Phase 4 static site compiler targeting VitePress."""

from rh_pipeline.site.compiler import (
    SiteBuildConfig,
    SiteBuildError,
    SiteBuildReport,
    compile_site,
)
from rh_pipeline.site.markdown_transforms import (
    SiteDirectiveError,
    expand_concept_directives,
    normalize_base_url,
    render_page_glossary,
    split_zoom_levels,
)

__all__ = [
    "SiteBuildConfig",
    "SiteBuildError",
    "SiteBuildReport",
    "SiteDirectiveError",
    "compile_site",
    "expand_concept_directives",
    "normalize_base_url",
    "render_page_glossary",
    "split_zoom_levels",
]
