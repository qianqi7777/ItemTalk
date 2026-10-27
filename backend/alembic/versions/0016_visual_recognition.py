"""Private multi-photo recognition rounds; preserve existing identification records."""

from pathlib import Path

from alembic import op

revision = "0016_visual_recognition"
down_revision = "0015_model_roles"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Retain private recognition history; use reviewed application rollback")
