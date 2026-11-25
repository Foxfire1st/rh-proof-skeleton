"""Unit tests for the deterministic file-size gate."""

from __future__ import annotations

from pathlib import Path

from pytest import CaptureFixture

from rh_pipeline.validate.file_size import classify_line_count, main, measure_tree


def test_classify_line_count_bands() -> None:
    assert classify_line_count(0) == "healthy"
    assert classify_line_count(300) == "healthy"
    assert classify_line_count(301) == "watchful"
    assert classify_line_count(600) == "watchful"
    assert classify_line_count(601) == "refactor-pressure"
    assert classify_line_count(900) == "refactor-pressure"
    assert classify_line_count(901) == "soft-limit-exceeded"
    assert classify_line_count(1200) == "soft-limit-exceeded"
    assert classify_line_count(1201) == "hard-limit-exceeded"


def test_measure_tree_detects_hard_limit_violation(tmp_path: Path) -> None:
    ok_file = tmp_path / "ok.py"
    ok_file.write_text("x = 1\n" * 10, encoding="utf-8")
    too_big = tmp_path / "too_big.py"
    too_big.write_text("x = 1\n" * 1201, encoding="utf-8")

    findings = measure_tree((tmp_path,))
    assert [item.path.name for item in findings] == ["ok.py", "too_big.py"]
    assert not findings[0].exceeds_hard_limit
    assert findings[1].exceeds_hard_limit
    assert main([str(tmp_path)]) == 1


def test_cli_reports_watchful_file_without_failing(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    watchful_file = tmp_path / "watchful.py"
    watchful_file.write_text("x = 1\n" * 450, encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert f"watchful: {watchful_file} (450 lines)" in capsys.readouterr().out
