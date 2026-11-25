#!/usr/bin/env bash
set -euo pipefail

replace=false
if [[ $# == 1 && $1 == --replace ]]; then
    replace=true
elif (( $# != 0 )); then
    printf 'Usage: %s [--replace]\n' "$0" >&2
    exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source "$root/scripts/check-quality.sh"

if [[ "$replace" == false && ( -e "$root/.venv" || -L "$root/.venv" ) ]]; then
    verify_venv "$root"
    exit 0
fi

if [[ -v PYTHON_BIN ]]; then
    python=$PYTHON_BIN
elif command -v python3.12 >/dev/null; then
    python=python3.12
else
    python=python3
fi

# Resolve caller-relative overrides before changing to the worktree root.
if ! python_path=$(type -P -- "$python") ||
    ! "$python_path" -I -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    printf 'Python >=3.12 is required. Checked: %s\n' "$python" >&2
    exit 1
fi
if [[ "$python_path" != /* ]]; then
    python_path="$PWD/$python_path"
fi

if [[ "$replace" == true ]]; then
    # An activated worktree can select the interpreter we are about to remove.
    python_path=$("$python_path" -I - "$root/.venv" <<'PYTHON'
import sys
from pathlib import Path

selected = Path(sys.executable)
replaced = Path(sys.argv[1])
launcher = selected
while True:
    # Inspect the directory entry before following the launcher's own symlink.
    physical = launcher.parent.resolve() / launcher.name
    if launcher.is_relative_to(replaced) or physical.is_relative_to(replaced):
        selected = Path(sys._base_executable).resolve()
        break
    if not physical.is_symlink():
        break
    launcher = physical.parent / physical.readlink()
print(selected)
PYTHON
    )
    if [[ ! -x "$python_path" || "$python_path" == "$root/.venv/"* ]]; then
        printf 'Cannot replace .venv without an executable Python outside it. Checked: %s\n' \
            "$python_path" >&2
        exit 1
    fi
fi

cd -- "$root"
if [[ "$replace" == true ]]; then
    rm -rf -- .venv
fi
mkdir -- .venv

cleanup() {
    local status=$?
    if (( status != 0 )); then
        rm -rf -- .venv
        printf 'Could not bootstrap %s/.venv with %s.\n' "$root" "$python_path" >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if "$python_path" -I -m ensurepip --version >/dev/null 2>&1; then
    "$python_path" -I -m venv "$root/.venv"
    pip_command=("$root/.venv/bin/python" -I -m pip)
else
    # Some system Python builds ship pip separately and omit ensurepip.
    "$python_path" -I -m venv --without-pip "$root/.venv"
    pip_command=("$python_path" -I -m pip --python "$root/.venv/bin/python")
fi

# Pip's destination can come from its own config and environment, even under python -I.
# Keep fetch settings from the environment while binding installation to this venv.
env -u PIP_TARGET -u PIP_PREFIX -u PIP_ROOT -u PIP_USER -u PIP_PYTHON \
    -u _PIP_RUNNING_IN_SUBPROCESS PIP_CONFIG_FILE=/dev/null \
    "${pip_command[@]}" install -e '.[dev]'

verify_venv "$root"
trap - EXIT INT TERM
