from pathlib import Path
from alembic import op

revision = "001"
down_revision = None


def upgrade():
    op.get_bind().exec_driver_sql(Path("migrations/schema.sql").read_text())


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a tested backup")
