import csv
import hashlib
from pathlib import Path
from psycopg import sql
from psycopg.types.json import Jsonb
from dqflow.contracts import TABLES, KEYS, parse_row
from dqflow.db import candidate, search_path
from dqflow.settings import dumps

DDL = {
    "customers": "customer_id text,created_at timestamptz,updated_at timestamptz",
    "orders": "order_id text,customer_id text,created_at timestamptz,updated_at timestamptz,status text,currency text,total_amount numeric(18,2)",
    "order_items": "order_id text,line_id text,sku text,quantity integer,unit_price numeric(18,2),updated_at timestamptz",
    "payments": "payment_id text,order_id text,kind text,status text,amount numeric(18,2),currency text,occurred_at timestamptz,updated_at timestamptz",
}


def ingest(conn, cfg, run_id, manifest, directory, baseline=False):
    schema = candidate(run_id)
    with conn.transaction():
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        search_path(conn, schema)
        for table in TABLES:
            conn.execute(sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table), sql.SQL(DDL[table])))
        conn.execute("DELETE FROM raw.records WHERE run_id=%s", (run_id,))
        conn.execute("DELETE FROM meta.rejected_records WHERE run_id=%s", (run_id,))
        for table, fields in TABLES.items():
            with (Path(directory) / f"{table}.csv").open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                ordinal = 0
                while True:
                    batch = []
                    for row in reader:
                        ordinal += 1
                        batch.append((ordinal, row))
                        if len(batch) == cfg.source.batch_rows:
                            break
                    if not batch:
                        break
                    with conn.cursor().copy(
                        "COPY raw.records(run_id,table_name,ordinal,payload,sha256) FROM STDIN"
                    ) as copy:
                        for number, row in batch:
                            copy.write_row(
                                (
                                    run_id,
                                    table,
                                    number,
                                    Jsonb(row),
                                    hashlib.sha256(dumps(row).encode()).hexdigest(),
                                )
                            )
                    good = []
                    for number, row in batch:
                        try:
                            parsed = parse_row(table, row)
                            good.append([parsed[field] for field in fields])
                        except (ValueError, TypeError) as exc:
                            conn.execute(
                                "INSERT INTO meta.rejected_records VALUES (%s,%s,%s,%s,%s)",
                                (
                                    run_id,
                                    table,
                                    number,
                                    str(exc).split("\n")[0][:200],
                                    "|".join(str(row.get(k, ""))[:100] for k in KEYS[table]),
                                ),
                            )
                    if baseline:
                        for values in good:
                            conn.execute(
                                sql.SQL("INSERT INTO {} VALUES ({})").format(
                                    sql.Identifier(table),
                                    sql.SQL(",").join(sql.Placeholder() for _ in fields),
                                ),
                                values,
                            )
                    else:
                        with conn.cursor().copy(
                            sql.SQL("COPY {} FROM STDIN").format(sql.Identifier(table))
                        ) as copy:
                            for values in good:
                                copy.write_row(values)
            conn.execute(
                "INSERT INTO meta.source_batches VALUES (%s,%s,%s,%s,%s) ON CONFLICT(run_id,table_name) DO NOTHING",
                (
                    run_id,
                    table,
                    str(Path(directory) / f"{table}.csv"),
                    manifest.tables[table].sha256,
                    ordinal,
                ),
            )
            for field in KEYS[table] + (["order_id"] if table == "payments" else []):
                conn.execute(
                    sql.SQL("CREATE INDEX ON {} ({})").format(sql.Identifier(table), sql.Identifier(field))
                )
            conn.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO dqflow_publisher").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO dqflow_publisher").format(
                sql.Identifier(schema)
            )
        )
    return schema
