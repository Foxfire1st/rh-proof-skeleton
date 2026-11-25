#!/usr/bin/env bash
set -euo pipefail

# Bootstrap uses the same preflight for a fresh environment and for reuse.
verify_venv() {
    local root=$1
    local bin="$root/.venv/bin"
    local tool
    local repair='Run scripts/bootstrap-venv.sh --replace from the worktree root.'

    if [[ ! -d "$root/.venv" || -L "$root/.venv" || ! -x "$bin/python" ]]; then
        printf 'Missing or invalid environment at %s/.venv. %s\n' "$root" "$repair" >&2
        return 1
    fi

    if ! "$bin/python" -I - "$root" <<'PY'
import json
import sys
from importlib.metadata import distribution
from pathlib import Path

import rh_pipeline

root = Path(sys.argv[1])
package_file = Path(rh_pipeline.__file__).resolve()
if (
    sys.version_info < (3, 12)
    or Path(sys.prefix).resolve() != root / ".venv"
    or not package_file.is_relative_to(root / "src/rh_pipeline")
):
    sys.exit(f"Expected a worktree-local Python >=3.12 and package, found {package_file}")

provenance = distribution("rh-pipeline").read_text("direct_url.json")
if not provenance or not json.loads(provenance).get("dir_info", {}).get("editable"):
    sys.exit(f"Expected an editable rh-pipeline install, found {package_file}")

print(package_file)
PY
    then
        printf 'Cannot verify the environment at %s/.venv. %s\n' "$root" "$repair" >&2
        return 1
    fi

    for tool in ruff pyright radon pytest rh-validate; do
        if [[ ! -x "$bin/$tool" || ! -f "$bin/$tool" ]]; then
            printf 'Missing executable %s. %s\n' "$bin/$tool" "$repair" >&2
            return 1
        fi
    done
}

check_quality() {
    local root
    root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
    cd -- "$root"
    verify_venv "$root"

    local bin="$root/.venv/bin"
    unset PYTHONPATH PYTHONHOME
    export PATH="$bin:$PATH"
    "$bin/ruff" check .
    "$bin/ruff" format --check .
    "$bin/pyright"

    # Keep the report on stdout regardless of config, and recognize Radon's inline errors.
    local radon_output radon_status=0
    radon_output=$(COLOR=no "$bin/radon" cc src -s -a -n B --output-file= 2>&1) || radon_status=$?
    printf '%s\n' "$radon_output"
    if (( radon_status != 0 )); then
        return "$radon_status"
    fi
    if [[ "$radon_output" =~ (^|$'\n')[[:blank:]]+ERROR: ]]; then
        return 1
    fi

    "$bin/python" -m rh_pipeline.validate.file_size
    if [[ -n "${RH_MEMORY_ROOT:-}" ]]; then
        "$bin/rh-validate" --repo . --memory "$RH_MEMORY_ROOT"
    else
        "$bin/rh-validate" --repo .
    fi
    "$bin/pytest"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    if (( $# != 0 )); then
        printf 'Usage: %s\n' "$0" >&2
        exit 2
    fi
    check_quality
fi
