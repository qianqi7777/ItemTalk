"""Private per-message web provenance; additive rollback-compatible metadata."""

from alembic import op

revision = "0013_web_lookup"
down_revision = "0012_answer_basis"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE messages ADD COLUMN web_lookup jsonb")
    op.execute(
        "ALTER TABLE assistant_runs ADD COLUMN force_web_search boolean NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE messages DROP CONSTRAINT ck_message_answer_basis")
    op.execute(
        "ALTER TABLE messages ADD CONSTRAINT ck_message_answer_basis CHECK (answer_basis IS NULL OR (role='assistant' AND answer_basis IN ('verified','web','general','clarification','refused','insufficient')))"
    )
    op.execute(
        "ALTER TABLE messages ADD CONSTRAINT ck_message_web_lookup CHECK (web_lookup IS NULL OR (role='assistant' AND jsonb_typeof(web_lookup)='object'))"
    )


def downgrade():
    raise RuntimeError("Preserve web provenance; review any destructive downgrade separately")
