"""Cross-request journeys complement the domain contracts in tests/."""

from hashlib import sha256

from conftest import PASSWORD, checked, publish_assign


def test_login_and_tenant_isolation(world, course, runtime):
    assignment = publish_assign(world, course)
    path = f"/api/learning/assignments/{assignment['id']}"
    learner = world["learner"]
    assert learner.request("GET", "/api/auth/me")["id"] == learner.id
    checked(
        learner.client.post(
            "/api/auth/login", json={"email": learner.email, "password": "wrong-password"}
        ),
        401,
    )
    learner.request("GET", path)
    for key in ("foreign", "peer"):
        actor = world[key]
        actor.request("GET", path, 404)
        actor.request(
            "PUT",
            path + "/progress",
            404,
            json={"lesson_id": course["lesson"]["id"], "expected_revision": 0},
        )
        assert actor.request("GET", "/api/learning/assignments")["items"] == []
        actor.request("GET", f"/api/courses/{course['id']}/structure", 404)
    world["author"].request("GET", "/api/admin/organizations", 404)
    world["operator"].request("GET", "/api/courses/", 404)
    token = learner.client.headers["Authorization"]
    checked(world["operator"].client.get("/api/auth/me", headers={"Authorization": token}), 401)
    operator_token = world["operator"].client.headers["Authorization"]
    checked(learner.client.get("/api/auth/me", headers={"Authorization": operator_token}), 401)


def test_create_publish_assign_pins_snapshot(world, course):
    world["owner"].request(
        "POST",
        f"/api/courses/{course['id']}/assignments",
        409,
        json={"user_ids": [world["learner"].id]},
    )
    assignment = publish_assign(world, course)
    path = f"/api/learning/assignments/{assignment['id']}"
    original = world["learner"].request("GET", path)
    assert "correct_answers" not in str(original)
    lesson = course["lesson"]
    world["author"].request(
        "PUT",
        f"/api/lessons/{lesson['id']}",
        json={"content": "New revision.", "expected_revision": lesson["revision"]},
    )
    world["author"].request("POST", f"/api/courses/{course['id']}/publish")
    world["learner"].login()
    restored = world["learner"].request("GET", path)
    assert restored["course_version_id"] == original["course_version_id"]
    assert restored["content"] == original["content"]
    assert restored["content"]["modules"][0]["lessons"][0]["content"] == "Published explanation."


def test_learning_survives_new_session_and_submit_retry(world, course, runtime):
    assignment = publish_assign(world, course)
    learner = world["learner"]
    path = f"/api/learning/assignments/{assignment['id']}"
    progress = {"lesson_id": course["lesson"]["id"], "expected_revision": 0}
    learner.request("PUT", path + "/progress", json=progress)
    learner.request("PUT", path + "/progress", 409, json=progress)
    response_path = path + f"/responses/test:{course['question']['id']}"
    draft = learner.request(
        "PUT", response_path, json={"answer": {"selected": ["Correct"]}, "expected_revision": 0}
    )
    # Close the HTTP connection pool as well as obtaining a fresh login token.
    import httpx

    learner.client.close()
    learner.client = httpx.Client(base_url=learner.url, timeout=30, trust_env=False)
    learner.login()
    restored = learner.request("GET", path)
    assert restored["last_lesson_id"] == course["lesson"]["id"]
    assert restored["responses"][0]["answer"] == {"selected": ["Correct"]}
    learner.request(
        "PUT", response_path, 409, json={"answer": {"selected": ["Wrong"]}, "expected_revision": 0}
    )
    payload = {
        "answer": {"selected": ["Correct"]},
        "expected_revision": draft["revision"],
        "submission_key": "e2e-submit-retry",
    }
    first = learner.request("POST", response_path + "/submit", json=payload)
    retry = learner.request("POST", response_path + "/submit", json=payload)
    assert first["id"] == retry["id"] and first["points"] == 1
    learner.request(
        "POST", response_path + "/submit", 409, json={**payload, "answer": {"selected": ["Wrong"]}}
    )
    assert learner.request("GET", path)["status"] == "completed"
    from app.models.learning import LearningAssignment, LearningResponse

    with runtime["sessions"]() as db:
        assert db.get(LearningAssignment, assignment["id"]).completed_at is not None
        assert db.query(LearningResponse).filter_by(assignment_id=assignment["id"]).count() == 1


def material(world, course):
    author = world["author"]
    cid = course["id"]
    content = b"# Onboarding\n\nApproved source material for learners.\n"
    document = author.request(
        "POST",
        f"/api/courses/{cid}/documents",
        202,
        files={"file": ("onboarding.txt", content, "text/plain")},
    )
    author.request("POST", f"/api/documents/{document['id']}/reindex")
    sources = author.request("GET", f"/api/courses/{cid}/source-release")
    source = sources["items"][0]
    author.request(
        "PUT",
        f"/api/courses/{cid}/source-release/{document['id']}",
        json={
            "learner_visible": True,
            "confirm_original_safe": True,
            "expected_revision": sources["content_revision"],
            "document_version": source["version"],
            "content_hash": source["content_hash"],
        },
    )
    return document["id"], content


def test_upload_publish_download_and_isolation(world, course):
    document_id, content = material(world, course)
    assignment = publish_assign(world, course)
    path = f"/api/learning/assignments/{assignment['id']}/sources/{document_id}/content"
    response = world["learner"].client.get(path)
    assert response.status_code == 200, response.text
    assert response.content == content
    snapshot = world["learner"].request("GET", f"/api/learning/assignments/{assignment['id']}")[
        "content"
    ]
    assert snapshot["sources"][0]["content_hash"] == sha256(content).hexdigest()
    assert "storage_key" not in str(snapshot)
    for actor in (world["foreign"], world["peer"]):
        actor.request("GET", path, 404)
    checked(world["learner"].client.get(path, headers={"Authorization": ""}), 401)


def test_revocation_and_disabled_account_reject_old_session(world, course, runtime):
    document_id, content = material(world, course)
    assignment = publish_assign(world, course)
    learner = world["learner"]
    path = f"/api/learning/assignments/{assignment['id']}"
    source_path = path + f"/sources/{document_id}/content"
    learner.request(
        "PUT",
        path + "/progress",
        json={"lesson_id": course["lesson"]["id"], "expected_revision": 0},
    )
    assert learner.client.get(source_path).content == content
    token = learner.client.headers["Authorization"]
    world["owner"].request("DELETE", path)
    assert learner.client.headers["Authorization"] == token
    learner.request("GET", path, 403)
    learner.request("GET", source_path, 403)
    learner.request(
        "PUT",
        path + "/progress",
        403,
        json={"lesson_id": course["lesson"]["id"], "expected_revision": 1},
    )
    learner.request("GET", path + "/results")
    from app.models.learning import LearningAssignment
    from app.models.user import User

    with runtime["sessions"]() as db:
        user = db.get(User, learner.id)
        org_id, version = user.organization_id, user.auth_version
    world["operator"].request(
        "PUT",
        f"/api/admin/users/{learner.id}",
        json={
            "is_active": False,
            "organization_id": org_id,
            "expected_auth_version": version,
        },
    )
    for endpoint in ("/api/auth/me", path, source_path):
        learner.request("GET", endpoint, 401)
    checked(
        learner.client.post("/api/auth/login", json={"email": learner.email, "password": PASSWORD}),
        401,
    )
    with runtime["sessions"]() as db:
        row = db.get(LearningAssignment, assignment["id"])
        assert row.state == "revoked"
        assert row.completed_lessons == [course["lesson"]["id"]]
