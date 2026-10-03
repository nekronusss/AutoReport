import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from statistics import median

import yaml
from psycopg import sql
from psycopg.types.json import Jsonb
from dqflow.contracts import TABLES, KEYS, MONEY
from dqflow.db import candidate, search_path
from dqflow.settings import dumps


class Checks:
    def __init__(self, conn, cfg, run_id):
        self.conn, self.cfg, self.run_id = conn, cfg, run_id
        self.rules = yaml.safe_load(Path(cfg.rules_file).read_text(encoding="utf-8"))
        if set(self.rules) != {f"DQ{i:02}" for i in range(1, 21)}:
            raise ValueError("Exactly DQ01-DQ20 must be configured")
        for key, rule in self.rules.items():
            if rule["severity"] != ("WARN" if key in ("DQ19", "DQ20") else "ERROR"):
                raise ValueError("Severity changes require a reviewed code release")

    def record(self, rule_id, violations=0, examples=None, duration=0, status=None):
        rule = self.rules[rule_id]
        state = status or ("FAIL" if violations else "PASS")
        self.conn.execute(
            """INSERT INTO meta.check_results
            (run_id,rule_id,version,description,severity,status,violations,duration_ms,examples,threshold)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(run_id,rule_id) DO UPDATE SET status=excluded.status,violations=excluded.violations,
            duration_ms=excluded.duration_ms,examples=excluded.examples""",
            (
                self.run_id,
                rule_id,
                rule["version"],
                rule["description"],
                rule["severity"],
                state,
                violations,
                int(duration * 1000),
                Jsonb((examples or [])[:20]),
                str(rule.get("threshold", 0)),
            ),
        )

    def query(self, rule_id, query, params=()):
        start = time.monotonic()
        wrapped = (
            "WITH failures AS ("
            + query
            + ") SELECT count(*) n, (SELECT coalesce(jsonb_agg(x),'[]') FROM (SELECT * FROM failures LIMIT 20) x) examples FROM failures"
        )
        row = self.conn.execute(wrapped, params).fetchone()
        self.record(rule_id, row["n"], row["examples"], time.monotonic() - start)

    def failed(self):
        return (
            self.conn.execute(
                "SELECT 1 FROM meta.check_results WHERE run_id=%s AND severity='ERROR' AND status='FAIL' LIMIT 1",
                (self.run_id,),
            ).fetchone()
            is not None
        )

    def fill(self):
        for key in self.rules:
            if not self.conn.execute(
                "SELECT 1 FROM meta.check_results WHERE run_id=%s AND rule_id=%s", (self.run_id, key)
            ).fetchone():
                self.record(key, status="NOT_EVALUATED")


