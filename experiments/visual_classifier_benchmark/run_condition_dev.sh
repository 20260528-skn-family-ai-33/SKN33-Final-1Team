#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec bash run_structure_dev.sh "$@" --experiment conditions-v1
