"""Removal keeps learning evidence, enforces scope and invalidates access."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from app.models.administration import AdminEvent
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.generation_run import GenerationRun
from app.models.integrations import MobilePushDevice, MobileSession
from app.models.learning import LearningAssignment, LearningPerson, PublishedLearningContent
from app.models.user import User
from app.schemas.learning import LearningRules
from tests.test_business_positions import company, headers, make_position


def assignment(db, org, users):
    course = Course(
        name="Регламент с историей",
        owner_id=users["manager"].id,
        organization_id=org.id,
        publication_status="published",
    )
    db.add(course)
    db.flush()
    version = CourseVersion(
        course_id=course.id, name=course.name, revision=1, publication_status="published"
    )
    db.add(version)
    db.flush()
    snapshot = {
        "course": {"id": course.id, "name": course.name, "description": ""},
        "modules": [],
        "assessments": [],
        "final_tests": [],
    }
    db.add(
        PublishedLearningContent(
            course_version_id=version.id, organization_id=org.id, snapshot=snapshot
        )
    )
    row = LearningAssignment(
        organization_id=org.id,
        course_version_id=version.id,
        user_id=users["employee"].id,
        assigned_by=users["manager"].id,
        rules_revision=1,
        rules=LearningRules().model_dump(mode="json"),
        completed_lessons=[19],
        completed_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return course, row, snapshot


def remove(client, actor, target, version=None):
    return client.request(
        "DELETE",
        f"/api/business/employees/{target.id}",
        headers=headers(actor),
        json={"expected_auth_version": target.auth_version if version is None else version},
    )


def test_remove_restore_preserves_history_and_revokes_all_sessions(client, db_session, company):
    org, users = company
    manager, employee = users["manager"], users["employee"]
    _, row, snapshot = assignment(db_session, org, users)
    db_session.add(
        LearningPerson(user_id=employee.id, organization_id=org.id, position="Сотрудник")
    )
    db_session.add(
        MobileSession(
            id="removal-session",
            user_id=employee.id,
            auth_version=0,
            refresh_token_hash="test-hash",
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
    )
    db_session.flush()
    db_session.add(
        MobilePushDevice(
            id="removal-device",
            session_id="removal-session",
            token_hash="device-hash",
            platform="android",
        )
    )
    db_session.commit()
    old = headers(employee)
    response = remove(client, manager, employee, 0)
    assert response.status_code == 200, response.text
    assert response.json()["organization_removed_at"] and not response.json()["is_active"]
    assert client.get("/api/auth/me", headers=old).status_code == 401
    assert db_session.get(MobileSession, "removal-session").revoked_at
    assert db_session.query(MobilePushDevice).count() == 0
    assert client.get("/api/business/employees", headers=headers(manager)).json()["total"] == 0
    archived = client.get("/api/business/employees?removed=true", headers=headers(manager)).json()
    assert [p["id"] for p in archived["items"]] == [employee.id]
    report = client.get("/api/team/results", headers=headers(manager))
    assert report.status_code == 200, report.text
    assert report.json()["items"][0]["id"] == row.id
    assert db_session.get(LearningAssignment, row.id).completed_lessons == [19]
    assert db_session.get(PublishedLearningContent, row.course_version_id).snapshot == snapshot
    assert db_session.get(LearningPerson, employee.id)
    assert remove(client, manager, employee, 0).status_code == 200  # lost-response retry
    assert db_session.query(AdminEvent).filter_by(action="business.employee_removed").count() == 1
    assert (
        client.put(
            f"/api/business/employees/{employee.id}/access",
            headers=headers(manager),
            json={"is_active": True, "expected_auth_version": 1},
        ).status_code
        == 409
    )
    restore = client.post(
        f"/api/business/employees/{employee.id}/restore",
        headers=headers(manager),
        json={"expected_auth_version": 1},
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["is_active"] and restore.json()["organization_removed_at"] is None
    assert client.get("/api/auth/me", headers=old).status_code == 401
    assert client.get("/api/auth/me", headers=headers(employee)).status_code == 200
    assert db_session.get(MobileSession, "removal-session").revoked_at
    assert remove(client, manager, employee, 0).status_code == 409  # old request after restore


def test_removal_permissions_revision_and_seat_limit(client, db_session, company):
    org, users = company
    target, manager = users["employee"], users["manager"]
    assert remove(client, users["outsider"], target).status_code == 404
    assert remove(client, target, target).status_code == 403
    assert remove(client, manager, users["owner"]).status_code in (403, 404)
    assert remove(client, users["owner"], users["owner"]).status_code == 403
    assert remove(client, manager, target, 10).status_code == 409
    assert remove(client, manager, target).status_code == 200
    org.seat_limit = 2
    db_session.commit()
    denied = client.post(
        f"/api/business/employees/{target.id}/restore",
        headers=headers(manager),
        json={"expected_auth_version": 1},
    )
    assert denied.status_code == 409
    assert not db_session.get(User, target.id).is_active
    assert (
        client.get(
            "/api/business/employees?removed=true", headers=headers(users["outsider"])
        ).json()["items"]
        == []
    )


@pytest.mark.parametrize("role", ["candidate", "manager"])
def test_owner_removes_all_non_owner_roles_and_clears_manager_team(
    client, db_session, company, role
):
    _, users = company
    target = users["manager"] if role == "manager" else users["employee"]
    target.organization_role = role
    db_session.commit()
    assert remove(client, users["owner"], target).status_code == 200
    if role == "manager":
        assert db_session.get(User, users["employee"].id).manager_id is None
        restored = client.post(
            f"/api/business/employees/{target.id}/restore",
            headers=headers(users["owner"]),
            json={"expected_auth_version": 1},
        )
        assert restored.status_code == 200
        assert db_session.get(User, users["employee"].id).manager_id is None


def test_regulation_delete_guards_and_keeps_assigned_snapshot(client, db_session, company):
    org, users = company
    course, row, snapshot = assignment(db_session, org, users)
    position = make_position(client, users["manager"])
    course.position_id = position["id"]
    db_session.commit()
    url = f"/api/courses/{course.id}?expected_revision=1"
    assert client.delete(url, headers=headers(users["outsider"])).status_code == 404
    assert client.delete(url, headers=headers(users["employee"])).status_code == 404
    assert (
        client.delete(url.replace("=1", "=99"), headers=headers(users["manager"])).status_code
        == 409
    )
    run = GenerationRun(
        owner_id=users["manager"].id,
        course_id=course.id,
        run_type="graph_generation",
        status="queued",
    )
    db_session.add(run)
    db_session.commit()
    assert client.delete(url, headers=headers(users["manager"])).status_code == 409
    run.status = "succeeded"
    db_session.commit()
    before = client.get(f"/api/learning/assignments/{row.id}", headers=headers(users["employee"]))
    assert before.status_code == 200, before.text
    assert client.delete(url, headers=headers(users["manager"])).status_code == 200
    assert client.delete(url, headers=headers(users["manager"])).status_code == 200
    assert (
        client.get(
            f"/api/business/positions/{position['id']}/regulations",
            headers=headers(users["manager"]),
        ).json()["items"]
        == []
    )
    after = client.get(f"/api/learning/assignments/{row.id}", headers=headers(users["employee"]))
    assert after.status_code == 200, after.text
    assert after.json() == before.json()
    assert db_session.get(PublishedLearningContent, row.course_version_id).snapshot == deepcopy(
        snapshot
    )
    assert db_session.query(AdminEvent).filter_by(action="course.deleted").count() == 1
