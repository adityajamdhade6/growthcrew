import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel, create_engine

from growthcrew import audit, config
from growthcrew.db import models  # noqa: F401  (registers tables on SQLModel.metadata)

MIGRATIONS = Path(__file__).parent / "migrations"
# Content memory embeddings: the lexical embedder's size (memory/embed.py).
VECTOR_DIMENSIONS = 512


def engine_url(url: str) -> str:
    """postgres:// and postgresql:// URLs use the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


@lru_cache
def get_engine() -> Engine:
    url = engine_url(config.DATABASE_URL)
    kwargs = (
        {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20} if "postgresql" in url else {}
    )
    engine = create_engine(url, **kwargs)
    if os.getenv("GROWTHCREW_MIGRATIONS") == "alembic":
        upgrade(engine)
    else:
        init_db(engine)
    return engine


def upgrade(engine: Engine) -> None:
    """Apply every Alembic migration not yet applied (production and Postgres)."""
    from alembic import command
    from alembic.config import Config

    settings = Config()
    settings.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as connection:
        settings.attributes["connection"] = connection
        command.upgrade(settings, "head")
    postgres_extras(engine)
    audit.protect(engine)


def postgres_extras(engine: Engine) -> None:
    """On Postgres: pgvector for content memory, with an HNSW index for cosine search."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(
            f"ALTER TABLE memorypiece ADD COLUMN IF NOT EXISTS embedding_vec "
            f"vector({VECTOR_DIMENSIONS})"))  # fmt: skip
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS memorypiece_embedding_hnsw ON memorypiece "
            "USING hnsw (embedding_vec vector_cosine_ops)"))  # fmt: skip


def init_db(engine: Engine) -> None:
    """Create missing tables, then add any columns the models have gained since.

    This covers the only kind of schema change made so far: new tables and new columns. A
    rename, a type change or a dropped column still needs a real migration (db/migrations/).
    """
    SQLModel.metadata.create_all(engine)
    postgres_extras(engine)
    audit.protect(engine)
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                ddl += column.type.compile(engine.dialect)
                default = getattr(column.default, "arg", None)
                if isinstance(default, bool):
                    ddl += f" DEFAULT {int(default)}"
                elif isinstance(default, int | float):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                connection.execute(text(ddl))
