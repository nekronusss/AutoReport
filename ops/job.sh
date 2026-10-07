#!/usr/bin/env bash
set -euo pipefail
exec 9>/run/lock/dqflow-maintenance.lock
flock -s 9
exec /opt/dqflow/current/ops/compose.sh run --rm runner "$@"

