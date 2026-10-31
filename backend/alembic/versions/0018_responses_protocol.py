"""Allow explicitly tested Responses configurations without rewriting existing profiles."""

from pathlib import Path

from alembic import op

revision = "0018_responses_protocol"
down_revision = "0017_manual_provenance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Retain saved Responses profiles; use reviewed application rollback")
