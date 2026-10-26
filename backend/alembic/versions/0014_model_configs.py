"""Immutable encrypted text-model profiles and bounded administrator write functions."""

from pathlib import Path

from alembic import op

revision = "0014_model_configs"
down_revision = "0013_web_lookup"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Keep encrypted model profiles for in-flight tasks; review rollback separately"
    )
