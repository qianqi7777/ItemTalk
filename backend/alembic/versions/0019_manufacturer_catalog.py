"""Fill only unregistered reviewed manufacturers; preserve explicit operator decisions."""

import json

from sqlalchemy import text

from alembic import op

revision = "0019_manufacturer_catalog"
down_revision = "0018_responses_protocol"
branch_labels = None
depends_on = None


def upgrade():
    # Frozen migration inputs, independent of future application catalog changes.
    seeds = (
        (
            ("弱水时砂", "rose technics", "rosetechnics", "roseselsa"),
            ["roseselsa.com", "rosetechnic.com", "rosetechnics.com"],
        ),
        (("迈从 (mchose)", "迈从(mchose)"), ["maicong.cn", "mchose.com.cn", "mchose.store"]),
    )
    for aliases, domains in seeds:
        for alias in aliases:
            op.execute(
                text(
                    "INSERT INTO brands(name,normalized_name,official_domains) "
                    "VALUES (:name,:key,CAST(:domains AS jsonb)) "
                    "ON CONFLICT(normalized_name) DO UPDATE SET official_domains="
                    "COALESCE(brands.official_domains,EXCLUDED.official_domains)"
                ).bindparams(name=alias, key=alias, domains=json.dumps(domains))
            )


def downgrade():
    raise RuntimeError("Retain reviewed manufacturer metadata; use compatible application rollback")
