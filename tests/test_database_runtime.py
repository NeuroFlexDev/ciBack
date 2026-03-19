from app.core.config import normalize_database_url
from app.database.db import build_engine_options


def test_normalize_database_url_rewrites_asyncpg_driver():
    url = "postgresql+asyncpg://user:pass@db:5432/app"
    assert normalize_database_url(url) == "postgresql+psycopg2://user:pass@db:5432/app"


def test_build_engine_options_for_sqlite_enable_thread_override():
    options = build_engine_options("sqlite:///./database.db")
    assert options["future"] is True
    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {"check_same_thread": False}


def test_build_engine_options_for_postgres_keep_default_connect_args():
    options = build_engine_options("postgresql+psycopg2://user:pass@db:5432/app")
    assert options["future"] is True
    assert options["pool_pre_ping"] is True
    assert "connect_args" not in options
