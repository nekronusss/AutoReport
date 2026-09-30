from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg import sql

ROLES = {"runner", "publisher", "reader", "migrator", "admin"}


def connect(cfg, role="runner"):
    if role not in ROLES:
        raise ValueError("Unknown role")
    d = cfg.database
    args = dict(
        host=d.host,
        port=d.port,
        dbname=d.database,
        user="postgres" if role == "admin" else f"dqflow_{role}",
        password=(Path(d.password_dir) / f"db_{role}_password").read_text().strip(),
        sslmode=d.sslmode,
        connect_timeout=5,
        autocommit=True,
        row_factory=dict_row,
        options=f"-c statement_timeout={d.statement_timeout_ms} -c lock_timeout={d.lock_timeout_ms}",
    )
    if d.sslrootcert:
        args["sslrootcert"] = d.sslrootcert
    return psycopg.connect(**args)


def candidate(run_id):
    return "candidate_" + UUID(str(run_id)).hex


class Busy(Exception):
    pass


@contextmanager
def dataset_lock(cfg):
    with connect(cfg, "publisher") as conn:
        key = f"{cfg.environment}:{cfg.dataset}"
        acquired = conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok", (key,)
        ).fetchone()
        if not acquired["ok"]:
            raise Busy("Dataset is already running")
        try:
            yield conn
        finally:
            if not conn.closed:
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (key,))


def search_path(conn, schema):
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
