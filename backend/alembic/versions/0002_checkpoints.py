"""Initialize LangGraph PostgreSQL checkpoints using the migration role."""

from app.agent_probe import saver

revision = "0002_checkpoints"
down_revision = "0001_mvp"
branch_labels = None
depends_on = None


def upgrade():
    # Checkpointer 的建表/索引迁移使用独立 autocommit 连接；应用角色只读写。
    with saver() as checkpointer:
        checkpointer.setup()


def downgrade():
    raise RuntimeError("Checkpoint downgrade requires an explicit backup restore")
