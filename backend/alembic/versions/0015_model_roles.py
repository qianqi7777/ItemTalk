"""Independent text, vision, speech and fixed-identity embedding routing."""

from pathlib import Path

from alembic import op

revision = "0015_model_roles"
down_revision = "0014_model_configs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Retain role configurations for in-flight tasks; use reviewed application rollback"
    )
