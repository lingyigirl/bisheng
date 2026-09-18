#!/bin/bash
# Upgrade an existing OpenFGA store to the db_datasource authorization model
# (F061). Idempotent: a store whose model already declares the type is left
# untouched. See the .py docstring for why this is a script and not a migration.
#
# Usage (run from src/backend/):
#   bash scripts/migrate_fga_add_datasource_type.sh --dry-run   # report only (default)
#   bash scripts/migrate_fga_add_datasource_type.sh --apply     # write the new model
#   bash scripts/migrate_fga_add_datasource_type.sh apply       # same as --apply
set -e

export PYTHONPATH="./"
export config="${config:-config.yaml}"

# Windows checkouts keep the venv interpreter under Scripts/ rather than bin/.
if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON_BIN=".venv/Scripts/python.exe"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "Python interpreter not found." >&2
    exit 1
fi

# Translate the convenience token `apply` into `--apply`; forward everything else.
ARGS=()
for arg in "$@"; do
    if [ "$arg" = "apply" ]; then
        ARGS+=("--apply")
    else
        ARGS+=("$arg")
    fi
done

"${PYTHON_BIN}" scripts/migrate_fga_add_datasource_type.py "${ARGS[@]}"