def validate_input(conn, cfg, run_id, manifest):
    search_path(conn, candidate(run_id))
    checks = Checks(conn, cfg, run_id)
    checks.query("DQ03", "SELECT table_name,ordinal FROM meta.rejected_records WHERE run_id=%s", (run_id,))
    required = []
    duplicates = []
    for table, keys in KEYS.items():
        required.append(
            f"SELECT '{table}' AS entity,concat_ws('|',{','.join(keys)}) AS id FROM {table} WHERE "
            + " OR ".join(f"btrim({k})=''" for k in keys)
        )
        duplicates.append(
            f"SELECT '{table}' AS entity,concat_ws('|',{','.join(keys)}) AS id FROM {table} GROUP BY {','.join(keys)} HAVING count(*)>1"
        )
    checks.query("DQ04", " UNION ALL ".join(required))
    checks.query("DQ05", " UNION ALL ".join(duplicates))
    checks.query(
        "DQ06",
        "SELECT order_id AS id FROM orders o WHERE NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id=o.customer_id)",
    )
    checks.query(
        "DQ07",
        "SELECT order_id AS id FROM order_items i WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.order_id=i.order_id) UNION ALL SELECT payment_id FROM payments p WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.order_id=p.order_id)",
    )
    checks.query(
        "DQ08",
        "SELECT order_id AS id FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id=o.order_id)",
    )
    checks.query(
        "DQ09",
        "SELECT order_id AS id FROM orders WHERE status NOT IN ('pending','completed','cancelled') UNION ALL SELECT payment_id FROM payments WHERE kind NOT IN ('payment','refund') OR status NOT IN ('pending','succeeded','failed')",
    )
    checks.query(
        "DQ10",
        "SELECT order_id AS id FROM orders WHERE total_amount<0 UNION ALL SELECT order_id FROM order_items WHERE quantity<=0 OR unit_price<0 UNION ALL SELECT payment_id FROM payments WHERE amount<0",
    )
    checks.query(
        "DQ11",
        "SELECT order_id AS id FROM orders WHERE currency<>%s UNION ALL SELECT p.payment_id FROM payments p LEFT JOIN orders o USING(order_id) WHERE p.currency<>%s OR p.currency<>o.currency",
        (cfg.currency, cfg.currency),
    )
    checks.query(
        "DQ12",
        "SELECT o.order_id AS id FROM orders o LEFT JOIN (SELECT order_id,sum(quantity*unit_price) amount FROM order_items GROUP BY order_id) i USING(order_id) WHERE abs(o.total_amount-coalesce(i.amount,0))>0.01",
    )
    pay = "SELECT order_id,sum(amount) FILTER (WHERE kind='payment' AND status='succeeded') captured,sum(amount) FILTER (WHERE kind='refund' AND status='succeeded') refunded FROM payments GROUP BY order_id"
    checks.query(
        "DQ13",
        f"SELECT o.order_id AS id FROM orders o LEFT JOIN ({pay}) p USING(order_id) WHERE o.status='completed' AND abs(o.total_amount-(coalesce(p.captured,0)-coalesce(p.refunded,0)))>0.01",
    )
    checks.query(
        "DQ14", f"SELECT order_id AS id FROM ({pay}) p WHERE coalesce(refunded,0)>coalesce(captured,0)"
    )
    checks.query(
        "DQ15",
        """SELECT order_id AS id FROM orders WHERE created_at<%s OR created_at>=%s OR updated_at<created_at OR updated_at>%s
        UNION ALL SELECT customer_id FROM customers WHERE updated_at<created_at OR updated_at>%s
        UNION ALL SELECT order_id FROM order_items WHERE updated_at>%s
        UNION ALL SELECT payment_id FROM payments WHERE updated_at<occurred_at OR updated_at>%s""",
        (
            manifest.period_start,
            manifest.period_end,
            manifest.as_of,
            manifest.as_of,
            manifest.as_of,
            manifest.as_of,
        ),
    )
    failures = []
    for table in TABLES:
        count = conn.execute(sql.SQL("SELECT count(*) n FROM {}").format(sql.Identifier(table))).fetchone()[
            "n"
        ]
        expected = manifest.tables[table]
        if count != expected.row_count:
            failures.append({"table": table, "field": "row_count"})
        if table in MONEY:
            currency_expr = "currency" if table != "order_items" else "%s::text"
            params = () if table != "order_items" else (cfg.currency,)
            values = conn.execute(
                f"SELECT {currency_expr} currency,sum({MONEY[table]}) amount FROM {table} GROUP BY 1", params
            ).fetchall()
            actual = {r["currency"]: r["amount"] for r in values}
            try:
                expected_totals = {k: Decimal(v) for k, v in expected.money_totals.items()}
            except Exception:
                expected_totals = {"INVALID": Decimal(0)}
            if actual != expected_totals:
                failures.append({"table": table, "field": "money_totals"})
    checks.record("DQ16", len(failures), failures)
    checks.record("DQ18")  # requested date exists and manifest matches exact calendar bounds
    history = conn.execute(
        """SELECT s.orders_count FROM mart.current_daily_summary s
        JOIN meta.publications p USING(run_id) WHERE p.dataset=%s AND p.period<%s
        AND extract(isodow FROM p.period)=extract(isodow FROM %s::date)
        ORDER BY p.period DESC LIMIT 8""",
        (
            cfg.dataset,
            manifest.period_start.astimezone(__import__("zoneinfo").ZoneInfo(cfg.timezone)).date(),
            manifest.period_start.astimezone(__import__("zoneinfo").ZoneInfo(cfg.timezone)).date(),
        ),
    ).fetchall()
    if len(history) < 4:
        checks.record("DQ19", status="NOT_EVALUATED")
    else:
        mid = median([h["orders_count"] for h in history])
        current = manifest.tables["orders"].row_count
        checks.record("DQ19", int((abs(current - mid) / mid > 0.5) if mid else current > 0))
    checks.query(
        "DQ20",
        f"SELECT o.order_id AS id FROM orders o JOIN ({pay}) p USING(order_id) WHERE o.status='cancelled' AND coalesce(p.captured,0)-coalesce(p.refunded,0)>0",
    )
    return checks


