"""F1 课程/班级/学期：结构维护、绑定知识库、学生「我的课程」。"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import get_db
from backend.models import Base, KnowledgeBase, User
from backend.routers import auth, courses
from backend.security import hash_password
from backend.seed_data import DEFAULT_ADMIN


def _build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(courses.router, prefix="/api/courses")

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture()
def api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    app = _build_app(SessionFactory)

    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()
    monkeypatch.setattr(auth, "_REG_MAX", 1000)
    monkeypatch.setattr(auth, "_LOGIN_MAX_FAILS", 1000)
    monkeypatch.setattr(auth, "_IP_MAX_FAILS", 1000)

    with SessionFactory() as db:
        db.add(
            User(
                account=DEFAULT_ADMIN[0],
                password_hash=hash_password(DEFAULT_ADMIN[1]),
                username=DEFAULT_ADMIN[2],
                role="admin",
            )
        )
        db.add(KnowledgeBase(name="数据结构", description="", created_by=1))
        db.commit()

    client = TestClient(app)
    try:
        yield client, SessionFactory
    finally:
        client.close()
        engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, account: str, password: str = "secret1x") -> str:
    r = client.post("/api/auth/login", json={"account": account, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _register(client: TestClient, account: str, username: str = "课程学生") -> str:
    r = client.post(
        "/api/auth/register",
        json={"account": account, "password": "secret1x", "username": username},
    )
    assert r.status_code == 200, r.text
    return _login(client, account)


def _admin(client: TestClient) -> str:
    return _login(client, DEFAULT_ADMIN[0], DEFAULT_ADMIN[1])


def _seed_structure(client: TestClient) -> tuple[int, int, int]:
    admin = _admin(client)
    sem = client.post(
        "/api/courses/semesters",
        json={"name": "2026 秋", "start_date": "2026-09-01", "end_date": "2027-01-15", "is_active": True},
        headers=_auth(admin),
    )
    assert sem.status_code == 201, sem.text
    semester_id = sem.json()["id"]

    course = client.post(
        "/api/courses/courses",
        json={"code": "CS101", "name": "数据结构", "semester_id": semester_id, "knowledge_base_id": 1},
        headers=_auth(admin),
    )
    assert course.status_code == 201, course.text
    course_id = course.json()["id"]

    cls = client.post(
        f"/api/courses/courses/{course_id}/classes",
        json={"name": "1 班"},
        headers=_auth(admin),
    )
    assert cls.status_code == 201, cls.text
    return semester_id, course_id, cls.json()["id"]


def test_student_cannot_manage_structure(api):
    client, _ = api
    student = _register(client, "24007001")
    assert client.post("/api/courses/semesters", json={"name": "x"}, headers=_auth(student)).status_code == 403
    assert (
        client.post(
            "/api/courses/courses", json={"code": "X1", "name": "x"}, headers=_auth(student)
        ).status_code
        == 403
    )


def test_admin_crud_and_binding(api):
    client, _ = api
    semester_id, course_id, class_id = _seed_structure(client)
    admin = _admin(client)

    rows = client.get("/api/courses/courses", headers=_auth(admin)).json()["items"]
    assert rows[0]["knowledge_base_id"] == 1
    assert rows[0]["knowledge_base_name"] == "数据结构"
    assert rows[0]["class_count"] == 1

    # 学期下有课程时不允许直接删
    r = client.delete(f"/api/courses/semesters/{semester_id}", headers=_auth(admin))
    assert r.status_code == 409

    # 课程编号唯一
    dup = client.post(
        "/api/courses/courses", json={"code": "CS101", "name": "重复"}, headers=_auth(admin)
    )
    assert dup.status_code == 409

    assert client.delete(f"/api/courses/classes/{class_id}", headers=_auth(admin)).status_code == 200
    assert client.delete(f"/api/courses/courses/{course_id}", headers=_auth(admin)).status_code == 200
    assert client.delete(f"/api/courses/semesters/{semester_id}", headers=_auth(admin)).status_code == 200


def test_student_my_courses_only_after_enrolled(api):
    client, _ = api
    _, course_id, class_id = _seed_structure(client)
    student = _register(client, "24007002", "选课学生")

    assert client.get("/api/courses/my/courses", headers=_auth(student)).json()["items"] == []

    admin = _admin(client)
    r = client.post(
        f"/api/courses/classes/{class_id}/members",
        json={"account": "24007002"},
        headers=_auth(admin),
    )
    assert r.status_code == 200 and r.json()["added"] is True
    # 重复添加幂等
    again = client.post(
        f"/api/courses/classes/{class_id}/members",
        json={"account": "24007002"},
        headers=_auth(admin),
    )
    assert again.status_code == 200 and again.json()["added"] is False

    mine = client.get("/api/courses/my/courses", headers=_auth(student)).json()["items"]
    assert len(mine) == 1
    assert mine[0]["code"] == "CS101"
    assert mine[0]["knowledge_base"]["id"] == 1
    assert mine[0]["classes"][0]["name"] == "1 班"

    members = client.get(f"/api/courses/classes/{class_id}/members", headers=_auth(admin)).json()
    assert [m["account"] for m in members["items"]] == ["24007002"]
    member_id = members["items"][0]["user_id"]

    # 非本班学生看不到成员
    outsider = _register(client, "24007003", "旁听生")
    assert client.get(f"/api/courses/classes/{class_id}/members", headers=_auth(outsider)).status_code == 403

    assert client.delete(
        f"/api/courses/classes/{class_id}/members/{member_id}", headers=_auth(admin)
    ).status_code == 200
    assert client.get("/api/courses/my/courses", headers=_auth(student)).json()["items"] == []


def test_only_students_can_join_class(api):
    client, _ = api
    _, _, class_id = _seed_structure(client)
    admin = _admin(client)
    r = client.post(
        f"/api/courses/classes/{class_id}/members",
        json={"account": DEFAULT_ADMIN[0]},
        headers=_auth(admin),
    )
    assert r.status_code == 422
