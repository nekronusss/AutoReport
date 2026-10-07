#!/usr/bin/env bash
set -euo pipefail
cd "${DQFLOW_RELEASE_DIR:-/opt/dqflow/current}"
exec docker compose --project-name dqflow-prod -f compose.yaml -f compose.prod.yaml "$@"

