"""Keep third-party reference material outside manufacturer knowledge."""

from pathlib import Path

from alembic import op

revision = "0020_supplemental_index"
down_revision = "0019_manufacturer_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Retain supplemental evidence; use reviewed application rollback")
