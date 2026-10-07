#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${DQFLOW_BACKUP_DIR:=/srv/dqflow-backups}"
: "${DQFLOW_BACKUP_REMOTE:?Set an rclone remote path on another host}"
: "${DQFLOW_AGE_RECIPIENT_FILE:?Set age public recipient file}"
command -v age >/dev/null
command -v rclone >/dev/null
ops_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
exec 9>/run/lock/dqflow-maintenance.lock
flock -x 9
"$ops_dir/compose.sh" run --rm --entrypoint python runner -c 'from dqflow.settings import load; c=load(); assert c.database.host == "postgres", "backup.sh supports the local postgres service only; configure and verify an external-DB backup adapter first"'
# All writing systemd jobs use this same lock. Manual writes must be paused.
backup_id="$(date -u +%Y%m%dT%H%M%SZ)"
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
mkdir -p -- "$DQFLOW_BACKUP_DIR"
"$ops_dir/compose.sh" exec -T postgres pg_dump -U postgres -d dqflow -Fc > "$work_dir/database.dump"
"$ops_dir/compose.sh" exec -T postgres pg_dumpall -U postgres --roles-only > "$work_dir/roles.sql"
cp -- /etc/dqflow/config.yaml "$work_dir/config.yaml"
cp -- "${DQFLOW_RELEASE_DIR:-/opt/dqflow/current}/uv.lock" "$work_dir/uv.lock"
printf '%s\n' "${DQFLOW_IMAGE:?Set image digest}" > "$work_dir/image.txt"
# Keep application paths stable after restore; include raw, reports and dbt evidence.
tar -C /srv/dqflow -cf "$work_dir/files.tar" raw artifacts
(cd "$work_dir" && sha256sum database.dump roles.sql config.yaml uv.lock image.txt files.tar > SHA256SUMS)
tar -C "$work_dir" -cf - . | age -R "$DQFLOW_AGE_RECIPIENT_FILE" -o "$DQFLOW_BACKUP_DIR/$backup_id.tar.age"
rclone copyto -- "$DQFLOW_BACKUP_DIR/$backup_id.tar.age" "$DQFLOW_BACKUP_REMOTE/$backup_id.tar.age"
rclone copyto -- "$DQFLOW_BACKUP_REMOTE/$backup_id.tar.age" "$work_dir/roundtrip.age"
cmp -- "$DQFLOW_BACKUP_DIR/$backup_id.tar.age" "$work_dir/roundtrip.age"
"$ops_dir/compose.sh" run --rm runner backup-confirmed --reference "$backup_id"
