"""安全启动、鉴权强制改密与 RAG 三态回归。"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from backend.models import Base, User


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def test_rotate_insecure_admin_passwords_writes_private_bootstrap_file(tmp_path, caplog):
    """默认管理员密码必须在启动时轮换，明文新密码只落本地引导文件，不进日志。"""
    from backend.security import hash_password, verify_password
    from backend.seed_data import DEFAULT_ADMIN
    from backend.services import admin_security

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    credentials = tmp_path / "admin_initial_credentials.txt"
    with Session(engine) as db:
        admin = User(
            account=DEFAULT_ADMIN[0],
            password_hash=hash_password(DEFAULT_ADMIN[1]),
            username=DEFAULT_ADMIN[2],
            role="admin",
        )
        db.add(admin)
        db.commit()

        rotated = admin_security.rotate_insecure_admin_passwords(
            db, credentials_path=credentials
        )

        assert len(rotated) == 1
        account, password = rotated[0]
        assert account == DEFAULT_ADMIN[0]
        assert password != DEFAULT_ADMIN[1]
        db.refresh(admin)
        assert not verify_password(DEFAULT_ADMIN[1], admin.password_hash)
        assert verify_password(password, admin.password_hash)
        assert admin.must_change_password == 1

    content = credentials.read_text(encoding="utf-8")
    assert DEFAULT_ADMIN[0] in content
    assert password in content
    if os.name != "nt":
        assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
    assert DEFAULT_ADMIN[1] not in caplog.text


def test_explicit_strong_admin_password_is_not_replaced(monkeypatch, tmp_path):
    from backend.security import verify_password
    from backend.services import admin_security

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        admin_security.settings, "DEFAULT_ADMIN_PASSWORD", "StrongPass123!"
    )
    monkeypatch.setattr(admin_security.settings, "DATA_DIR", tmp_path)
    with Session(engine) as db:
        admin = admin_security.create_default_admin(db)
        assert verify_password("StrongPass123!", admin.password_hash)
        assert not verify_password("111111", admin.password_hash)
    assert not (tmp_path / "admin_initial_credentials.txt").exists()


def test_weak_explicit_admin_password_is_replaced(monkeypatch, tmp_path):
    from backend.security import verify_password
    from backend.services import admin_security

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(admin_security.settings, "DEFAULT_ADMIN_PASSWORD", "weak12")
    monkeypatch.setattr(admin_security.settings, "DATA_DIR", tmp_path)
    with Session(engine) as db:
        admin = admin_security.create_default_admin(db)
        assert not verify_password("weak12", admin.password_hash)
        assert admin.must_change_password == 1
    assert (tmp_path / "admin_initial_credentials.txt").exists()


def test_default_admin_refuses_non_admin_account_collision():
    from backend.security import hash_password
    from backend.seed_data import DEFAULT_ADMIN
    from backend.services import admin_security

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            User(
                account=DEFAULT_ADMIN[0],
                password_hash=hash_password("student-pass"),
                username="学生占用了管理员账号",
                role="student",
            )
        )
        db.commit()
        with pytest.raises(RuntimeError, match="不是管理员"):
            admin_security.create_default_admin(db)


def test_initial_password_file_failure_rolls_back_admin(monkeypatch, tmp_path):
    from backend.seed_data import DEFAULT_ADMIN
    from backend.services import admin_security

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(admin_security.settings, "DEFAULT_ADMIN_PASSWORD", "")
    monkeypatch.setattr(admin_security.settings, "DATA_DIR", tmp_path)

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(admin_security, "remember_initial_password", fail_write)
    with Session(engine) as db:
        with pytest.raises(OSError, match="disk full"):
            admin_security.create_default_admin(db)
        assert db.scalar(select(User).where(User.account == DEFAULT_ADMIN[0])) is None


def test_get_current_user_blocks_api_until_initial_password_changed():
    """初始随机密码登录后，除查看本人和改密外，其余 API 必须先改密。"""
    from backend.deps import get_current_user
    from backend.security import create_access_token, hash_password

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(
            account="23040101",
            password_hash=hash_password("temporary-password"),
            username="系统管理员",
            role="admin",
            must_change_password=1,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=create_access_token(
                user.id, user.account, user.role, user.token_version
            ),
        )

        with pytest.raises(HTTPException) as exc:
            get_current_user(
                request=_request("/api/chat/sessions"),
                credentials=credentials,
                db=db,
            )
        assert exc.value.status_code == 403
        assert "修改密码" in exc.value.detail

        assert (
            get_current_user(
                request=_request("/api/auth/change-password"),
                credentials=credentials,
                db=db,
            ).id
            == user.id
        )


def test_change_password_requires_eight_chars_and_rejects_reuse():
    from backend.routers.auth import change_password
    from backend.schemas import ChangePasswordRequest
    from backend.security import hash_password

    with pytest.raises(ValidationError):
        ChangePasswordRequest(old_password="old-pass", new_password="short")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(
            account="23040109",
            password_hash=hash_password("same-pass"),
            username="改密测试",
            role="admin",
            must_change_password=1,
        )
        db.add(user)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            change_password(
                ChangePasswordRequest(
                    old_password="same-pass",
                    new_password="same-pass",
                ),
                user,
                db,
            )
        assert exc.value.status_code == 400
        db.refresh(user)
        assert user.must_change_password == 1


def test_can_answer_timeout_is_error_not_unanswerable(monkeypatch):
    """可答性模型超时是系统错误，不能折叠成「资料不可答」。"""
    from langchain_core.documents import Document

    from backend.services import rag_service

    class SlowLLM:
        async def ainvoke(self, _messages):
            await asyncio.sleep(1)

    monkeypatch.setattr(rag_service, "get_llm", lambda: SlowLLM())

    result = asyncio.run(
        rag_service.can_answer(
            "进程和线程有什么区别？",
            [(Document(page_content="进程与线程", metadata={}), 0.8)],
            timeout=0.01,
        )
    )

    assert result is rag_service.Answerability.ERROR


def test_answerability_timeout_streams_retry_error_without_unmatched(monkeypatch):
    """可答性超时只返回可重试错误，不生成拒答事件，也不写未命中反馈。"""
    from langchain_core.documents import Document

    from backend.services import rag_service

    doc = Document(
        page_content="进程是资源分配单位，线程是调度单位。",
        metadata={"course": "操作系统", "title": "进程与线程"},
    )
    monkeypatch.setattr(rag_service, "vector_search", lambda *a, **k: [(doc, 0.9)])
    monkeypatch.setattr(rag_service.settings, "ANSWERABILITY_CHECK", True)

    async def _timeout(*_a, **_k):
        return rag_service.Answerability.ERROR

    monkeypatch.setattr(rag_service, "can_answer", _timeout)

    async def run():
        events = []
        async for event in rag_service._stream_answer_locked(
            "进程和线程有什么区别？"
        ):
            events.append(event)
        return events

    events = asyncio.run(run())
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert "可重试" in errors[0]["message"] or "重试" in errors[0]["message"]
    assert all(event["type"] != "unmatched" for event in events)
    assert all(event["type"] != "sources" for event in events)


def test_knowledge_bases_get_non_reusable_vector_keys():
    """新建同名知识库也不能复用旧向量身份。"""
    from backend.models import KnowledgeBase

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = KnowledgeBase(name="操作系统", description="", created_by=None)
        db.add(first)
        db.commit()
        db.delete(first)
        db.commit()
        second = KnowledgeBase(name="操作系统", description="", created_by=None)
        db.add(second)
        db.commit()

        assert first.vector_key.startswith("kb_")
        assert second.vector_key.startswith("kb_")
        assert first.vector_key != second.vector_key


def test_search_filter_rejects_legacy_vectors_after_course_deletion():
    """旧 metadata 只有课程名时，tombstone 后不能再被同名新库带回来。"""
    from langchain_core.documents import Document

    from backend.services.vector_store import is_active_vector

    active_keys = {"kb_new"}
    legacy_courses = set()
    legacy_deleted = Document(
        page_content="旧向量",
        metadata={"course": "操作系统"},
    )
    active = Document(
        page_content="新向量",
        metadata={"course": "操作系统", "knowledge_base_id": "kb_new"},
    )
    stale = Document(
        page_content="旧身份向量",
        metadata={"course": "操作系统", "knowledge_base_id": "kb_old"},
    )

    assert not is_active_vector(legacy_deleted, active_keys, legacy_courses)
    assert is_active_vector(active, active_keys, legacy_courses)
    assert not is_active_vector(stale, active_keys, legacy_courses)


def test_chunk_locator_extracts_page_and_section():
    """PDF 页标记和 Markdown 标题要进入引用 metadata。"""
    from backend.services.vector_store import _chunk_locator

    page, section = _chunk_locator(
        "【第 3 页】\n## 进程管理\n进程是资源分配的基本单位。"
    )
    assert page == "3"
    assert section == "进程管理"


def test_remove_base_rolls_back_when_delete_commit_fails(monkeypatch):
    from backend.models import KnowledgeBase, KnowledgeBaseTombstone, VectorCleanupTask
    from backend.routers import knowledge

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin = User(
            account="23040119",
            password_hash="x",
            username="删除测试",
            role="admin",
        )
        db.add(admin)
        db.commit()
        base = KnowledgeBase(name="删除事务测试", created_by=admin.id)
        db.add(base)
        db.commit()
        base_id = base.id

        monkeypatch.setattr(
            knowledge,
            "snapshot_base",
            lambda *_args: SimpleNamespace(id=1, payload='{"docs": []}'),
        )

        def fail_commit():
            raise RuntimeError("database locked")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database locked"):
            knowledge.remove_base(base_id, admin, db)
        db.rollback()
        assert db.get(KnowledgeBase, base_id) is not None
        assert db.scalar(select(KnowledgeBaseTombstone.id)) is None
        assert db.scalar(select(VectorCleanupTask.id)) is None


def test_remove_base_restores_snapshot_files_when_commit_fails(monkeypatch, tmp_path):
    from backend.models import Document, KnowledgeBase
    from backend.routers import knowledge
    from backend.services import recycle

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "upload.txt"
    source.write_text("原始资料", encoding="utf-8")
    monkeypatch.setattr(recycle, "TRASH_DIR", tmp_path / "trash")

    with Session(engine) as db:
        admin = User(
            account="23040129",
            password_hash="x",
            username="文件回滚测试",
            role="admin",
        )
        db.add(admin)
        db.commit()
        base = KnowledgeBase(name="文件回滚知识库", created_by=admin.id)
        db.add(base)
        db.commit()
        doc = Document(
            filename="upload.txt",
            file_type="txt",
            file_size=len("原始资料".encode()),
            stored_path=str(source),
            status="ready",
            course=base.name,
            uploaded_by=admin.id,
        )
        db.add(doc)
        db.commit()
        base_id = base.id
        doc_id = doc.id

        def fail_commit():
            raise RuntimeError("database locked")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database locked"):
            knowledge.remove_base(base_id, admin, db)
        db.rollback()
        db.expire_all()

        assert db.get(KnowledgeBase, base_id) is not None
        restored_doc = db.get(Document, doc_id)
        assert restored_doc is not None
        assert Path(restored_doc.stored_path).exists()
        assert Path(restored_doc.stored_path).read_text(encoding="utf-8") == "原始资料"


def test_snapshot_restore_matches_files_by_doc_id_not_order(tmp_path):
    from backend.models import Document
    from backend.routers.knowledge import _restore_snapshot_files

    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()
    (trash_dir / "a.bin").write_bytes(b"A")
    (trash_dir / "b.bin").write_bytes(b"B")
    doc_a = Document(
        id=1,
        filename="a.txt",
        file_type="txt",
        stored_path=str(tmp_path / "a.txt"),
        course="测试",
    )
    doc_b = Document(
        id=2,
        filename="b.txt",
        file_type="txt",
        stored_path=str(tmp_path / "b.txt"),
        course="测试",
    )
    payload = json.dumps(
        {
            "docs": [
                {"doc_id": 2, "trash_name": "b.bin"},
                {"doc_id": 1, "trash_name": "a.bin"},
            ]
        }
    )

    assert _restore_snapshot_files(payload, [doc_a, doc_b], trash_dir)
    assert (tmp_path / "a.txt").read_bytes() == b"A"
    assert (tmp_path / "b.txt").read_bytes() == b"B"


def test_snapshot_base_restores_files_when_final_flush_fails(monkeypatch, tmp_path):
    from backend.models import Document, KnowledgeBase, RecycleItem
    from backend.services import recycle

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "upload.txt"
    source.write_text("原始资料", encoding="utf-8")
    monkeypatch.setattr(recycle, "TRASH_DIR", tmp_path / "trash")
    with Session(engine) as db:
        admin = User(
            account="23040139",
            password_hash="x",
            username="快照失败测试",
            role="admin",
        )
        db.add(admin)
        db.commit()
        base = KnowledgeBase(name="快照失败知识库", created_by=admin.id)
        db.add(base)
        db.commit()
        doc = Document(
            filename="upload.txt",
            file_type="txt",
            stored_path=str(source),
            status="ready",
            course=base.name,
            uploaded_by=admin.id,
        )
        db.add(doc)
        db.commit()

        real_flush = db.flush
        calls = {"count": 0}

        def flaky_flush(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("flush failed")
            return real_flush(*args, **kwargs)

        monkeypatch.setattr(db, "flush", flaky_flush)
        with pytest.raises(RuntimeError, match="flush failed"):
            recycle.snapshot_base(db, base, admin.id)
        db.rollback()
        assert source.exists()
        assert source.read_text(encoding="utf-8") == "原始资料"
        assert db.scalar(select(RecycleItem.id)) is None


def test_snapshot_compensation_keeps_trash_when_any_restore_fails(
    monkeypatch, tmp_path
):
    from backend.services import recycle

    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()
    archived_a = trash_dir / "a.bin"
    archived_b = trash_dir / "b.bin"
    archived_a.write_bytes(b"A")
    archived_b.write_bytes(b"B")
    original_a = tmp_path / "a.txt"
    original_b = tmp_path / "b.txt"

    real_move = recycle.shutil.move

    def flaky_move(src, dest):
        if Path(src).name == "b.bin":
            raise OSError("move failed")
        return real_move(src, dest)

    monkeypatch.setattr(recycle.shutil, "move", flaky_move)
    restored = recycle._restore_moved_files(
        [(original_a, archived_a), (original_b, archived_b)]
    )

    assert restored is False
    assert original_a.read_bytes() == b"A"
    assert not original_b.exists()
    assert archived_b.exists()
