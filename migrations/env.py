from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from pathlib import Path
from dqflow.settings import load

cfg = load().database
url = URL.create(
    "postgresql+psycopg",
    username="dqflow_migrator",
    password=(Path(cfg.password_dir) / "db_migrator_password").read_text().strip(),
    host=cfg.host,
    port=cfg.port,
    database=cfg.database,
    query={"sslmode": cfg.sslmode, **({"sslrootcert": cfg.sslrootcert} if cfg.sslrootcert else {})},
)
with create_engine(url).connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
