"""核心逻辑回归（不依赖网络 / 正式数据库）。"""
from __future__ import annotations

import asyncio
import os
import time

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.models import Base, KnowledgeBase, KnowledgePoint, User
from backend.services.kb_scope import (
    assert_writable_course,
    can_view_point,
    visible_course_names,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _user(db: Session, username: str, role: str) -> User:
    user = User(username=username, password_hash="x", real_name=username, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_course_visibility_and_write_guard(db):
    admin = _user(db, "10000001", "admin")
    alice = _user(db, "10000002", "student")
    bob = _user(db, "10000003", "student")
    db.add_all(
        [
            KnowledgeBase(name="公开库", created_by=admin.id),
            KnowledgeBase(name="小A私有库", created_by=alice.id),
            KnowledgeBase(name="小B私有库", created_by=bob.id),
        ]
    )
    db.commit()

    assert visible_course_names(alice, db) == {"公开库", "小A私有库"}

    for bad in ("", "   ", "不存在的库"):
        with pytest.raises(HTTPException) as exc:
            assert_writable_course(alice, db, bad)
        assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        assert_writable_course(alice, db, "小B私有库")
    assert exc.value.status_code == 403

    assert_writable_course(alice, db, "公开库")
    assert_writable_course(alice, db, "小A私有库")


def test_point_visibility(db):
    admin = _user(db, "10000011", "admin")
    alice = _user(db, "10000012", "student")
    bob = _user(db, "10000013", "student")
    db.add_all(
        [
            KnowledgeBase(name="公开库", created_by=admin.id),
            KnowledgeBase(name="小B私有库", created_by=bob.id),
        ]
    )
    db.commit()
    public_point = KnowledgePoint(
        title="公开", content="x", course="公开库", created_by=admin.id
    )
    private_point = KnowledgePoint(
        title="私有", content="x", course="小B私有库", created_by=bob.id
    )
    db.add_all([public_point, private_point])
    db.commit()

    assert can_view_point(alice, db, public_point)
    assert not can_view_point(alice, db, private_point)


def test_kb_list_helpers():
    from backend.routers.chat import _kb_from_conv, _kb_list

    assert _kb_list('["数据结构", "操作系统"]') == ["数据结构", "操作系统"]
    assert _kb_list('["", null, "A"]') == ["A"]
    assert _kb_list("not-json") == []
    assert _kb_from_conv('["操作系统", "数据结构"]') == "操作系统"
    assert _kb_from_conv("not-json") == ""


def test_vector_doc_helper():
    from backend.services.vector_store import _doc

    doc = _doc("正文", {"source_type": "document", "doc_id": "1"})
    assert doc.page_content == "正文"
    assert doc.metadata["doc_id"] == "1"


def test_course_related_short_circuit():
    from backend.services.rag_service import is_course_related

    # 这几条都在关键词阶段短路，不会调用模型
    assert asyncio.run(is_course_related("红黑树的插入过程")) is True
    assert asyncio.run(is_course_related("今天天气如何")) is False
    assert asyncio.run(is_course_related("考研面试里的多线程")) is False


def test_unmatched_marker():
    from backend.services.rag_service import is_unmatched_answer

    assert is_unmatched_answer("【知识库未收录】知识库中暂无相关内容")
    assert not is_unmatched_answer("栈是后进先出的线性表")


def test_avatar_path_guard():
    from backend.routers.auth import _avatar_file

    path = _avatar_file("/static/avatars/u1_abc.png", 1)
    assert path is not None and path.name == "u1_abc.png"
    assert _avatar_file("/static/avatars/u2_abc.png", 1) is None
    assert _avatar_file("/static/avatars/../u1_x.png", 1) is None
    assert _avatar_file("", 1) is None


def test_rate_limit_prune():
    from backend.routers import auth as auth_mod

    now = time.monotonic()
    with auth_mod._login_lock:
        auth_mod._login_fails.clear()
        auth_mod._reg_log.clear()
        auth_mod._login_fails["old|1"] = [now - 700]
        auth_mod._login_fails["new|1"] = [now - 10]
        auth_mod._reg_log["old-ip"] = [now - 700]
        auth_mod._reg_log["new-ip"] = [now - 10]
        auth_mod._last_prune = 0.0
        auth_mod._prune_expired(now)

        assert "old|1" not in auth_mod._login_fails
        assert "new|1" in auth_mod._login_fails
        assert "old-ip" not in auth_mod._reg_log
        assert "new-ip" in auth_mod._reg_log
