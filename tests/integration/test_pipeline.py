import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from dqflow.db import connect, dataset_lock
from dqflow.demo import generate
from dqflow.pipeline import run, rollback
from dqflow.reporting import status, drain

pytestmark = pytest.mark.integration
DAY = date(2026, 9, 30)


def test_clean_oracle_idempotency(cfg):
    manifest = generate(cfg, DAY, 200)
    result = run(cfg, DAY)
    assert result["exit_code"] == 0, result
    # Independent streaming oracle over source CSV, not transformation code.
    root = Path(cfg.source.directory) / manifest["snapshot_id"]
    with (root / "orders.csv").open() as handle:
        orders = list(csv.DictReader(handle))
    with (root / "payments.csv").open() as handle:
        payments = list(csv.DictReader(handle))
    ordered = sum((Decimal(o["total_amount"]) for o in orders if o["status"] != "cancelled"), Decimal(0))
    captured = sum(
        (Decimal(p["amount"]) for p in payments if p["status"] == "succeeded" and p["kind"] == "payment"),
        Decimal(0),
    )
    with connect(cfg) as conn:
        summary = conn.execute(
            "SELECT * FROM mart.daily_summary_versions WHERE run_id=%s", (result["run_id"],)
        ).fetchone()
        assert summary["orders_count"] == 50
        assert summary["customers_count"] == 10
        assert summary["ordered_amount"] == ordered
        assert summary["captured_amount"] == captured
        assert summary["net_collected_amount"] == captured
        checks = conn.execute(
            "SELECT rule_id,status FROM meta.check_results WHERE run_id=%s", (result["run_id"],)
        ).fetchall()
        assert len(checks) == 20
        assert {r["rule_id"]: r["status"] for r in checks}["DQ19"] == "NOT_EVALUATED"
    again = run(cfg, DAY)
    assert again["reused"] and again["run_id"] == result["run_id"]
    assert status(cfg, DAY)["report_state"] == "READY"
    assert (Path(cfg.artifacts.root) / "reports" / result["run_id"] / "summary.html").exists()


@pytest.mark.parametrize(
    "scenario,rule",
    [
        ("duplicate", "DQ05"),
        ("decimal", "DQ03"),
        ("empty_id", "DQ04"),
        ("missing_customer", "DQ06"),
        ("orphan_payment", "DQ07"),
        ("missing_item", "DQ08"),
        ("status", "DQ09"),
        ("negative", "DQ10"),
        ("currency", "DQ11"),
        ("wrong_total", "DQ12"),
        ("unpaid", "DQ13"),
        ("over_refund", "DQ14"),
        ("future", "DQ15"),
        ("source_sum", "DQ16"),
        ("incomplete", "DQ01"),
        ("corrupt", "DQ02"),
        ("schema", "DQ03"),
    ],
)
def test_bad_snapshot_is_blocked(cfg, scenario, rule):
    generate(cfg, DAY, 200, scenario=scenario)
    result = run(cfg, DAY)
    assert result["exit_code"] == 3, result
    with connect(cfg) as conn:
        failed = conn.execute(
            "SELECT status FROM meta.check_results WHERE run_id=%s AND rule_id=%s", (result["run_id"], rule)
        ).fetchone()
        assert failed["status"] == "FAIL"
        assert not conn.execute(
            "SELECT 1 FROM meta.publications WHERE run_id=%s", (result["run_id"],)
        ).fetchone()


def test_empty_and_missing(cfg):
    generate(cfg, DAY, 0)
    result = run(cfg, DAY)
    assert result["exit_code"] == 0, result
    with connect(cfg) as conn:
        assert (
            conn.execute(
                "SELECT orders_count FROM mart.daily_summary_versions WHERE run_id=%s", (result["run_id"],)
            ).fetchone()["orders_count"]
            == 0
        )
    assert run(cfg, DAY + timedelta(days=1))["exit_code"] == 4


def test_lock_contention(cfg):
    with dataset_lock(cfg):
        assert run(cfg, DAY)["exit_code"] == 75


def test_retry_copy_commit_report_and_rollback(cfg, monkeypatch):
    generate(cfg, DAY, 200, 42)
    monkeypatch.setenv("DQFLOW_TEST_FAILPOINT", "after_copy")
    failed = run(cfg, DAY)
    assert failed["exit_code"] == 5
    assert status(cfg, DAY)["data_state"] == "NOT_PUBLISHED"
    monkeypatch.setenv("DQFLOW_TEST_FAILPOINT", "after_commit")
    old = run(cfg, DAY)
    assert old["exit_code"] == 0, old
    assert old["run_id"] == failed["run_id"]
    assert status(cfg, DAY)["latest_attempt"]["error_code"] is None
    generate(cfg, DAY, 200, 43)
    monkeypatch.setenv("DQFLOW_TEST_FAILPOINT", "report")
    new = run(cfg, DAY)
    assert new["exit_code"] == 6, new
    state = status(cfg, DAY)
    assert state["run_id"] == new["run_id"] and state["report_state"] == "PENDING"
    monkeypatch.delenv("DQFLOW_TEST_FAILPOINT")
    with connect(cfg) as conn:
        conn.execute("UPDATE meta.outbox SET available_at=now() WHERE run_id=%s", (new["run_id"],))
    drain(cfg, new["run_id"])
    assert status(cfg, DAY)["report_state"] == "READY"
    rollback(cfg, DAY, old["run_id"], "Acceptance test")
    assert status(cfg, DAY)["run_id"] == old["run_id"]
    assert status(cfg, DAY)["report_state"] == "READY"


def test_warning_publishes(cfg):
    generate(cfg, DAY, 200, scenario="warning")
    result = run(cfg, DAY)
    assert result["state"] == "SUCCEEDED_WITH_WARNINGS", result


def test_reader_cannot_read_raw(cfg):
    with connect(cfg, "reader") as conn:
        assert not conn.execute(
            "SELECT has_table_privilege(current_user,(SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='raw' AND c.relname='records'),'SELECT') v"
        ).fetchone()["v"]
    with connect(cfg) as conn:
        assert not conn.execute(
            "SELECT has_table_privilege(current_user,'meta.publications','UPDATE') v"
        ).fetchone()["v"]
        assert not conn.execute(
            "SELECT has_table_privilege(current_user,'mart.fact_orders_versions','INSERT') v"
        ).fetchone()["v"]
