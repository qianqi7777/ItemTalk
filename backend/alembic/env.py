from sqlalchemy import create_engine

from alembic import context
from app.config import settings

if context.is_offline_mode():
    context.configure(url=settings().db_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(settings().db_url()).connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
