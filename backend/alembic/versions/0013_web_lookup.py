"""Private per-message web provenance; additive rollback-compatible metadata."""

import json

from sqlalchemy import text

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
    # Reviewed manufacturer aliases are registered once by the migrator, not by web/model output.
    # Explicit existing lists (including an empty/revoked list) remain authoritative.
    seeds = (
        (("迈从", "mchose"), ["maicong.cn", "mchose.com.cn", "mchose.store"]),
        (("漫步者", "edifier", "hecate"), ["edifier.com", "edifier.com.cn", "hecategaming.com"]),
        (("索尼", "sony"), ["sony.com", "sony.com.cn", "sony.net"]),
        (("罗技", "logitech", "logi"), ["logitech.com", "logitech.com.cn", "logi.com"]),
    )
    for aliases, domains in seeds:
        for alias in aliases:
            op.execute(
                text(
                    "INSERT INTO brands(name,normalized_name,official_domains) "
                    "VALUES (:name,:key,CAST(:domains AS jsonb)) "
                    "ON CONFLICT(normalized_name) DO UPDATE SET official_domains="
                    "COALESCE(brands.official_domains,EXCLUDED.official_domains)"
                ).bindparams(
                    name=alias.upper() if alias.isascii() else alias,
                    key=alias,
                    domains=json.dumps(domains),
                )
            )


def downgrade():
    raise RuntimeError("Preserve web provenance; review any destructive downgrade separately")
