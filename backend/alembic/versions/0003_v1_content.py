"""Add versioned manuals, private answer feedback and anonymized research gaps."""

from pathlib import Path

from alembic import op

revision = "0003_v1_content"
down_revision = "0002_checkpoints"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a verified backup; published manual versions must be preserved")
