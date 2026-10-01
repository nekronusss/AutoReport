import csv
import hashlib
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from dqflow.contracts import TABLES, MONEY
from dqflow.settings import dumps


def file_hash(path):
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def amount(i, seed):
    return Decimal(10000 + (i * 7919 + seed * 97) % 90000) / 100


def generate(cfg, day, rows=200000, seed=42, scenario="clean"):
    allowed = {
        "clean",
        "duplicate",
        "null",
        "decimal",
        "status",
        "missing_customer",
        "missing_item",
        "orphan_payment",
        "currency",
        "negative",
        "over_refund",
        "unpaid",
        "wrong_total",
        "future",
        "empty_id",
        "incomplete",
        "corrupt",
        "schema",
        "warning",
        "source_sum",
    }
    if scenario not in allowed:
        raise ValueError(f"Unknown scenario: {scenario}")
    if rows < 0 or (rows and rows % 20):
        raise ValueError("rows must be zero or a multiple of 20")
    start, end = cfg.bounds(day)
    stamp = (start + timedelta(hours=12)).isoformat()
    as_of = end + timedelta(hours=4, seconds=seed % 3600)
    sid = hashlib.sha256(f"{day}:{rows}:{seed}:{scenario}:v1".encode()).hexdigest()[:32]
    root = Path(cfg.source.directory)
    target = root / sid
    if target.exists():
        manifest = json.loads((target / "manifest.json").read_text())
        temp = root / f"{day}.tmp"
        temp.write_text(dumps({"snapshot_id": sid}))
        os.replace(temp, root / f"{day}.json")
        return manifest
    target.mkdir(parents=True)
    n = rows // 4
    customers = rows // 20

    def records(table):
        if table == "customers":
            for i in range(customers):
                if scenario == "missing_customer" and i == 0:
                    continue
                yield dict(customer_id=f"c{i}", created_at=start.isoformat(), updated_at=stamp)
            return
        for i in range(n):
            cost = amount(i, seed)
            oid = f"o{i}"
            status = "completed" if i % 10 < 8 else "pending" if i % 10 == 8 else "cancelled"
            if table == "orders":
                row = dict(
                    order_id=oid,
                    customer_id=f"c{i % customers}",
                    created_at=stamp,
                    updated_at=stamp,
                    status=status,
                    currency=cfg.currency,
                    total_amount=str(cost),
                )
                if i == 0:
                    if scenario == "duplicate":
                        yield row.copy()
                    if scenario == "null":
                        row["customer_id"] = None
                    if scenario == "empty_id":
                        row["order_id"] = ""
                    if scenario == "decimal":
                        row["total_amount"] = "not-a-number"
                    if scenario == "status":
                        row["status"] = "unknown"
                    if scenario == "currency":
                        row["currency"] = "USD"
                    if scenario == "future":
                        row["created_at"] = (as_of + timedelta(days=1)).isoformat()
                    if scenario == "wrong_total":
                        row["total_amount"] = str(cost + 1)
                    if scenario == "warning":
                        row["status"] = "cancelled"
                yield row
            elif table == "order_items":
                if scenario == "missing_item" and i == 0:
                    continue
                parts = 2 if i < n * 4 // 5 else 1
                first = (cost / parts).quantize(Decimal("0.01"))
                for line in range(parts):
                    yield dict(
                        order_id=oid,
                        line_id=str(line + 1),
                        sku=f"sku{i % 25}",
                        quantity="-1" if scenario == "negative" and i == 0 else "1",
                        unit_price=str(first if line == 0 else cost - first),
                        updated_at=stamp,
                    )
            else:
                yield dict(
                    payment_id=f"p{i}",
                    order_id="absent" if scenario == "orphan_payment" and i == 0 else oid,
                    kind="refund" if scenario == "over_refund" and i == 0 else "payment",
                    status="failed"
                    if status == "cancelled" or (scenario == "unpaid" and i == 0)
                    else "succeeded",
                    amount=str(cost if status != "pending" else (cost / 2).quantize(Decimal("0.01"))),
                    currency=cfg.currency,
                    occurred_at=stamp,
                    updated_at=stamp,
                )

    tables = {}
    for table, fields in TABLES.items():
        path = target / f"{table}.csv"
        totals: dict[str, Decimal] = {}
        count = 0
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in records(table):
                writer.writerow(row)
                count += 1
                if table in MONEY:
                    try:
                        value = Decimal(row[MONEY[table]])
                    except Exception:
                        value = Decimal(0)
                    currency = row.get("currency", cfg.currency)
                    totals[currency] = totals.get(currency, Decimal(0)) + value
        tables[table] = dict(
            row_count=count, sha256=file_hash(path), money_totals={k: str(v) for k, v in totals.items()}
        )
    if scenario == "source_sum":
        tables["orders"]["money_totals"][cfg.currency] = "1.00"
    manifest = dict(
        source="demo",
        schema_version="2.0" if scenario == "schema" else "1.0",
        snapshot_id=sid,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        as_of=as_of.isoformat(),
        complete=scenario != "incomplete",
        tables=tables,
    )
    (target / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
    if scenario == "corrupt":
        with (target / "orders.csv").open("ab") as binary_handle:
            binary_handle.write(b"corrupt\n")
    temp = root / f"{day}.tmp"
    temp.write_text(dumps({"snapshot_id": sid}))
    os.replace(temp, root / f"{day}.json")
    return manifest
