"""HTTP journeys on a migrated, disposable PostgreSQL database.

Deliberately outside tests/: its conftest installs SQLite and shared sessions.
"""

import os
import secrets
import socket
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from pydantic_settings.sources import DotEnvSettingsSource
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "e2e-password-2026-only"


@pytest.fixture(scope="session")
def runtime(tmp_path_factory):
    dsn = os.environ.get("E2E_POSTGRES_DSN")
    if not dsn:
        pytest.fail("E2E_POSTGRES_DSN must point to a disposable PostgreSQL service")
    url = make_url(dsn)
    if url.get_backend_name() != "postgresql":
        pytest.fail("E2E requires PostgreSQL; SQLite fallback is forbidden")
    name = "lernium_e2e_" + uuid4().hex
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    database_url = url.set(database=name).render_as_string(hide_password=False)
    patch = pytest.MonkeyPatch()
    servers = []
    engine = None
    try:
        # Never load developer credentials or upload paths from the repository .env.
        patch.setattr(DotEnvSettingsSource, "__call__", lambda self: {})
        for key, value in {
            "ENV": "test",
            "DEBUG": "false",
            "DATABASE_URL": database_url,
            "JWT_SECRET": secrets.token_urlsafe(48),
            "ADMIN_JWT_SECRET": secrets.token_urlsafe(48),
            "UPLOAD_DIR": str(tmp_path_factory.mktemp("e2e-uploads")),
            "AI_ROUTING_MODE": "off",
            "AI_ROUTING_CANDIDATES": "[]",
            "AI_PROVIDERS": "[]",
            "VSELLM_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "SMTP_HOST": "",
            "JOB_EAGER": "true",
            "AUTH_RATE_LIMIT_ENABLED": "false",
        }.items():
            patch.setenv(key, value)

        from app import models  # noqa: F401
        from app.database.db import SessionLocal, engine

        assert engine.url.database == name, "Application was imported before E2E configuration"
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")

        from app.ai import gateway
        from app.application import create_app
        from app.services import mail_service, pipeline_service

        external_calls = []

        def unexpected_external_call(*args, **kwargs):
            external_calls.append("LLM/mail")
            raise RuntimeError("Unexpected external call: install an explicit scenario stub")

        connect = socket.socket.connect
        connect_ex = socket.socket.connect_ex

        def guarded_connect(sock, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                external_calls.append(str(address))
                raise RuntimeError("E2E blocks external socket connections")
            return connect(sock, address)

        def guarded_connect_ex(sock, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                external_calls.append(str(address))
                raise RuntimeError("E2E blocks external socket connections")
            return connect_ex(sock, address)

        patch.setattr(socket.socket, "connect", guarded_connect)
        patch.setattr(socket.socket, "connect_ex", guarded_connect_ex)

        patch.setattr(gateway, "configured", lambda: False)
        patch.setattr(gateway, "invoke_messages", unexpected_external_call)
        patch.setattr(mail_service, "send_account_mail", unexpected_external_call)
        patch.setattr(
            pipeline_service,
            "replace_document_embeddings",
            lambda document_id, version, chunks: [
                f"e2e:{document_id}:{version}:{chunk['chunk_id']}" for chunk in chunks
            ],
        )

        # HTTP transport stays real; no dependency overrides or shared ORM session.
        urls = {}
        for surface in ("platform", "admin"):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            server = uvicorn.Server(
                uvicorn.Config(create_app(surface), log_level="warning", lifespan="off")
            )
            thread = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            servers.append((server, thread, listener))
            thread.start()
            deadline = time.monotonic() + 20
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    pytest.fail(f"{surface} HTTP server did not start")
                time.sleep(0.05)
            urls[surface] = f"http://127.0.0.1:{listener.getsockname()[1]}"
        yield {"urls": urls, "sessions": SessionLocal, "external_calls": external_calls}
    finally:
        for server, _, _ in servers:
            server.should_exit = True
        for server, thread, listener in servers:
            thread.join(timeout=10)
            listener.close()
        if engine is not None:
            engine.dispose()
        patch.undo()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin_engine.dispose()


def checked(response, status=200):
    assert response.status_code == status, (
        f"{response.request.method} {response.request.url}: {response.text}"
    )
    return response.json() if response.content else None


class Actor:
    def __init__(self, url, email, user_id):
        self.url, self.email, self.id = url, email, user_id
        self.client = httpx.Client(base_url=url, timeout=30, trust_env=False)
        self.login()

    def login(self):
        result = checked(
            self.client.post("/api/auth/login", json={"email": self.email, "password": PASSWORD})
        )
        self.client.headers["Authorization"] = "Bearer " + result["access_token"]

    def request(self, method, path, status=200, **kwargs):
        return checked(self.client.request(method, path, **kwargs), status)


@pytest.fixture
def world(runtime):
    from app.core.security import hash_password
    from app.models.administration import Organization
    from app.models.learning import OrganizationLearningSettings
    from app.models.user import User

    suffix = uuid4().hex
    identities = {}
    password_hash = hash_password(PASSWORD)
    with runtime["sessions"]() as db:
        orgs = [Organization(name=f"E2E {suffix} {i}", kind="education") for i in range(2)]
        db.add_all(orgs)
        db.flush()
        for org in orgs:
            db.add(
                OrganizationLearningSettings(
                    organization_id=org.id,
                    profile="education",
                    delivery={"mobile_data_policy": {"material_downloads": True}},
                )
            )
        for key, role, org in (
            ("owner", "owner", orgs[0]),
            ("author", "author", orgs[0]),
            ("learner", "member", orgs[0]),
            ("peer", "member", orgs[0]),
            ("foreign", "author", orgs[1]),
            ("operator", "member", None),
        ):
            user = User(
                email=f"{key}.{suffix}@example.test",
                password_hash=password_hash,
                organization_id=org.id if org else None,
                organization_role=role,
                is_admin=key == "operator",
            )
            db.add(user)
            db.flush()
            identities[key] = (user.email, user.id)
        db.commit()
    actors = {}
    try:
        for key, (email, user_id) in identities.items():
            actors[key] = Actor(
                runtime["urls"]["admin" if key == "operator" else "platform"], email, user_id
            )
        yield actors
    finally:
        for actor in actors.values():
            actor.client.close()
        assert not runtime["external_calls"], runtime["external_calls"]


@pytest.fixture
def course(world):
    author = world["author"]
    course = author.request("POST", "/api/courses/drafts", 201)
    cid = course["id"]
    author.request(
        "PUT",
        f"/api/courses/{cid}",
        json={
            "title": "E2E onboarding",
            "description": "Created through HTTP",
            "expected_revision": 1,
        },
    )
    module = author.request("POST", f"/api/courses/{cid}/modules/", 201, json={"title": "Basics"})
    lesson = author.request(
        "POST",
        f"/api/courses/{cid}/modules/{module['id']}/lessons/",
        201,
        json={"title": "First lesson", "content": "Published explanation."},
    )
    question = author.request(
        "POST",
        f"/api/modules/{module['id']}/tests/",
        201,
        json={"question": "Choose", "answers": ["Correct", "Wrong"], "correct_answer": "Correct"},
    )
    return {"id": cid, "lesson": lesson, "question": question}


def publish_assign(world, course):
    world["author"].request("POST", f"/api/courses/{course['id']}/publish")
    return world["owner"].request(
        "POST",
        f"/api/courses/{course['id']}/assignments",
        201,
        json={"user_ids": [world["learner"].id]},
    )["items"][0]
