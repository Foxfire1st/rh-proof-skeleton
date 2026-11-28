"""Golden unit tests for neutral low-level markdown masking primitives."""

from __future__ import annotations

import rh_pipeline.markdown_mask as markdown_mask
from rh_pipeline.markdown_mask import (
    find_directive_close,
    find_math_end,
    mask_code_spans,
    mask_fenced_code,
    mask_inline_code,
)
from rh_pipeline.site.markdown_transforms import (
    mask_code_spans as site_mask_code_spans,
)
from rh_pipeline.site.markdown_transforms import (
    mask_fenced_code as site_mask_fenced_code,
)


def test_reexports_and_neutral_module_boundary() -> None:
    assert site_mask_code_spans is mask_code_spans
    assert site_mask_fenced_code is mask_fenced_code
    assert not hasattr(markdown_mask, "ConceptCard")
    assert not hasattr(markdown_mask, "SiteBuildError")


def test_crlf_fenced_and_inline_code_preserve_exact_lengths_and_offsets() -> None:
    text = "Intro `code $x$` end.\r\n```lean\r\ndef f := $x$\r\n```\r\nAfter `span` and text.\r\n"
    masked = mask_code_spans(text)
    assert len(masked) == len(text)
    assert masked.count("\r\n") == text.count("\r\n")
    assert [i for i, ch in enumerate(masked) if ch == "\n"] == [
        i for i, ch in enumerate(text) if ch == "\n"
    ]
    assert "def f" not in masked
    assert "$x$" not in masked
    assert masked.endswith("After        and text.\r\n")
    assert mask_fenced_code(text).startswith("Intro `code $x$` end.\r\n")


def test_nested_and_odd_backtick_runs_and_paragraph_break() -> None:
    nested = "A ``code with `single` inside`` and ```three `` inside``` done."
    masked = mask_inline_code(nested)
    assert len(masked) == len(nested)
    assert "single" not in masked
    assert "three" not in masked
    assert masked.startswith("A ") and masked.endswith(" done.")

    odd_unmatched = "Unmatched ` single tick stays literal.\n\nNext `paired` masked."
    masked_odd = mask_inline_code(odd_unmatched)
    assert len(masked_odd) == len(odd_unmatched)
    assert "single tick stays literal." in masked_odd
    assert "paired" not in masked_odd

    crlf_para = "Open `tick\r\n\r\nclose `tick` here."
    masked_para = mask_inline_code(crlf_para)
    assert len(masked_para) == len(crlf_para)
    assert masked_para.startswith("Open `tick\r\n\r\nclose ")
    assert masked_para.endswith(" here.")


def test_math_inside_inline_code_and_directives_inside_math() -> None:
    with_code = "Use `$not math$` then $real {{concept:a|b}}$ after."
    masked = mask_code_spans(with_code)
    assert len(masked) == len(with_code)
    first_dollar = masked.index("$")
    assert with_code[first_dollar : first_dollar + 5] == "$real"
    math_end = find_math_end(masked, first_dollar)
    assert math_end is not None
    assert with_code[first_dollar:math_end] == "$real {{concept:a|b}}$"

    display = "Before $$\\frac{1}{2} {{term:a|b}}$$ end."
    d_start = display.index("$$")
    d_end = find_math_end(display, d_start)
    assert d_end is not None
    assert display[d_start:d_end] == "$$\\frac{1}{2} {{term:a|b}}$$"


def test_directive_close_with_braces_math_and_escaped_chars() -> None:
    sample = "{{concept:slug|prose with {nested {braces}} and $\\frac{1}{n^{s}}$ and \\} end}} tail"
    start = len("{{concept:")
    close = find_directive_close(sample, start)
    assert close is not None
    assert sample[close : close + 2] == "}}"
    assert sample[close + 2 :] == " tail"

    term_braces = "{{term:slug|set {x}}} next"
    t_close = find_directive_close(term_braces, len("{{term:"))
    assert t_close is not None
    assert term_braces[len("{{term:") : t_close] == "slug|set {x}"
    assert term_braces[t_close + 2 :] == " next"
