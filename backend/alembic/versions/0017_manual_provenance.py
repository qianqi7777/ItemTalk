"""Retain exact official-page provenance for externally hosted manuals."""

from pathlib import Path

from alembic import op

revision = "0017_manual_provenance"
down_revision = "0016_visual_recognition"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Retain manual provenance; use reviewed application rollback")
