"""Freeze generation inputs and preserve incremental chapter provenance."""

from pathlib import Path

from alembic import op

revision = "0009_personal_manuals"
down_revision = "0008_private_cleanup"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Manual versions require a verified backup restore")
