"""回收站修复的回归测试：分钟级写事务、文档回收、指纹快照与失败清单。"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import settings
from backend.database import get_db
from backend.models import (
    Base,
    Document,
    KnowledgeBase,
    KnowledgePoint,
    RecycleItem,
    User,
)
from backend.routers import auth, recycle
from backend.security import hash_password
from backend.seed_data import DEFAULT_ADMIN
from backend.services import recycle as recycle_service
from backend.services import vector_reconcile


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(recycle.router, prefix="/api/admin/recycle")

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(recycle_service, "TRASH_DIR", trash_dir)

    with SessionFactory() as db:
        admin = User(
            account=DEFAULT_ADMIN[0],
            password_hash=hash_password(DEFAULT_ADMIN[1]),
            username=DEFAULT_ADMIN[2],
            role="admin",
        )
        db.add(admin)
        db.commit()
        admin_id = admin.id

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"account": DEFAULT_ADMIN[0], "password": DEFAULT_ADMIN[1]},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    try:
        yield SimpleNamespace(
            client=client,
            factory=SessionFactory,
            headers=headers,
            admin_id=admin_id,
            uploads=upload_dir,
            trash=trash_dir,
        )
    finally:
        client.close()
        engine.dispose()


def _make_doc(env, db, course: str, text: str = "hello world") -> Document:
    src = env.uploads / f"{course}.txt"
    src.write_text(text, encoding="utf-8")
    doc = Document(
        filename=f"{course}.txt",
        file_type="txt",
        file_size=len(text),
        stored_path=str(src),
        course=course,
        content_hash="a" * 64,
        content_simhash="b" * 20,
        content_length=len(text),
        chunk_count=1,
    )
    db.add(doc)
    db.commit()
    return doc


def _snapshot_payload(item: RecycleItem) -> dict:
    return json.loads(item.payload)


def test_snapshot_base_keeps_content_fingerprints(env):
    with env.factory() as db:
        base = KnowledgeBase(name="数据结构")
        db.add(base)
        db.flush()
        doc = _make_doc(env, db, "数据结构")
        item = recycle_service.snapshot_base(db, base, env.admin_id)
        db.commit()

        entry = _snapshot_payload(item)["docs"][0]
        assert entry["content_hash"] == "a" * 64
        assert entry["content_simhash"] == "b" * 20
        assert entry["content_length"] == len("hello world")
        assert entry["chunk_count"] == 1
        assert entry["course"] == "数据结构"
        assert entry["doc_id"] == doc.id
        archived = env.trash / str(item.id) / entry["trash_name"]
        assert archived.exists()
        assert not (env.uploads / "数据结构.txt").exists()


def test_restore_base_schedules_vectors_and_keeps_fingerprints(env, monkeypatch):
    with env.factory() as db:
        base = KnowledgeBase(name="数据结构")
        db.add(base)
        db.flush()
        base_id = base.id
        _make_doc(env, db, "数据结构")
        item = recycle_service.snapshot_base(db, base, env.admin_id)
        db.delete(base)
        for doc in list(db.query(Document)):
            db.delete(doc)
        db.commit()
        item_id = item.id

    scheduled: list[int] = []
    monkeypatch.setattr(
        recycle, "run_vector_cleanup_task_by_id", lambda task_id: scheduled.append(task_id)
    )

    def _no_embedding(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("恢复请求事务内不应调用 embedding")

    monkeypatch.setattr(recycle, "add_knowledge_point", _no_embedding)

    r = env.client.post(f"/api/admin/recycle/{item_id}/restore", headers=env.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] is True
    assert body["ok"] is True
    assert body["failed"] == []
    assert body["documents"] == 1
    assert body["vectors_pending"] is True
    # 旧前端字段仍然存在
    assert body["name"] and body["points"] is not None
    assert scheduled, "应登记后台向量重建任务"

    with env.factory() as db:
        restored = db.query(Document).one()
        assert restored.content_hash == "a" * 64
        assert restored.content_simhash == "b" * 20
        assert restored.content_length == len("hello world")
        task = db.query(vector_reconcile.VectorCleanupTask).one()
        assert task.kind == "restore"
        assert task.status == "pending"
        assert task.object_id == str(base_id)
        assert task.course_name == body["name"]
        assert db.get(RecycleItem, item_id) is None
        assert (env.uploads / os.path.basename(restored.stored_path)).exists()
    assert not (env.trash / str(item_id)).exists()


def test_recycle_document_moves_file_and_restores_with_task(env, monkeypatch):
    cleaned: list[int] = []
    monkeypatch.setattr(
        vector_reconcile,
        "run_vector_cleanup_task",
        lambda task: cleaned.append(task.id),
    )

    with env.factory() as db:
        db.add(KnowledgeBase(name="操作系统"))
        db.commit()
        doc = _make_doc(env, db, "操作系统")
        doc_id = doc.id
        item = recycle_service.recycle_document(db, doc, env.admin_id)
        item_id = item.id
        assert item.kind == "document"
        assert item.name == "操作系统.txt"
        entry = _snapshot_payload(item)["docs"][0]
        assert entry["content_hash"] == "a" * 64
        assert entry["content_simhash"] == "b" * 20
        assert entry["course"] == "操作系统"
        assert db.get(Document, doc_id) is None
        assert (env.trash / str(item_id) / entry["trash_name"]).exists()
        assert not (env.uploads / "操作系统.txt").exists()
        assert cleaned, "提交后应执行一次向量清理"

    scheduled: list[int] = []
    monkeypatch.setattr(
        recycle, "run_vector_cleanup_task_by_id", lambda task_id: scheduled.append(task_id)
    )

    r = env.client.post(f"/api/admin/recycle/{item_id}/restore", headers=env.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] is True
    assert body["kind"] == "document"
    assert body["documents"] == 1
    assert body["failed"] == []
    assert body["vectors_pending"] is True

    with env.factory() as db:
        restored = db.query(Document).one()
        assert restored.course == "操作系统"
        assert restored.content_hash == "a" * 64
        task = db.query(vector_reconcile.VectorCleanupTask).filter_by(
            kind="restore_document"
        ).one()
        assert task.object_id == str(restored.id)
        assert task.knowledge_base_id == db.query(KnowledgeBase).one().vector_key
        assert db.get(RecycleItem, item_id) is None


def test_recycle_document_keeps_pending_task_when_cleanup_fails(env, monkeypatch):
    def _boom(_task):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(vector_reconcile, "run_vector_cleanup_task", _boom)

    with env.factory() as db:
        db.add(KnowledgeBase(name="计算机网络"))
        db.commit()
        doc = _make_doc(env, db, "计算机网络")
        item = recycle_service.recycle_document(db, doc, env.admin_id)
        assert item.kind == "document"
        task = db.query(vector_reconcile.VectorCleanupTask).one()
        assert task.kind == "document"
        assert task.status == "pending"
        assert "chroma down" in (task.last_error or "")
        assert db.get(RecycleItem, item.id) is not None


def test_restore_missing_file_returns_failed_list(env):
    with env.factory() as db:
        item = RecycleItem(
            kind="document",
            name="丢失的文档.txt",
            deleted_by=env.admin_id,
            payload=json.dumps(
                {
                    "docs": [
                        {
                            "filename": "丢失的文档.txt",
                            "file_type": "txt",
                            "file_size": 3,
                            "course": "操作系统",
                            "trash_name": "not-there.txt",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        db.add(item)
        db.commit()
        item_id = item.id

    r = env.client.post(f"/api/admin/recycle/{item_id}/restore", headers=env.headers)
    # 恢复失败必须是错误状态码，不能 200 + ok:false（调用方会误判成功）
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["restored"] is False
    assert body["ok"] is False
    assert body["failed"] == [
        {"name": "丢失的文档.txt", "reason": "回收站中缺少源文件"}
    ]
    assert body["failed_documents"] == 1
    with env.factory() as db:
        assert db.get(RecycleItem, item_id) is not None


def test_purge_commits_before_file_cleanup(env, monkeypatch):
    with env.factory() as db:
        item = RecycleItem(kind="document", name="x.txt", deleted_by=env.admin_id, payload="{}")
        db.add(item)
        db.commit()
        item_id = item.id
        (env.trash / str(item_id)).mkdir(parents=True)

    def _boom(_item):
        raise OSError("disk busy")

    monkeypatch.setattr(recycle, "drop_files", _boom)
    r = env.client.delete(f"/api/admin/recycle/{item_id}", headers=env.headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    with env.factory() as db:
        assert db.get(RecycleItem, item_id) is None


def test_startup_sweep_removes_orphan_vectors(env, monkeypatch):
    """DB 里已删的文档 / 知识点，其向量分块要在启动时清掉，否则检索得到、点开 404。"""
    from backend.services import vector_store

    with env.factory() as db:
        db.add(KnowledgeBase(name="操作系统"))
        db.commit()
        doc = _make_doc(env, db, "操作系统")
        point = KnowledgePoint(title="进程与线程", content="进程是资源分配单位。", course="操作系统")
        db.add(point)
        db.commit()
        doc_id, point_id = doc.id, point.id

    rows = [
        (f"doc-{doc_id}-0-v1", {"source_type": "document", "doc_id": str(doc_id)}),
        ("doc-999-0-v1", {"source_type": "document", "doc_id": "999"}),
        (f"point-{point_id}-v1-c0", {"source_type": "knowledge_point", "point_id": str(point_id)}),
        ("point-888-v1-c0", {"source_type": "knowledge_point", "point_id": "888"}),
    ]
    deleted: list[str] = []

    def _fake_delete(ids):
        deleted.extend(ids)
        return len(ids)

    monkeypatch.setattr(vector_store, "iter_vectors", lambda: rows)
    monkeypatch.setattr(vector_store, "delete_vectors_by_ids", _fake_delete)
    monkeypatch.setattr(vector_reconcile, "SessionLocal", env.factory)

    assert vector_reconcile.sweep_orphan_vectors() == 2
    assert set(deleted) == {"doc-999-0-v1", "point-888-v1-c0"}


def test_startup_sweep_is_noop_without_vectors(env, monkeypatch):
    from backend.services import vector_store

    monkeypatch.setattr(vector_store, "iter_vectors", lambda: [])
    monkeypatch.setattr(vector_reconcile, "SessionLocal", env.factory)
    assert vector_reconcile.sweep_orphan_vectors() == 0


def test_embedding_model_change_is_noop_when_model_unchanged(monkeypatch):
    """模型没变就别重建：启动维护不能每次都把全库向量重算一遍。"""
    from backend.services import vector_reconcile, vector_store

    monkeypatch.setattr(
        vector_store, "recorded_embedding_model", vector_store.get_embedding_model_name
    )
    resets: list[int] = []
    monkeypatch.setattr(vector_store, "reset_vector_store", lambda: resets.append(1))

    assert vector_reconcile.rebuild_vectors_for_embedding_model_change() == (0, 0)
    assert resets == []


def test_embedding_marker_recorded_on_first_run(monkeypatch):
    """全新安装（没有模型标记）只记标记，不触发重建。"""
    from backend.services import vector_reconcile, vector_store

    recorded: list[str | None] = []
    monkeypatch.setattr(vector_store, "recorded_embedding_model", lambda: None)
    monkeypatch.setattr(
        vector_store, "record_embedding_model", lambda name=None: recorded.append(name)
    )

    assert vector_reconcile.rebuild_vectors_for_embedding_model_change() == (0, 0)
    assert recorded and recorded[0]


def test_missing_marker_with_ready_docs_triggers_rebuild(env, monkeypatch):
    """按旧提示删掉 data/chroma 后，标记文件也一起没了。

    这时 DB 里文档仍是 ready，若把「没有标记」当成全新安装放过，
    检索会永远搜不到内容；必须按当前模型重建。
    """
    from backend.services import vector_reconcile, vector_store

    with env.factory() as db:
        db.add(KnowledgeBase(name="操作系统"))
        doc = _make_doc(env, db, "操作系统")
        doc.status = "ready"
        db.commit()

    resets: list[int] = []
    recorded: list[str | None] = []
    monkeypatch.setattr(vector_reconcile, "SessionLocal", env.factory)
    monkeypatch.setattr(vector_store, "recorded_embedding_model", lambda: None)
    monkeypatch.setattr(
        vector_store, "record_embedding_model", lambda name=None: recorded.append(name)
    )
    monkeypatch.setattr(vector_store, "reset_vector_store", lambda: resets.append(1))
    monkeypatch.setattr(
        vector_reconcile,
        "rebuild_base_vectors",
        lambda db, base_id, course, vector_key=None: (0, 0),
    )

    ok, failed = vector_reconcile.rebuild_vectors_for_embedding_model_change()
    assert resets == [1], "缺少标记但仍有就绪文档时必须先清空旧向量"
    assert (ok, failed) == (1, 0)
    assert recorded, "重建成功后才写标记"


def test_missing_marker_without_docs_is_fresh_install(env, monkeypatch):
    from backend.services import vector_reconcile, vector_store

    resets: list[int] = []
    recorded: list[str | None] = []
    monkeypatch.setattr(vector_reconcile, "SessionLocal", env.factory)
    monkeypatch.setattr(vector_store, "recorded_embedding_model", lambda: None)
    monkeypatch.setattr(
        vector_store, "record_embedding_model", lambda name=None: recorded.append(name)
    )
    monkeypatch.setattr(vector_store, "reset_vector_store", lambda: resets.append(1))

    assert vector_reconcile.rebuild_vectors_for_embedding_model_change() == (0, 0)
    assert resets == []
    assert recorded
