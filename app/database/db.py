# app/database/db.py
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./database.db")

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _ensure_column(table_name: str, column_name: str, ddl: str) -> None:
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in existing_columns:
        return

    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def initialize_database() -> None:
    # Импортируем модели явно, чтобы metadata гарантированно знала о всех таблицах.
    from app.models import user  # noqa: F401
    from app.models import course  # noqa: F401
    from app.models import course_structure  # noqa: F401
    from app.models import module  # noqa: F401
    from app.models import lesson  # noqa: F401
    from app.models import theory  # noqa: F401
    from app.models import feedback  # noqa: F401
    from app.models import task  # noqa: F401
    from app.models import test  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_column("courses", "owner_id", "owner_id INTEGER")
    _ensure_column("course_structure", "owner_id", "owner_id INTEGER")


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
