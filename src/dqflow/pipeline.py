import hashlib
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4, UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from dqflow.connectors import SourceClient, SourceError, QualityError
from dqflow.db import Busy, candidate, connect, dataset_lock
from dqflow.ingestion import ingest
from dqflow.settings import dumps
from dqflow.validation import Checks, validate_input, build_dbt, reconcile_mart


def code_hash():
    digest = hashlib.sha256()
    for root in (Path("src/dqflow"), Path("dbt"), Path("migrations")):
        for path in sorted(root.rglob("*")):
            if path.suffix in (".py", ".sql", ".yml", ".html") and "target" not in path.parts:
                digest.update(str(path).encode())
                digest.update(path.read_bytes())
    for path in (Path("pyproject.toml"), Path("uv.lock")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def log(cfg, run_id, attempt_id, stage, **details):
    print(
        dumps(
            dict(
                timestamp=datetime.now(timezone.utc),
                level="INFO",
                environment=cfg.environment,
                dataset=cfg.dataset,
                run_id=run_id,
                attempt_id=attempt_id,
                stage=stage,
                **details,
            )
        ),
        file=sys.stderr,
    )


@contextmanager
def heartbeat(cfg, run_id):
    stop = threading.Event()

    def beat():
        while not stop.wait(30):
            try:
                with connect(cfg) as conn:
                    conn.execute("UPDATE meta.runs SET heartbeat=now() WHERE run_id=%s", (run_id,))
            except Exception:
                return

    worker = threading.Thread(target=beat, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1)


def set_state(conn, cfg, run_id, attempt_id, state):
    conn.execute("UPDATE meta.runs SET state=%s,heartbeat=now() WHERE run_id=%s", (state, run_id))
    conn.execute("UPDATE meta.attempts SET state=%s WHERE attempt_id=%s", (state, attempt_id))
    log(cfg, run_id, attempt_id, state)


def enqueue(conn, run_id, kind):
    conn.execute(
        "INSERT INTO meta.outbox(event_id,run_id,kind) VALUES (%s,%s,%s) ON CONFLICT(run_id,kind) DO NOTHING",
        (uuid4(), run_id, kind),
    )


def publish(lock, cfg, run_id, day, manifest):
    schema = sql.Identifier(candidate(run_id))
    with lock.transaction():
        existing = lock.execute(
            "SELECT r.as_of,p.run_id FROM meta.publications p JOIN meta.runs r USING(run_id) WHERE p.dataset=%s AND p.period=%s FOR UPDATE OF p",
            (cfg.dataset, day),
        ).fetchone()
        if existing and existing["as_of"] > manifest.as_of:
            raise QualityError("DQ15", "Older as_of cannot replace a newer publication; use rollback")
        for source, target in (
            ("fact_orders", "fact_orders_versions"),
            ("daily_summary", "daily_summary_versions"),
        ):
            lock.execute(
                sql.SQL("INSERT INTO mart.{} SELECT %s,c.* FROM {}.{} c ON CONFLICT DO NOTHING").format(
                    sql.Identifier(target), schema, sql.Identifier(source)
                ),
                (run_id,),
            )
        lock.execute(
            "INSERT INTO meta.publications(dataset,period,run_id) VALUES (%s,%s,%s) ON CONFLICT(dataset,period) DO UPDATE SET run_id=excluded.run_id,published_at=now()",
            (cfg.dataset, day, run_id),
        )
        lock.execute("UPDATE meta.runs SET state='PUBLISHED_REPORT_PENDING' WHERE run_id=%s", (run_id,))
        enqueue(lock, run_id, "report")
        enqueue(lock, run_id, "notification_published")
        lock.execute(
            "INSERT INTO meta.audit_events(run_id,action,reason) VALUES (%s,'publish','All mandatory gates passed')",
            (run_id,),
        )


def run(cfg, day, source="demo", snapshot_id=None, baseline=False):
    if source != "demo" and source != cfg.source.type:
        raise ValueError("source must be demo or configured source type")
    started = time.monotonic()
    run_id, attempt_id = uuid4(), uuid4()
    manifest = None
    client = SourceClient(cfg)
    try:
        with dataset_lock(cfg) as lock, connect(cfg) as conn:
            try:
                manifest = client.manifest(day, snapshot_id)
            except (SourceError, QualityError) as exc:
                state = "FAILED_SOURCE" if isinstance(exc, SourceError) else "FAILED_DQ"
                start, end = cfg.bounds(day)
                conn.execute(
                    """INSERT INTO meta.runs(run_id,logical_key,dataset,period,snapshot_id,source,as_of,manifest,code_version,config_hash,state,error_code)
                    VALUES (%s,%s,%s,%s,'unavailable',%s,%s,'{}',%s,%s,%s,%s)""",
                    (
                        run_id,
                        str(uuid4()),
                        cfg.dataset,
                        day,
                        source,
                        end,
                        code_hash(),
                        cfg.fingerprint(),
                        state,
                        type(exc).__name__,
                    ),
                )
                conn.execute(
                    "INSERT INTO meta.attempts(attempt_id,run_id,state,finished_at) VALUES (%s,%s,%s,now())",
                    (attempt_id, run_id, state),
                )
                checks = Checks(conn, cfg, run_id)
                checks.record(getattr(exc, "rule", "DQ18"), 1)
                checks.fill()
                enqueue(conn, run_id, "notification_failed")
                return dict(
                    run_id=str(run_id),
                    state=state,
                    error=str(exc),
                    exit_code=4 if state == "FAILED_SOURCE" else 3,
                )
            version = code_hash()
            logical = hashlib.sha256(
                dumps([manifest.source, str(day), manifest.snapshot_id, version, cfg.fingerprint()]).encode()
            ).hexdigest()
            prior = conn.execute("SELECT * FROM meta.runs WHERE logical_key=%s", (logical,)).fetchone()
            if prior:
                run_id = prior["run_id"]
                if prior["state"] in ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PUBLISHED_REPORT_PENDING"):
                    conn.execute(
                        "INSERT INTO meta.attempts(attempt_id,run_id,state,finished_at) VALUES (%s,%s,'REUSED',now())",
                        (attempt_id, run_id),
                    )
                    if prior["state"] == "PUBLISHED_REPORT_PENDING":
                        from dqflow.reporting import drain

                        drain(cfg, run_id)
                    current = conn.execute(
                        "SELECT state FROM meta.runs WHERE run_id=%s", (run_id,)
                    ).fetchone()["state"]
                    return dict(
                        run_id=str(run_id),
                        state=current,
                        reused=True,
                        exit_code=6 if current == "PUBLISHED_REPORT_PENDING" else 0,
                    )
            else:
                conn.execute(
                    """INSERT INTO meta.runs(run_id,logical_key,dataset,period,snapshot_id,source,as_of,manifest,code_version,config_hash,state)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CREATED')""",
                    (
                        run_id,
                        logical,
                        cfg.dataset,
                        day,
                        manifest.snapshot_id,
                        source,
                        manifest.as_of,
                        Jsonb(manifest.model_dump(mode="json")),
                        version,
                        cfg.fingerprint(),
                    ),
                )
            conn.execute(
                "INSERT INTO meta.attempts(attempt_id,run_id,state) VALUES (%s,%s,'CREATED')",
                (attempt_id, run_id),
            )
            conn.execute("DELETE FROM meta.check_results WHERE run_id=%s", (run_id,))
            checks = Checks(conn, cfg, run_id)
            committed = False
            try:
                with heartbeat(cfg, run_id):
                    set_state(conn, cfg, run_id, attempt_id, "EXTRACTING")
                    raw_dir = Path(cfg.artifacts.raw) / str(run_id)
                    client.extract(manifest, raw_dir)
                    checks.record("DQ01")
                    checks.record("DQ02")
                    set_state(conn, cfg, run_id, attempt_id, "LOADING")
                    ingest(conn, cfg, run_id, manifest, raw_dir, baseline)
                    if (
                        os.environ.get("DQFLOW_TEST_FAILPOINT") == "after_copy"
                        and cfg.environment != "production"
                    ):
                        raise RuntimeError("Injected failure after COPY")
                    set_state(conn, cfg, run_id, attempt_id, "VALIDATING")
                    quality_start = time.monotonic()
                    checks = validate_input(conn, cfg, run_id, manifest)
                    if checks.failed():
                        raise QualityError("DQ03", "Input quality gates failed; see check_results")
                    build_dbt(conn, cfg, run_id, day)
                    reconcile_mart(conn, cfg, run_id)
                    quality_ms = int((time.monotonic() - quality_start) * 1000)
                    if checks.failed():
                        raise QualityError("DQ17", "Mart reconciliation failed")
                    checks.fill()
                    if time.monotonic() - started > cfg.pipeline.max_runtime_seconds:
                        raise RuntimeError("Pipeline deadline exceeded")
                    set_state(conn, cfg, run_id, attempt_id, "PUBLISHING")
                    publish(lock, cfg, run_id, day, manifest)
                    committed = True
                    conn.execute(
                        "UPDATE meta.runs SET quality_ms=%s,row_count=%s WHERE run_id=%s",
                        (quality_ms, sum(t.row_count for t in manifest.tables.values()), run_id),
                    )
                    if (
                        os.environ.get("DQFLOW_TEST_FAILPOINT") == "after_commit"
                        and cfg.environment != "production"
                    ):
                        raise RuntimeError("Injected unknown COMMIT outcome")
            except Exception as exc:
                # Resolve an ambiguous commit using durable publication state; never blindly reinsert.
                published = conn.execute(
                    "SELECT 1 FROM mart.daily_summary_versions WHERE run_id=%s", (run_id,)
                ).fetchone()
                if published or committed:
                    conn.execute(
                        "UPDATE meta.runs SET state='PUBLISHED_REPORT_PENDING' WHERE run_id=%s", (run_id,)
                    )
                else:
                    state = (
                        "FAILED_DQ"
                        if isinstance(exc, QualityError)
                        else "FAILED_SOURCE"
                        if isinstance(exc, SourceError)
                        else "FAILED_TECH"
                    )
                    if isinstance(exc, QualityError) and not checks.failed():
                        checks.record(exc.rule, 1)
                    checks.fill()
                    set_state(conn, cfg, run_id, attempt_id, state)
                    conn.execute(
                        "UPDATE meta.runs SET error_code=%s,finished_at=now(),duration_ms=%s WHERE run_id=%s",
                        (type(exc).__name__, int((time.monotonic() - started) * 1000), run_id),
                    )
                    conn.execute(
                        "UPDATE meta.attempts SET finished_at=now() WHERE attempt_id=%s", (attempt_id,)
                    )
                    enqueue(conn, run_id, "notification_failed")
                    log(cfg, run_id, attempt_id, state, error_code=type(exc).__name__)
                    return dict(
                        run_id=str(run_id),
                        state=state,
                        error=str(exc),
                        exit_code={"FAILED_DQ": 3, "FAILED_SOURCE": 4, "FAILED_TECH": 5}[state],
                    )
            from dqflow.reporting import drain

            drain(cfg, run_id)
            state = conn.execute("SELECT state FROM meta.runs WHERE run_id=%s", (run_id,)).fetchone()["state"]
            duration = int((time.monotonic() - started) * 1000)
            conn.execute(
                "UPDATE meta.runs SET duration_ms=%s,finished_at=now() WHERE run_id=%s", (duration, run_id)
            )
            conn.execute(
                "UPDATE meta.attempts SET state=%s,finished_at=now() WHERE attempt_id=%s", (state, attempt_id)
            )
            log(cfg, run_id, attempt_id, state, duration_ms=duration)
            return dict(
                run_id=str(run_id),
                state=state,
                reused=False,
                duration_ms=duration,
                report=f"/reports/{run_id}/summary.html",
                exit_code=6 if state == "PUBLISHED_REPORT_PENDING" else 0,
            )
    except Busy:
        return dict(state="DEFERRED_LOCK", exit_code=75)
    finally:
        client.close()


def rollback(cfg, day, run_id, reason):
    if not reason.strip():
        raise ValueError("Rollback reason is required")
    run_id = UUID(str(run_id))
    with dataset_lock(cfg) as conn:
        with conn.transaction():
            target = conn.execute(
                "SELECT * FROM meta.runs WHERE run_id=%s AND dataset=%s AND period=%s AND state IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS','PUBLISHED_REPORT_PENDING')",
                (run_id, cfg.dataset, day),
            ).fetchone()
            if not target:
                raise ValueError("Target is not a verified version of this dataset and period")
            conn.execute(
                "UPDATE meta.publications SET run_id=%s,published_at=now() WHERE dataset=%s AND period=%s",
                (run_id, cfg.dataset, day),
            )
            conn.execute(
                "INSERT INTO meta.audit_events(run_id,action,reason) VALUES (%s,'rollback',%s)",
                (run_id, reason),
            )
            enqueue(conn, run_id, "report")
    return {"run_id": str(run_id), "state": "ROLLED_BACK", "reason": reason}
