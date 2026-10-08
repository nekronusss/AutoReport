import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import json

import pytest
from dqflow.contracts import TABLES
from dqflow.db import candidate, connect, search_path
from dqflow.demo import file_hash, generate
from dqflow.pipeline import run
from dqflow.validation import reconcile_mart
from dqflow.reporting import status

pytestmark = pytest.mark.integration
DAY = date(2026, 9, 30)


def test_fixed_multiple_children_oracle_and_mart_corruption(cfg):
    manifest = generate(cfg, DAY, 20, seed=100)
    root = Path(cfg.source.directory) / manifest["snapshot_id"]
    # First order: two items already present. Split its successful payment into two.
    path = root / "payments.csv"
    with path.open(newline="") as handle:
        payments = list(csv.DictReader(handle))
    first = payments[0]
    total = Decimal(first["amount"])
    first["amount"] = "1.00"
    payments.append({**first, "payment_id": "p-extra", "amount": str(total - 1)})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TABLES["payments"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(payments)
    manifest["tables"]["payments"]["row_count"] += 1
    manifest["tables"]["payments"]["sha256"] = file_hash(path)
    (root / "manifest.json").write_text(json.dumps(manifest))
    result = run(cfg, DAY)
    assert result["exit_code"] == 0, result
    with connect(cfg) as conn:
        fact = conn.execute(
            "SELECT * FROM mart.fact_orders_versions WHERE run_id=%s AND order_id='o0'", (result["run_id"],)
        ).fetchone()
        assert fact["captured_amount"] == Decimal("197.00")  # fixed fixture: seed=100, order 0
        assert fact["item_amount"] == Decimal("197.00")
        search_path(conn, candidate(result["run_id"]))
        conn.execute("UPDATE daily_summary SET orders_count=orders_count+1")
        reconcile_mart(conn, cfg, result["run_id"])
        assert (
            conn.execute(
                "SELECT status FROM meta.check_results WHERE run_id=%s AND rule_id='DQ17'",
                (result["run_id"],),
            ).fetchone()["status"]
            == "FAIL"
        )


def test_previous_publication_survives_bad_revision_and_older_asof(cfg):
    generate(cfg, DAY, 20, seed=100)
    good = run(cfg, DAY)
    assert good["exit_code"] == 0
    generate(cfg, DAY, 20, seed=101, scenario="duplicate")
    assert run(cfg, DAY)["exit_code"] == 3
    assert status(cfg, DAY)["run_id"] == good["run_id"]
    generate(cfg, DAY, 20, seed=99)
    older = run(cfg, DAY)
    assert older["exit_code"] == 3, older
    assert status(cfg, DAY)["run_id"] == good["run_id"]


def test_dq19_four_comparable_days(cfg):
    for lag in (28, 21, 14, 7):
        day = DAY - timedelta(days=lag)
        generate(cfg, day, 20)
        assert run(cfg, day)["exit_code"] == 0
    generate(cfg, DAY, 200)
    result = run(cfg, DAY)
    assert result["state"] == "SUCCEEDED_WITH_WARNINGS", result
    with connect(cfg) as conn:
        assert (
            conn.execute(
                "SELECT status FROM meta.check_results WHERE run_id=%s AND rule_id='DQ19'",
                (result["run_id"],),
            ).fetchone()["status"]
            == "FAIL"
        )
