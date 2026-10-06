import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import typer
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from dqflow import bootstrap
from dqflow.db import connect
from dqflow.demo import generate
from dqflow.pipeline import run as run_pipeline, rollback as rollback_pipeline
from dqflow.reporting import drain, status as get_status
from dqflow.settings import load, dumps, iso_date

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
demo_app = typer.Typer()
schedule_app = typer.Typer()
outbox_app = typer.Typer()
app.add_typer(demo_app, name="demo")
app.add_typer(schedule_app, name="schedule")
app.add_typer(outbox_app, name="outbox")


def output(value):
    typer.echo(dumps(value))


def execute(action):
    try:
        result = action()
        output(result)
        code = result.get("exit_code", 0) if isinstance(result, dict) else 0
    except (ValueError, FileNotFoundError) as exc:
        output({"state": "INVALID_CONFIG_OR_CONTRACT", "error": str(exc), "exit_code": 2})
        code = 2
    except Exception as exc:
        # Never echo connection strings, secret-bearing HTTP URLs or exception arguments.
        output({"state": "FAILED_TECH", "error_code": type(exc).__name__, "exit_code": 5})
        code = 5
    if code:
        raise typer.Exit(code)


@app.command("local-init")
def local_init():
    execute(lambda: (bootstrap.local_init(load()) or {"state": "INITIALIZED"}))


@app.command("bootstrap-roles")
def bootstrap_roles():
    execute(lambda: (bootstrap.roles(load()) or {"state": "ROLES_READY"}))


@app.command()
def migrate():
    def upgrade():
        command.upgrade(Config("migrations/alembic.ini"), "head")
        return {"state": "MIGRATED"}

    execute(upgrade)


@app.command()
def doctor():
    def check():
        cfg = load()
        for path in (cfg.artifacts.root, cfg.artifacts.raw):
            root = Path(path)
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".doctor"
            probe.write_text("ok")
            probe.unlink()
        with connect(cfg) as conn:
            version = conn.execute("SELECT version() v").fetchone()["v"]
            conn.execute("SELECT 1 FROM meta.runs LIMIT 1")
            revision = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()[
                "version_num"
            ]
            expected = ScriptDirectory.from_config(Config("migrations/alembic.ini")).get_current_head()
            if revision != expected:
                raise ValueError(f"Database migration {revision}; required {expected}")
            if conn.execute(
                "SELECT has_table_privilege(current_user,'meta.publications','UPDATE') allowed"
            ).fetchone()["allowed"]:
                raise ValueError("Runner must not be able to switch publications")
        with connect(cfg, "reader") as conn:
            rights = conn.execute(
                "SELECT has_table_privilege(current_user,(SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='raw' AND c.relname='records'),'SELECT') raw_access"
            ).fetchone()
            if rights["raw_access"]:
                raise ValueError("Report reader unexpectedly has raw access")
        return {
            "state": "HEALTHY",
            "database": version,
            "schema_version": revision,
            "free_bytes": shutil.disk_usage(cfg.artifacts.root).free,
            "config_hash": cfg.fingerprint(),
        }

    execute(check)


@demo_app.command("generate")
def demo_generate(
    period: str = typer.Option(...), rows: int = 200000, seed: int = 42, scenario: str = "clean"
):
    execute(lambda: generate(load(), iso_date(period), rows, seed, scenario))


@app.command()
def run(period: str = typer.Option(...), source: str = "demo"):
    execute(lambda: run_pipeline(load(), iso_date(period), source))


@app.command()
def status(period: str = typer.Option(...), json_output: bool = typer.Option(False, "--json")):
    execute(lambda: get_status(load(), iso_date(period)))


@app.command()
def report(period: str = typer.Option(...)):
    def render_again():
        cfg = load()
        state = get_status(cfg, iso_date(period))
        if "run_id" not in state:
            raise ValueError("No published version")
        with connect(cfg) as conn:
            conn.execute(
                "UPDATE meta.outbox SET status='PENDING',available_at=now() WHERE run_id=%s AND kind='report'",
                (state["run_id"],),
            )
        drain(cfg, state["run_id"])
        result = get_status(cfg, iso_date(period))
        result["exit_code"] = 0 if result["report_state"] == "READY" else 6
        return result

    execute(render_again)


