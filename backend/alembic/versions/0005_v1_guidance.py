"""Persist safe, versioned guidance sessions and user step feedback."""

from pathlib import Path

from alembic import op

revision = "0005_v1_guidance"
down_revision = "0004_v1_memories"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Guidance checkpoints require an explicit backup restore")
