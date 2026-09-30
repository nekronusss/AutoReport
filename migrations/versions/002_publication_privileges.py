from alembic import op

revision = "002"
down_revision = "001"


def upgrade():
    op.execute("REVOKE INSERT, UPDATE, DELETE ON meta.publications FROM dqflow_runner")
    op.execute("GRANT USAGE ON SCHEMA public TO dqflow_runner")
    op.execute("GRANT SELECT ON public.alembic_version TO dqflow_runner")


def downgrade():
    raise RuntimeError("Privilege downgrade disabled; review a forward migration")
