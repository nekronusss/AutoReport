import csv
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from dqflow.db import connect
from dqflow.demo import file_hash
from dqflow.settings import dumps

ARTIFACTS = ("summary.html", "summary.csv", "orders.csv", "quality.json", "failed_records.csv")


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: safe_cell(row.get(k)) for k in fields})


def render(cfg, run_id, writer):
    if os.environ.get("DQFLOW_TEST_FAILPOINT") == "report" and cfg.environment != "production":
        raise OSError("Injected render failure")
    with connect(cfg, "reader") as reader:
        run = reader.execute("SELECT * FROM meta.runs WHERE run_id=%s", (run_id,)).fetchone()
        summary = reader.execute(
            "SELECT * FROM mart.daily_summary_versions WHERE run_id=%s", (run_id,)
        ).fetchall()
        if not summary:
            raise ValueError("Only published versions may be rendered")
        checks = reader.execute(
            "SELECT * FROM meta.check_results WHERE run_id=%s ORDER BY rule_id", (run_id,)
        ).fetchall()
        warnings = [c for c in checks if c["severity"] == "WARN" and c["status"] == "FAIL"]
        report_state = "SUCCEEDED_WITH_WARNINGS" if warnings else "SUCCEEDED"
        root = Path(cfg.artifacts.root) / "reports"
        root.mkdir(parents=True, exist_ok=True)
        final = root / str(run_id)
        temp = root / f".{run_id}.{uuid4().hex}.tmp"
        if not final.exists():
            temp.mkdir()
            try:
                env = Environment(
                    loader=FileSystemLoader(Path(__file__).parent / "templates"),
                    autoescape=select_autoescape(["html"]),
                )
                html = env.get_template("report.html").render(
                    run=run,
                    summary=summary[0],
                    checks=checks,
                    state=report_state,
                    warnings=warnings,
                    generated=datetime.now(timezone.utc),
                )
                (temp / "summary.html").write_text(html, encoding="utf-8")
                write_csv(temp / "summary.csv", summary, list(summary[0]))
                # Server-side cursor bounds memory for large exports.
                with reader.transaction():
                    with reader.cursor(name="report_orders") as cursor:
                        cursor.itersize = cfg.source.batch_rows
                        cursor.execute(
                            "SELECT * FROM mart.fact_orders_versions WHERE run_id=%s ORDER BY order_id",
                            (run_id,),
                        )
                        fields = [c.name for c in cursor.description]
                        write_csv(temp / "orders.csv", cursor, fields)
                (temp / "quality.json").write_text(
                    dumps(
                        {
                            "run_id": run_id,
                            "period": run["period"],
                            "as_of": run["as_of"],
                            "state": report_state,
                            "checks": checks,
                        }
                    ),
                    encoding="utf-8",
                )
                examples = [
                    {"rule_id": c["rule_id"], "technical_id": dumps(e)} for c in checks for e in c["examples"]
                ]
                write_csv(temp / "failed_records.csv", examples, ["rule_id", "technical_id"])
                hashes = {name: file_hash(temp / name) for name in ARTIFACTS}
                (temp / "hashes.json").write_text(dumps(hashes))
                for name in ARTIFACTS:
                    if file_hash(temp / name) != hashes[name]:
                        raise OSError("Artifact changed during rendering")
                temp.rename(final)
            finally:
                if temp.exists():
                    shutil.rmtree(temp)
        hashes = json.loads((final / "hashes.json").read_text())
        for name in ARTIFACTS:
            if file_hash(final / name) != hashes[name]:
                raise OSError("Published artifact integrity error")
            writer.execute(
                "INSERT INTO meta.report_artifacts(run_id,name,path,sha256) VALUES (%s,%s,%s,%s) ON CONFLICT(run_id,name) DO NOTHING",
                (run_id, name, str(final / name), hashes[name]),
            )
        writer.execute("UPDATE meta.runs SET state=%s,error_code=NULL WHERE run_id=%s", (report_state, run_id))


