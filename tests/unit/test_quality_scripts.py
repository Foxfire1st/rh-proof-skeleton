"""Exercise the worktree quality scripts without installing dependencies in test fixtures."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from textwrap import dedent

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    root = tmp_path / "candidate worktree"
    shutil.copytree(SCRIPTS, root / "scripts")
    (root / "src").mkdir()
    return root


def run_script(worktree: Path, name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(worktree / "scripts" / name), *args],
        cwd=worktree / "src",
        capture_output=True,
        text=True,
        check=False,
    )


def write_package(source: Path, body: str = "") -> None:
    package = source / "rh_pipeline"
    (package / "validate").mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "validate/file_size.py").write_text(body, encoding="utf-8")


def install_fixture_venv(worktree: Path, source: Path) -> Path:
    environment = worktree / ".venv"
    venv.create(environment, with_pip=False, symlinks=True)
    site_packages = next(environment.glob("lib/python*/site-packages"))
    (site_packages / "fixture.pth").write_text(f"{source}\n", encoding="utf-8")
    metadata = site_packages / "rh_pipeline-0.1.0.dist-info"
    metadata.mkdir()
    (metadata / "direct_url.json").write_text(
        f'{{"url": "{source.parent.as_uri()}", "dir_info": {{"editable": true}}}}\n',
        encoding="utf-8",
    )
    return environment / "bin"


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{dedent(body).lstrip()}", encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("script", ["bootstrap-venv.sh", "check-quality.sh"])
def test_unknown_flag_reports_usage(worktree: Path, script: str) -> None:
    result = run_script(worktree, script, "--unknown")
    assert result.returncode != 0
    assert "Usage:" in result.stderr
    assert not (worktree / ".venv").exists()


def test_quality_rejects_missing_venv(worktree: Path) -> None:
    result = run_script(worktree, "check-quality.sh")
    assert result.returncode != 0
    assert "Missing or invalid environment" in result.stderr
    assert "scripts/bootstrap-venv.sh --replace" in result.stderr


@pytest.mark.parametrize("script", ["bootstrap-venv.sh", "check-quality.sh"])
def test_wrong_checkout_rejected_even_with_candidate_pythonpath(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    foreign_source = tmp_path / "foreign checkout" / "src"
    write_package(foreign_source)
    write_package(worktree / "src")
    install_fixture_venv(worktree, foreign_source)
    monkeypatch.setenv("PYTHONPATH", str(worktree / "src"))

    result = run_script(worktree, script)
    assert result.returncode != 0
    assert str(foreign_source / "rh_pipeline/__init__.py") in result.stderr
    assert "scripts/bootstrap-venv.sh --replace" in result.stderr
    assert (worktree / ".venv/bin/python").is_file()


def test_bootstrap_rejects_python_older_than_312(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = tmp_path / "old-python"
    write_executable(
        python,
        r"""
        if [[ $1 == -I && $2 == -c && $3 == *'sys.version_info < (3, 12)'* ]]; then
            exit 1
        fi
        exit 93
        """,
    )
    monkeypatch.setenv("PYTHON_BIN", str(python))
    result = run_script(worktree, "bootstrap-venv.sh")
    assert result.returncode == 1
    assert f"Python >=3.12 is required. Checked: {python}" in result.stderr
    assert not (worktree / ".venv").exists()


def test_bootstrap_removes_partial_venv_after_pip_failure(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = tmp_path / "python"
    write_executable(
        python,
        r"""
        case "$*" in
            '-I -c '*) exit 0 ;;
            '-I -m ensurepip --version') exit 1 ;;
            '-I -m venv --without-pip '*) touch "$5/partial" ;;
            '-I -m pip --python '*) exit 42 ;;
            *) exit 93 ;;
        esac
        """,
    )
    monkeypatch.setenv("PYTHON_BIN", str(python))
    result = run_script(worktree, "bootstrap-venv.sh")
    assert result.returncode == 42
    assert f"with {python}" in result.stderr
    assert not (worktree / ".venv").exists()


def test_quality_rejects_noneditable_install(worktree: Path) -> None:
    write_package(worktree / "src")
    install_fixture_venv(worktree, worktree / "src")
    provenance = next((worktree / ".venv").rglob("direct_url.json"))
    provenance.write_text('{"dir_info": {}}', encoding="utf-8")

    result = run_script(worktree, "check-quality.sh")
    assert result.returncode != 0
    assert "Expected an editable rh-pipeline install" in result.stderr


@pytest.fixture
def toolchain(worktree: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    write_package(
        worktree / "src",
        'from pathlib import Path\nPath("trace").open("a").write("file_size\\n")\n',
    )
    bin_dir = install_fixture_venv(worktree, worktree / "src")
    for tool in ("ruff", "pyright", "radon", "pytest"):
        write_executable(
            bin_dir / tool,
            r"""
            printf '%s %s\n' "${0##*/}" "$*" >> trace
            """,
        )
    shadow = worktree.parent / "shadow"
    write_package(shadow, "raise SystemExit('wrong checkout used')\n")
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    return bin_dir, worktree / "trace"


def test_quality_runs_worktree_tools_in_ci_order(
    worktree: Path, toolchain: tuple[Path, Path]
) -> None:
    bin_dir, trace = toolchain
    write_executable(
        bin_dir / "radon",
        r"""
        printf 'radon %s\n' "$*" >> trace
        printf 'src/example.py\n    F 1:0 example - B (7)\n'
        """,
    )
    reuse = run_script(worktree, "bootstrap-venv.sh")
    assert reuse.returncode == 0, reuse.stderr
    assert not trace.exists()
    result = run_script(worktree, "check-quality.sh")
    assert result.returncode == 0, result.stderr
    assert str(worktree / "src/rh_pipeline/__init__.py") in result.stdout
    assert "example - B (7)" in result.stdout
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "ruff check .",
        "ruff format --check .",
        "pyright ",
        "radon cc src -s -a -n B --output-file=",
        "file_size",
        "pytest ",
    ]


def test_quality_rejects_radon_syntax_error_even_with_zero_status(
    worktree: Path, toolchain: tuple[Path, Path]
) -> None:
    bin_dir, trace = toolchain
    write_executable(
        bin_dir / "radon",
        r"""
        printf 'src/example.py\n    ERROR: invalid syntax (<unknown>, line 1)\n'
        """,
    )
    result = run_script(worktree, "check-quality.sh")
    assert result.returncode != 0
    assert "ERROR: invalid syntax" in result.stdout
    assert "file_size" not in trace.read_text(encoding="utf-8")


@pytest.mark.parametrize("script", ["bootstrap-venv.sh", "check-quality.sh"])
def test_worktree_root_is_independent_of_cdpath(
    worktree: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    monkeypatch.setenv("CDPATH", ".")
    (worktree / ".venv").mkdir()
    for cwd, command in (
        (worktree, Path("scripts") / script),
        (worktree / "src", Path("../scripts") / script),
        (worktree / "src", worktree / "scripts" / script),
    ):
        result = subprocess.run(
            [str(command)], cwd=cwd, capture_output=True, text=True, check=False
        )
        assert result.returncode == 1
        assert f"Missing or invalid environment at {worktree}/.venv." in result.stderr


@pytest.mark.parametrize("ensurepip_status", [0, 1])
def test_bootstrap_confines_pip_destination(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ensurepip_status: int
) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    config = tmp_path / "pip.conf"
    config.write_text(f"[install]\ntarget = {foreign}\n", encoding="utf-8")
    wheelhouse = tmp_path / "wheelhouse"
    python = tmp_path / "bootstrap-python"
    write_executable(
        python,
        r"""
        set -eu
        case "$*" in
            '-I -c '*) exit 0 ;;
            '-I -m ensurepip --version') exit "$ENSUREPIP_STATUS" ;;
            '-I -m venv '*)
                bin="${!#}/bin"
                mkdir -p "$bin"
                cp "$0" "$bin/python"
                for tool in ruff pyright radon pytest; do
                    cp "$0" "$bin/$tool"
                done
                ;;
            '-I -m pip '*)
                [[ -z ${PIP_TARGET+x}${PIP_PREFIX+x}${PIP_ROOT+x}${PIP_USER+x}${PIP_PYTHON+x} ]]
                [[ $PIP_CONFIG_FILE == /dev/null && $PIP_FIND_LINKS == "$EXPECTED_WHEELHOUSE" ]]
                printf '%s\n' "$*" > install-command
                ;;
            '-I - '*) printf '%s/src/rh_pipeline/__init__.py\n' "$3" ;;
            *) exit 93 ;;
        esac
        """,
    )
    for variable in ("PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_PYTHON"):
        monkeypatch.setenv(variable, str(foreign))
    monkeypatch.setenv("PIP_USER", "1")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(config))
    monkeypatch.setenv("PIP_FIND_LINKS", str(wheelhouse))
    monkeypatch.setenv("EXPECTED_WHEELHOUSE", str(wheelhouse))
    monkeypatch.setenv("PYTHON_BIN", str(python))
    monkeypatch.setenv("ENSUREPIP_STATUS", str(ensurepip_status))

    result = run_script(worktree, "bootstrap-venv.sh")
    assert result.returncode == 0, result.stderr
    invocation = (worktree / "install-command").read_text(encoding="utf-8").strip()
    expected = "-I -m pip"
    if ensurepip_status:
        expected += f" --python {worktree / '.venv/bin/python'}"
    assert invocation == f"{expected} install -e .[dev]"
    assert list(foreign.iterdir()) == []


def test_quality_keeps_radon_report_on_stdout(
    worktree: Path, toolchain: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, trace = toolchain
    real_radon = Path(sys.executable).parent / "radon"
    write_executable(bin_dir / "radon", f'exec {shlex.quote(str(real_radon))} "$@"\n')
    (worktree / "src/example.py").write_text(
        "def grade_b(value):\n"
        + "".join(f"    if value == {i}:\n        return {i}\n" for i in range(6)),
        encoding="utf-8",
    )
    report = tmp_path / "radon-report.txt"
    report.write_text("existing report", encoding="utf-8")
    config = tmp_path / "radon.cfg"
    config.write_text(f"[radon]\noutput_file = {report}\n", encoding="utf-8")
    monkeypatch.setenv("RADONCFG", str(config))

    result = run_script(worktree, "check-quality.sh")
    assert result.returncode == 0, result.stderr
    assert "grade_b - B (7)" in result.stdout
    assert report.read_text(encoding="utf-8") == "existing report"
    assert "pytest" in trace.read_text(encoding="utf-8")


@pytest.mark.parametrize("alias_kind", ["checkout", "launcher"])
def test_bootstrap_replaces_venv_selected_through_symlink(
    worktree: Path,
    toolchain: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
) -> None:
    bin_dir, _ = toolchain
    marker = worktree / ".venv/old-environment"
    marker.touch()
    site_packages = next((worktree / ".venv").glob("lib/python*/site-packages"))
    seed = tmp_path / "install-seed"
    shutil.copytree(site_packages, seed / "site-packages")
    for tool in ("ruff", "pyright", "radon", "pytest"):
        shutil.copy2(bin_dir / tool, seed)

    # Exercise real Python and venv creation without fetching dev dependencies.
    shim = tmp_path / "bin"
    shim.mkdir()
    write_executable(
        shim / "env",
        r"""
        set -eu
        site=(.venv/lib/python*/site-packages)
        cp -R "$INSTALL_SEED/site-packages/." "${site[0]}"
        for tool in ruff pyright radon pytest; do
            cp "$INSTALL_SEED/$tool" ".venv/bin/$tool"
        done
        """,
    )
    monkeypatch.setenv("PATH", str(shim), prepend=":")
    monkeypatch.setenv("INSTALL_SEED", str(seed))
    alias = tmp_path / "python-alias"
    if alias_kind == "checkout":
        alias.symlink_to(worktree, target_is_directory=True)
        selected = alias / ".venv/bin/python"
    else:
        alias.symlink_to(bin_dir / "python")
        selected = alias
    monkeypatch.setenv("PYTHON_BIN", str(selected))

    result = run_script(worktree, "bootstrap-venv.sh", "--replace")
    assert result.returncode == 0, result.stderr
    assert str(worktree / "src/rh_pipeline/__init__.py") in result.stdout
    assert not marker.exists()
