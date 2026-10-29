#!/usr/bin/env bash
# Format the code and sort the imports, with ruff (it does what black and isort do).
# ruff comes with the dev extras: pip install -e '.[dev]'. Its settings are in pyproject.toml.
#
#   scripts/format.sh                     format everything
#   scripts/format.sh outpost/ssh         format only these files or folders
#   scripts/format.sh --check [files]     change nothing; exit 1 if something is not formatted
set -euo pipefail
cd "$(dirname "$0")/.."

check=false
if [ "${1:-}" = "--check" ]; then
    check=true
    shift
fi
files=("$@")
[ ${#files[@]} -gt 0 ] || files=(outpost tests)

if $check; then
    ruff check --select I "${files[@]}"
    ruff format --check "${files[@]}"
else
    ruff check --select I --fix "${files[@]}"
    ruff format "${files[@]}"
fi
