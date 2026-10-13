"""Enable bounded terminal guidance checkpoint retention jobs."""

from alembic import op

revision = "0006_checkpoint_retention"
down_revision = "0005_v1_guidance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_job_type_check")
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check CHECK "
        "(job_type IN ('identify','research','parse','embed','manual','asset_cleanup',"
        "'user_cleanup','memory_extract','checkpoint_cleanup'))"
    )


def downgrade():
    raise RuntimeError("Checkpoint retention changes require a verified backup restore")
