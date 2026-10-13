"""Add explicitly confirmed, instance-scoped private memories."""

from pathlib import Path

from alembic import op

revision = "0004_v1_memories"
down_revision = "0003_v1_content"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Private memory downgrade requires a verified backup restore")
