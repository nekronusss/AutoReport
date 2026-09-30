import os
import secrets
from pathlib import Path
from psycopg import sql
from dqflow.db import connect


def local_init(cfg):
    if cfg.environment != "local":
        raise ValueError("local-init is restricted to local environment")
    directory = Path(cfg.database.password_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for role in ("admin", "migrator", "runner", "publisher", "reader"):
        path = directory / f"db_{role}_password"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(36))
            os.chmod(path, 0o600)
    for path in (cfg.source.directory, cfg.artifacts.root, cfg.artifacts.raw):
        Path(path).mkdir(parents=True, exist_ok=True)


def roles(cfg):
    with connect(cfg, "admin") as conn:
        for role in ("migrator", "runner", "publisher", "reader"):
            name = f"dqflow_{role}"
            password = (Path(cfg.database.password_dir) / f"db_{role}_password").read_text().strip()
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(name), sql.Literal(password)
                    )
                )
        conn.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO dqflow_migrator,dqflow_runner").format(
                sql.Identifier(cfg.database.database)
            )
        )
        conn.execute("GRANT USAGE,CREATE ON SCHEMA public TO dqflow_migrator")
