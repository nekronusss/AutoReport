#!/usr/bin/env bash
set -euo pipefail
environment=''
release=''
while (($#)); do
  case "$1" in
    --environment) environment="$2"; shift 2;;
    --release) release="$2"; shift 2;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done
[[ "$environment" == production && "$release" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || exit 2
: "${DQFLOW_IMAGE:?Set immutable registry image}"
[[ "$DQFLOW_IMAGE" =~ @sha256:[a-f0-9]{64}$ ]] || { echo 'Digest required'; exit 2; }
export DQFLOW_RELEASE_DIR="/opt/dqflow/releases/$release"
cd "$DQFLOW_RELEASE_DIR"
test -r /etc/dqflow/config.yaml
test -r /etc/dqflow/secrets/db_runner_password
ops/compose.sh config --quiet
ops/compose.sh pull
ops/backup.sh
exec 9>/run/lock/dqflow-maintenance.lock
flock -x 9
ops/compose.sh run --rm migrator migrate
ops/compose.sh --profile monitoring up -d --wait postgres report-web metrics-exporter prometheus alertmanager
ops/compose.sh run --rm runner doctor
curl --fail --silent http://127.0.0.1:8080/health >/dev/null
ln -s "$DQFLOW_RELEASE_DIR" /opt/dqflow/current.next
mv -Tf /opt/dqflow/current.next /opt/dqflow/current
echo "Release $release active"
