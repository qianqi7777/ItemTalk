"""Add separately measured trigram/HNSW indexes without changing evidence identity."""

from alembic import op

revision = "0011_retrieval_indexes"
down_revision = "0010_v2_operations"
branch_labels = None
depends_on = None


def upgrade():
    # strict_order迭代扫描需要pgvector 0.8+；不在迁移中自动升级生产扩展。
    op.execute("""
        DO $$ BEGIN
          IF (SELECT string_to_array(extversion,'.')::integer[] < ARRAY[0,8,0]
              FROM pg_extension WHERE extname='vector') IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'pgvector 0.8.0 or newer is required; review extension separately';
          END IF;
        END $$
    """)
    op.execute(
        "CREATE INDEX ix_chunks_word_distance ON knowledge_chunks "
        "USING gist(content gist_trgm_ops(siglen=128))"
    )
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON knowledge_chunks "
        "USING hnsw(embedding vector_cosine_ops) WITH(m=16,ef_construction=128)"
    )


def downgrade():
    raise RuntimeError(
        "Review index rollback separately; do not downgrade production automatically"
    )
