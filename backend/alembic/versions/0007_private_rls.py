"""Force tenant isolation and limit authentication/queue entry points."""

from pathlib import Path

from alembic import op

revision = "0007_private_rls"
down_revision = "0006_checkpoint_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "RLS is a security boundary; restore a verified paired backup in maintenance"
    )
