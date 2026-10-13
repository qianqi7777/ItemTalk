"""MVP schema, ownership constraints, queue and deferred input validation."""

from pathlib import Path

from alembic import op

revision = "0001_mvp"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    # 此迁移包含 PL/pgSQL 的 %I 格式占位；直接交给驱动且不传参数，
    # 避免 SQLAlchemy 空参数集合被 psycopg 误识别为客户端绑定。
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(sql)


def downgrade():
    raise RuntimeError("Restore a verified backup; destructive downgrade is intentionally disabled")
