"""Add isolated operational metrics, administrator controls and private query cache."""

from pathlib import Path

from alembic import op

revision = "0010_v2_operations"
down_revision = "0009_personal_manuals"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("V2 operational history requires a verified backup restore")
