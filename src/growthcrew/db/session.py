from functools import lru_cache

from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel, create_engine

from growthcrew import config
from growthcrew.db import models  # noqa: F401  (registers tables on SQLModel.metadata)


@lru_cache
def get_engine() -> Engine:
    engine = create_engine(config.DATABASE_URL)
    init_db(engine)
    return engine


def init_db(engine: Engine) -> None:
    """Create missing tables, then add any columns the models have gained since.

    This covers the only kind of schema change made so far: new tables and new columns. A
    rename, a type change or a dropped column still needs a real migration (db/migrations/).
    """
    SQLModel.metadata.create_all(engine)
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
