#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")"

echo "Running local validation for $(basename "$PWD")..."

pdm run black --check src tests
pdm run mypy src tests
pdm run pytest -q

echo "All local checks passed!"
