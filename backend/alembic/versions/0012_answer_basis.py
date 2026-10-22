"""Persist answer provenance separately from generated text and official citations."""

from alembic import op

revision = "0012_answer_basis"
down_revision = "0011_retrieval_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE messages ADD COLUMN answer_basis varchar(20)")
    op.execute(
        "ALTER TABLE messages ADD CONSTRAINT ck_message_answer_basis CHECK "
        "(answer_basis IS NULL OR (role='assistant' AND answer_basis IN "
        "('verified','general','clarification','refused','insufficient')))"
    )


def downgrade():
    raise RuntimeError("Review answer provenance rollback separately; do not discard history")
