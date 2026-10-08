import os
import subprocess
import sys
from datetime import date

import pytest
import yaml

from dqflow.connectors import SourceClient, SourceError
from dqflow.db import connect
from dqflow.demo import generate
from dqflow.pipeline import run
from dqflow.reporting import status

pytestmark = pytest.mark.integration
DAY = date(2026, 9, 30)


def test_process_termination_after_copy(cfg, tmp_path):
    generate(cfg, DAY, 20)
    config = tmp_path / "crash.yaml"
    config.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")
    script = """
import os
from datetime import date
import dqflow.pipeline as p
from dqflow.settings import load
original = p.ingest
def crash(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(137)
p.ingest = crash
p.run(load(), date(2026,9,30))
"""
    killed = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "DQFLOW_CONFIG": str(config)},
        capture_output=True,
        timeout=90,
    )
    assert killed.returncode == 137
    assert status(cfg, DAY)["data_state"] == "NOT_PUBLISHED"
    result = run(cfg, DAY)
    assert result["exit_code"] == 0, result
    with connect(cfg) as conn:
        assert (
            conn.execute(
                "SELECT count(*) n FROM mart.fact_orders_versions WHERE run_id=%s", (result["run_id"],)
            ).fetchone()["n"]
            == 5
        )


def test_source_failure_preserves_previous_and_retry(cfg, monkeypatch):
    generate(cfg, DAY, 20, seed=200)
    first = run(cfg, DAY)
    assert first["exit_code"] == 0
    generate(cfg, DAY, 20, seed=201)
    original = SourceClient.extract

    def fail(self, manifest, directory):
        raise SourceError("Injected upstream disconnect")

    monkeypatch.setattr(SourceClient, "extract", fail)
    failed = run(cfg, DAY)
    assert failed["exit_code"] == 4
    assert status(cfg, DAY)["run_id"] == first["run_id"]
    monkeypatch.setattr(SourceClient, "extract", original)
    retry = run(cfg, DAY)
    assert retry["exit_code"] == 0
    assert retry["run_id"] == failed["run_id"]


def test_webhook_timeout_does_not_undo_report(cfg, monkeypatch):
    import httpx

    cfg.notifications.endpoint = "http://local.invalid/events"

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("injected")

    monkeypatch.setattr(httpx.Client, "post", timeout)
    generate(cfg, DAY, 20)
    result = run(cfg, DAY)
    assert result["exit_code"] == 0, result
    assert status(cfg, DAY)["notification"]["status"] == "PENDING"
    assert status(cfg, DAY)["report_state"] == "READY"


def test_unwritable_raw_never_publishes(cfg, tmp_path):
    generate(cfg, DAY, 20)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("test")
    cfg.artifacts.raw = str(blocker)
    result = run(cfg, DAY)
    assert result["exit_code"] == 5
    assert status(cfg, DAY)["data_state"] == "NOT_PUBLISHED"