@app.command()
def retry(run_id: str = typer.Option(...)):
    def retry_run():
        cfg = load()
        with connect(cfg) as conn:
            record = conn.execute(
                "SELECT * FROM meta.runs WHERE run_id=%s AND dataset=%s", (UUID(run_id), cfg.dataset)
            ).fetchone()
        if not record:
            raise ValueError("Unknown run")
        return run_pipeline(
            cfg,
            record["period"],
            record["source"],
            None if record["snapshot_id"] == "unavailable" else record["snapshot_id"],
        )

    execute(retry_run)


@app.command()
def backfill(
    from_date: str = typer.Option(..., "--from"),
    to_date: str = typer.Option(..., "--to"),
    source: str = "demo",
):
    def process():
        cfg = load()
        begin, end = iso_date(from_date), iso_date(to_date)
        if end < begin:
            raise ValueError("End precedes start")
        results = []
        while begin <= end:
            result = run_pipeline(cfg, begin, source)
            results.append(result)
            if result["exit_code"]:
                return {"results": results, "exit_code": result["exit_code"]}
            begin += timedelta(days=1)
        return {"results": results}

    execute(process)


@app.command()
def rollback(
    period: str = typer.Option(...), to_run_id: str = typer.Option(...), reason: str = typer.Option(...)
):
    execute(lambda: rollback_pipeline(load(), iso_date(period), to_run_id, reason))


@schedule_app.command("tick")
def tick():
    def schedule():
        cfg = load()
        now = datetime.now(ZoneInfo(cfg.timezone))
        with connect(cfg) as conn:
            conn.execute(
                "INSERT INTO meta.service_health(name,last_seen) VALUES ('scheduler',now()) ON CONFLICT(name) DO UPDATE SET last_seen=now()"
            )
        if now.strftime("%H:%M") < cfg.pipeline.start_local:
            return {"state": "BEFORE_START", "results": []}
        results = []
        for lag in range(cfg.pipeline.automatic_catchup_days, 0, -1):
            day = now.date() - timedelta(days=lag)
            if day.weekday() not in cfg.pipeline.weekdays or day in cfg.pipeline.holidays:
                continue
            if get_status(cfg, day)["data_state"] != "PUBLISHED":
                results.append(run_pipeline(cfg, day))
        return {"results": results, "exit_code": next((r["exit_code"] for r in results if r["exit_code"]), 0)}

    execute(schedule)


@outbox_app.command("drain")
def outbox_drain():
    execute(lambda: drain(load()))


@app.command()
def acceptance(profile: str = "local"):
    if profile != "local" or load().environment == "production":
        raise typer.BadParameter("Acceptance requires an isolated local/test environment")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests", "--junitxml=data/acceptance.xml"],
        env={**os.environ, "DQFLOW_INTEGRATION": "1"},
    )
    output({"state": "PASSED" if result.returncode == 0 else "FAILED", "junit": "data/acceptance.xml"})
    raise typer.Exit(result.returncode)


@app.command()
def benchmark(rows: int = 200000, repeat: int = 20, baseline: bool = False):
    from dqflow.benchmark import benchmark as measure

    execute(lambda: measure(load(), rows, repeat, baseline))


@app.command("backup-confirmed")
def confirm_backup(reference: str = typer.Option(...)):
    from dqflow.maintenance import backup_confirmed

    execute(lambda: backup_confirmed(load(), reference))


@app.command()
def cleanup():
    from dqflow.maintenance import cleanup as clean

    execute(lambda: clean(load()))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8080, kind: str = "reports"):
    import uvicorn

    module = {
        "reports": "dqflow.web:app",
        "source": "dqflow.source_api:app",
        "mock": "dqflow.webhook:app",
    }.get(kind)
    if not module:
        raise typer.BadParameter("kind must be reports, source or mock")
    uvicorn.run(module, host=host, port=port)


if __name__ == "__main__":
    app()