def drain(cfg, run_id=None, budget=120):
    start = time.monotonic()
    processed = 0
    with connect(cfg) as conn:
        while time.monotonic() - start < budget:
            with conn.transaction():
                event = conn.execute(
                    """SELECT * FROM meta.outbox WHERE status='PENDING' AND available_at<=now()
                   AND (%s::uuid IS NULL OR run_id=%s) ORDER BY CASE kind WHEN 'report' THEN 0 ELSE 1 END,created_at
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (run_id, run_id),
                ).fetchone()
                if not event:
                    break
                try:
                    if event["kind"] == "report":
                        with conn.transaction():
                            render(cfg, event["run_id"], conn)
                    elif cfg.notifications.endpoint:
                        state = conn.execute(
                            "SELECT state,period FROM meta.runs WHERE run_id=%s", (event["run_id"],)
                        ).fetchone()
                        headers = {"Idempotency-Key": str(event["event_id"])}
                        if cfg.notifications.token_file:
                            headers["Authorization"] = (
                                "Bearer " + Path(cfg.notifications.token_file).read_text().strip()
                            )
                        with httpx.Client(timeout=5, follow_redirects=False) as client:
                            response = client.post(
                                cfg.notifications.endpoint,
                                headers=headers,
                                json={
                                    "event_id": str(event["event_id"]),
                                    "event_type": event["kind"],
                                    "run_id": str(event["run_id"]),
                                    "state": state["state"],
                                    "period": str(state["period"]),
                                    "report": f"/reports/{event['run_id']}/summary.html",
                                },
                            )
                            response.raise_for_status()
                    conn.execute(
                        "UPDATE meta.outbox SET status=%s,delivered_at=now(),attempts=attempts+1,error_code=NULL WHERE event_id=%s",
                        (
                            "DISABLED"
                            if event["kind"] != "report" and not cfg.notifications.endpoint
                            else "DELIVERED",
                            event["event_id"],
                        ),
                    )
                    processed += 1
                except Exception as exc:
                    attempts = event["attempts"] + 1
                    status = (
                        "DEAD"
                        if event["kind"] != "report" and attempts >= cfg.notifications.attempts
                        else "PENDING"
                    )
                    conn.execute(
                        "UPDATE meta.outbox SET status=%s,attempts=%s,error_code=%s,available_at=now()+(%s*interval '1 second') WHERE event_id=%s",
                        (
                            status,
                            attempts,
                            type(exc).__name__,
                            min(300, 2 ** min(attempts, 8)),
                            event["event_id"],
                        ),
                    )
    return {"processed": processed}


def status(cfg, day):
    with connect(cfg, "reader") as conn:
        publication = conn.execute(
            "SELECT p.published_at,r.* FROM meta.publications p JOIN meta.runs r USING(run_id) WHERE p.dataset=%s AND p.period=%s",
            (cfg.dataset, day),
        ).fetchone()
        latest = conn.execute(
            "SELECT run_id,state,error_code,created_at FROM meta.runs WHERE dataset=%s AND period=%s ORDER BY created_at DESC LIMIT 1",
            (cfg.dataset, day),
        ).fetchone()
        if not publication:
            return {
                "period": str(day),
                "data_state": "NOT_PUBLISHED",
                "latest_attempt": latest,
                "report_state": "ABSENT",
            }
        rid = publication["run_id"]
        report = conn.execute(
            "SELECT * FROM meta.report_artifacts WHERE run_id=%s AND name='summary.html'", (rid,)
        ).fetchone()
        event = conn.execute(
            "SELECT status,attempts,error_code FROM meta.outbox WHERE run_id=%s AND kind LIKE 'notification%%' ORDER BY created_at DESC LIMIT 1",
            (rid,),
        ).fetchone()
        return {
            "period": str(day),
            "run_id": str(rid),
            "data_state": "PUBLISHED",
            "run_state": publication["state"],
            "as_of": publication["as_of"],
            "age_seconds": int((datetime.now(timezone.utc) - publication["as_of"]).total_seconds()),
            "report_state": "READY" if report else "PENDING",
            "report": f"/reports/{rid}/summary.html" if report else None,
            "notification": event,
            "latest_attempt": latest,
        }
