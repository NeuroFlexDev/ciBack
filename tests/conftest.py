# tests/conftest.py
import os
from pathlib import Path
from typing import Generator

import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

TESTS_ROOT = Path(__file__).resolve().parent
TEST_TMP_ROOT = TESTS_ROOT / "_tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
TEST_RUNTIME_DB = TEST_TMP_ROOT / "runtime.db"

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("ENV", "dev")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_RUNTIME_DB.as_posix()}")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("JWT_ALG", "HS256")
os.environ.setdefault("LOG_LEVEL", "INFO")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.db import Base, get_db
from main import app


@pytest.fixture(scope="session")
def repo_tmp_dir() -> Path:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_TMP_ROOT


@pytest.fixture(scope="session")
def engine():
    # чистый sqlite в памяти
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="function")
def db_session(engine) -> Generator:
    connection = engine.connect()
    trans = connection.begin()
    SessionLocal = sessionmaker(bind=connection)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture(scope="function")
def client(db_session):
    # оверрайдим зависимость FastAPI на наш сессионный
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
