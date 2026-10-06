from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
import shutil

from psycopg import sql
from psycopg.types.json import Jsonb
from dqflow.db import connect, dataset_lock, candidate


def backup_confirmed(cfg, reference):
    if not reference.strip():
        raise ValueError("Verified external backup reference is required")
    with connect(cfg) as conn:
        conn.execute(
            "INSERT INTO meta.service_health(name,last_seen,details) VALUES ('external_backup',now(),%s) ON CONFLICT(name) DO UPDATE SET last_seen=now(),details=excluded.details",
            (Jsonb({"reference": reference}),),
        )
    return {"state": "BACKUP_CONFIRMED", "reference": reference}


def cleanup(cfg):
    removed = []
    with dataset_lock(cfg), connect(cfg) as conn:
        backup = conn.execute(
            "SELECT last_seen FROM meta.service_health WHERE name='external_backup'"
        ).fetchone()
        if not backup or datetime.now(timezone.utc) - backup["last_seen"] > timedelta(hours=26):
            raise ValueError("Cleanup requires verified external backup newer than 26 hours")
        # Published versions are retained, including rollback targets. Only failed, unreferenced runs expire.
        runs = conn.execute(
            """SELECT r.* FROM meta.runs r WHERE dataset=%s AND state IN ('FAILED_DQ','FAILED_SOURCE','FAILED_TECH','CANCELLED')
          AND created_at<now()-(%s*interval '1 day')
          AND NOT EXISTS(SELECT 1 FROM meta.publications p WHERE p.run_id=r.run_id)
          AND NOT EXISTS(SELECT 1 FROM mart.daily_summary_versions m WHERE m.run_id=r.run_id)
          AND NOT EXISTS(SELECT 1 FROM meta.outbox o WHERE o.run_id=r.run_id AND status='PENDING')""",
            (cfg.dataset, cfg.pipeline.raw_retention_days),
        ).fetchall()
        for run in runs:
            rid = str(UUID(str(run["run_id"])))
            raw_root = Path(cfg.artifacts.raw).resolve()
            target = (raw_root / rid).resolve()
            if target.parent != raw_root:
                raise ValueError("Unsafe cleanup path")
            with conn.transaction():
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(candidate(rid)))
                )
                conn.execute("DELETE FROM raw.records WHERE run_id=%s", (rid,))
                conn.execute("DELETE FROM meta.rejected_records WHERE run_id=%s", (rid,))
                conn.execute("DELETE FROM meta.source_batches WHERE run_id=%s", (rid,))
                conn.execute(
                    "INSERT INTO meta.audit_events(run_id,action,reason) VALUES (%s,'cleanup','Expired failed raw after verified backup')",
                    (rid,),
                )
            if target.exists():
                shutil.rmtree(target)
            removed.append(rid)
    return {
        "removed_failed_runs_raw": removed,
        "policy": "Verified versions retained for explicit rollback; metadata retained",
    }
