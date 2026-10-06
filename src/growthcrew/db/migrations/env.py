"""Alembic environment: the SQLModel tables, against DATABASE_URL."""

from alembic import context
from sqlmodel import SQLModel

from growthcrew import config as settings
from growthcrew.db import models  # noqa: F401  (registers the tables)
from growthcrew.db.session import engine_url

target_metadata = SQLModel.metadata


def run() -> None:
    from sqlalchemy import create_engine

    url = context.config.get_main_option("sqlalchemy.url") or engine_url(settings.DATABASE_URL)
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                          render_as_batch=True)  # fmt: skip
        with context.begin_transaction():
            context.run_migrations()
        return
    connectable = context.config.attributes.get("connection") or create_engine(url)
    if hasattr(connectable, "connect") and not hasattr(connectable, "dialect_options"):
        with connectable.connect() as connection:
            _migrate(connection)
    else:
        _migrate(connectable)


def _migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite")  # fmt: skip
    with context.begin_transaction():
        context.run_migrations()


run()