def build_dbt(conn, cfg, run_id, day):
    schema = candidate(run_id)
    target = Path(cfg.artifacts.root).resolve() / "dbt" / str(run_id)
    target.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    d = cfg.database
    env.update(
        DQ_DB_HOST=d.host,
        DQ_DB_PORT=str(d.port),
        DQ_DB_NAME=d.database,
        DQ_SCHEMA=schema,
        DQ_DB_SSLMODE=d.sslmode,
        DQ_DB_SSLROOTCERT=d.sslrootcert or "",
        DBT_ENV_SECRET_PASSWORD=(Path(d.password_dir) / "db_runner_password").read_text().strip(),
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
    )
    args = [
        str(Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")),
        "build",
        "--project-dir",
        "dbt",
        "--profiles-dir",
        "dbt",
        "--target-path",
        str(target),
        "--log-path",
        str(target / "logs"),
        "--no-use-colors",
        "--vars",
        dumps({"period": str(day), "timezone": cfg.timezone, "currency": cfg.currency}),
    ]
    with (target / "console.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            args, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=cfg.pipeline.max_runtime_seconds
        )
    try:
        manifest = json.loads((target / "manifest.json").read_text())
        output = json.loads((target / "run_results.json").read_text())
        expected = {k for k, n in manifest["nodes"].items() if n["resource_type"] in ("test", "model")}
        actual = {r["unique_id"]: r["status"] for r in output["results"]}
        ok = (
            result.returncode == 0
            and bool(expected)
            and expected == set(actual)
            and all(s in ("success", "pass") for s in actual.values())
        )
    except (OSError, KeyError, ValueError):
        ok = False
    if not ok:
        raise RuntimeError(f"dbt build failed or results incomplete; see {target / 'console.log'}")
    conn.execute(
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO dqflow_publisher").format(sql.Identifier(schema))
    )


def reconcile_mart(conn, cfg, run_id):
    """Independent SQL against typed input, without referencing fact_orders."""
    expected = conn.execute("""SELECT
      (SELECT count(*) FROM orders) orders_count,
      (SELECT count(distinct customer_id) FROM orders) customers_count,
      (SELECT coalesce(sum(total_amount),0) FROM orders WHERE status<>'cancelled') ordered_amount,
      (SELECT coalesce(sum(amount),0) FROM payments WHERE kind='payment' AND status='succeeded') captured_amount,
      (SELECT coalesce(sum(amount),0) FROM payments WHERE kind='refund' AND status='succeeded') refunded_amount""").fetchone()
    expected["net_collected_amount"] = expected["captured_amount"] - expected["refunded_amount"]
    actual = conn.execute("SELECT * FROM daily_summary").fetchall()
    bad = len(actual) != 1 or any(actual[0][k] != v for k, v in expected.items())
    detail = conn.execute("""SELECT count(*) n FROM fact_orders f WHERE
      f.item_amount<>(SELECT coalesce(sum(i.quantity*i.unit_price),0) FROM order_items i WHERE i.order_id=f.order_id)
      OR f.captured_amount<>(SELECT coalesce(sum(p.amount),0) FROM payments p WHERE p.order_id=f.order_id AND p.kind='payment' AND p.status='succeeded')
      OR f.refunded_amount<>(SELECT coalesce(sum(p.amount),0) FROM payments p WHERE p.order_id=f.order_id AND p.kind='refund' AND p.status='succeeded')""").fetchone()[
        "n"
    ]
    Checks(conn, cfg, run_id).record("DQ17", int(bad) + detail)
