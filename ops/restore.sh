#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${DQFLOW_RESTORE_ROOT:?Set NEW isolated restore root}"
: "${DQFLOW_RESTORE_DB:?Set NEW isolated database name}"
: "${DQFLOW_AGE_IDENTITY_FILE:?Set decryption identity file}"
: "${PGHOST:?Set isolated PostgreSQL host}"
: "${PGUSER:?Set restore PostgreSQL role}"
: "${PGPASSFILE:?Set protected pgpass file}"
archive="${1:?Usage: restore.sh encrypted-backup.tar.age}"
[[ "$DQFLOW_RESTORE_DB" =~ ^dqflow_restore_[a-zA-Z0-9_]+$ ]] || { echo 'Restore database must start dqflow_restore_'; exit 2; }
[[ ! -e "$DQFLOW_RESTORE_ROOT" ]] || { echo 'Restore root must not exist'; exit 2; }
start="$(date +%s)"
mkdir -p -- "$DQFLOW_RESTORE_ROOT"
age -d -i "$DQFLOW_AGE_IDENTITY_FILE" "$archive" | tar -xf - -C "$DQFLOW_RESTORE_ROOT"
(cd "$DQFLOW_RESTORE_ROOT" && sha256sum -c SHA256SUMS)
# Restore cluster roles separately on a clean cluster after reviewing roles.sql.
createdb -- "$DQFLOW_RESTORE_DB"
pg_restore --exit-on-error --no-owner --dbname="$DQFLOW_RESTORE_DB" "$DQFLOW_RESTORE_ROOT/database.dump"
mkdir "$DQFLOW_RESTORE_ROOT/data"
tar -xf "$DQFLOW_RESTORE_ROOT/files.tar" -C "$DQFLOW_RESTORE_ROOT/data"
printf 'Restore duration: %s seconds. Run doctor, verify references and replay in isolation.\n' "$(( $(date +%s)-start ))"

