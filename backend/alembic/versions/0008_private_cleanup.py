"""Track private storage objects, including transaction rollback orphans."""

from pathlib import Path

from alembic import op

revision = "0008_private_cleanup"
down_revision = "0007_private_rls"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Private cleanup requires a verified paired backup restore")
