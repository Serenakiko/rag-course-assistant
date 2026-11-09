"""针对审查中发现的高风险回归建立可重复验证。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import HTTPException
from langchain_core.documents import Document as LangChainDocument
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from backend.models import (
    Base,
    Conversation,
    Document,
    KnowledgeBase,
    RecycleItem,
    User,
    VectorCleanupTask,
)
from backend.schemas import LoginRequest
from backend.security import hash_password


def _request(client: tuple[str, int] = ("127.0.0.1", 12345), headers=None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/login",
            "raw_path": b"/api/auth/login",
            "query_string": b"",
            "headers": headers or [],
            "client": client,
            "server": ("testserver", 80),
        }
    )


def _session_factory(path: Path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_document_dedup_preserves_operators_case_and_code_symbols():
    """代码和公式中的符号、大小写不能因去重归一化而消失。"""
    from backend.services.document_dedup import fingerprint_text, is_duplicate_fingerprint

    different_pairs = [
        ("x+y", "x-y"),
        ("i++", "i--"),
        ("fooBar()", "foobar"),
    ]
    for left, right in different_pairs:
        assert not is_duplicate_fingerprint(
            fingerprint_text(left),
            fingerprint_text(right),
        )

    assert fingerprint_text("进程 是资源分配单位").content_hash == fingerprint_text(
        "进程是资源分配单位"
    ).content_hash


def test_login_lockout_rejects_before_checking_a_correct_password():
    """达到失败上限后必须直接拒绝，不能继续验证并放行正确密码。"""
    from backend.routers import auth

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    auth._login_fails.clear()
    auth._login_ip_fails.clear()

    with Session(engine) as db:
        db.add(
            User(
                account="26000001",
                password_hash=hash_password("correct-pass"),
                username="锁定测试",
                role="student",
            )
        )
        db.commit()

        for _ in range(auth._LOGIN_MAX_FAILS):
            with pytest.raises(HTTPException) as exc:
                auth.login(
                    LoginRequest(account="26000001", password="wrong-pass"),
                    _request(),
                    db,
                )
            assert exc.value.status_code == 401

        with pytest.raises(HTTPException) as locked:
            auth.login(
                LoginRequest(account="26000001", password="correct-pass"),
                _request(),
                db,
            )
        assert locked.value.status_code == 429


def test_private_direct_client_cannot_spoof_forwarded_ip():
    """局域网客户端直连时不能被当成可信反向代理。"""
    from backend.routers.auth import _client_ip, _is_trusted_proxy

    assert not _is_trusted_proxy("192.168.1.20")
    request = _request(
        client=("192.168.1.20", 12345),
        headers=[(b"x-forwarded-for", b"203.0.113.10")],
    )
    assert _client_ip(request) == "192.168.1.20"


def test_gen_seq_increment_is_atomic(monkeypatch, tmp_path):
    """两个并发请求必须拿到不同生成序号。"""
    from backend.routers import chat

    engine, factory = _session_factory(tmp_path / "gen-seq.db")
    with factory() as db:
        user = User(account="26000002", password_hash="x", username="并发测试", role="student")
        db.add(user)
        db.flush()
        conv = Conversation(user_id=user.id, title="并发会话", gen_seq=0)
        db.add(conv)
        db.commit()
        conv_id = conv.id

    barrier = threading.Barrier(2)

    class CoordinatedSession:
        def __init__(self):
            self.inner = factory()

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def get(self, entity, ident):
            value = self.inner.get(entity, ident)
            if entity is Conversation:
                barrier.wait(timeout=10)
            return value

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(chat, "SessionLocal", CoordinatedSession)
    results: list[int] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(chat._bump_gen_seq(conv_id))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(results) == [1, 2]


def test_nonstream_generation_oom_returns_resource_error(monkeypatch):
    """管理员重新生成遇到模型 OOM 时必须返回可读错误而非 500。"""
    from backend.services import rag_service

    doc = LangChainDocument(
        page_content="进程是资源分配单位，线程是调度单位。",
        metadata={"course": "操作系统", "title": "进程与线程"},
    )

    async def oom_stream(*_args, **_kwargs):
        if False:
            yield ""
        raise RuntimeError("llama-server reported out-of-memory: failed to allocate buffer")

    monkeypatch.setattr(rag_service, "vector_search", lambda *_args, **_kwargs: [(doc, 0.9)])
    monkeypatch.setattr(rag_service.settings, "ANSWERABILITY_CHECK", False)
    monkeypatch.setattr(rag_service, "_generate_stream", oom_stream)

    result = asyncio.run(rag_service._generate_answer_locked("进程和线程有什么区别？"))

    assert result["error"] == "resource"


def test_answerability_ambiguous_output_is_error_not_unanswerable(monkeypatch):
    """“无法判断”表示判定失败，不能当成资料不可答。"""
    from backend.services import rag_service

    class AmbiguousLLM:
        async def ainvoke(self, _messages):
            class Response:
                content = "无法判断资料是否足够"

            return Response()

    doc = LangChainDocument(page_content="资料只介绍了二叉树。", metadata={"course": "数据结构"})
    monkeypatch.setattr(rag_service, "get_llm", lambda **_kwargs: AmbiguousLLM())

    result = asyncio.run(rag_service.can_answer("红黑树插入怎么旋转？", [(doc, 0.7)]))

    assert result is rag_service.Answerability.ERROR


def test_generation_truncation_detects_openai_finish_reason(monkeypatch):
    """OpenAI 兼容接口使用 finish_reason，也应触发截断提示。"""
    from backend.services import rag_service

    class FakeTemplate:
        @classmethod
        def from_messages(cls, _messages):
            return cls()

        def __or__(self, _llm):
            return "chain"

    async def fake_stream(_chain, _inputs, _timeout):
        yield SimpleNamespace(
            content="被截断的正文",
            response_metadata={"finish_reason": "length"},
        )

    monkeypatch.setattr(rag_service.ChatPromptTemplate, "from_messages", FakeTemplate.from_messages)
    monkeypatch.setattr(rag_service, "get_llm", lambda: object())
    monkeypatch.setattr(rag_service, "_astream_with_deadline", fake_stream)
    finish_info: dict = {}

    async def collect():
        return [
            chunk
            async for chunk in rag_service._generate_stream(
                "system {context}",
                "context",
                "question",
                [],
                finish_info,
            )
        ]

    chunks = asyncio.run(collect())

    assert chunks == ["被截断的正文"]
    assert finish_info["finish_reason"] == "length"


def test_ollama_llm_can_be_unloaded_before_embedding(monkeypatch):
    """检索前应能显式卸载常驻对话模型，避免与向量模型争内存。"""
    from backend.services import llm_service

    captured: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(llm_service.settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_service, "urlopen", fake_urlopen)

    llm_service.unload_ollama_llm()

    assert captured["url"].endswith("/api/generate")
    assert captured["body"]["keep_alive"] == 0


def test_delete_knowledge_point_without_collection_is_noop(monkeypatch, tmp_path):
    """向量集合尚未创建时删除知识点应成功，而不是 500。"""
    from backend.config import settings
    from backend.services import vector_store

    monkeypatch.setattr(settings, "CHROMA_DIR", tmp_path / "missing-chroma")
    monkeypatch.setattr(vector_store, "_client", None)
    monkeypatch.setattr(vector_store, "_vector_store", None)

    assert vector_store.delete_knowledge_point(999) is True


def test_text_parser_falls_back_to_gb18030(tmp_path):
    """常见中文 GBK/GB18030 文本应按原意读取。"""
    from backend.services.document_parser import parse_file

    path = tmp_path / "课程资料.txt"
    path.write_bytes("操作系统进程与线程".encode("gb18030"))

    assert parse_file(path) == "操作系统进程与线程"


def test_init_db_merges_duplicate_unanswered_rows_without_losing_answer(monkeypatch, tmp_path):
    """旧库重复反馈迁移应保留较新的已解决答案。"""
    from backend import database

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE unanswered_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    display_no INTEGER DEFAULT 0,
                    question TEXT NOT NULL,
                    course VARCHAR(128) DEFAULT '',
                    status VARCHAR(16) DEFAULT 'pending',
                    related_points TEXT DEFAULT '[]',
                    answer TEXT DEFAULT '',
                    answered_at DATETIME,
                    read_at DATETIME,
                    admin_read_at DATETIME,
                    created_by INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO unanswered_questions
                    (question, course, status, answer, created_by, created_at, updated_at)
                VALUES
                    ('红黑树如何插入？', '数据结构', 'pending', '', 1,
                     '2026-01-01 00:00:00', '2026-01-01 00:00:00'),
                    ('红黑树如何插入？', '数据结构', 'resolved', '已补充完整答案', 1,
                     '2026-01-02 00:00:00', '2026-01-02 00:00:00')
                """
            )
        )

    monkeypatch.setattr(database, "engine", engine)
    database.init_db()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT status, answer FROM unanswered_questions "
                "WHERE question = '红黑树如何插入？'"
            )
        ).all()

    assert rows == [("resolved", "已补充完整答案")]


def test_snapshot_base_aborts_when_source_file_cannot_be_archived(monkeypatch, tmp_path):
    """回收站移动源文件失败时必须中止删除，不能留下不可恢复记录。"""
    from backend.services import recycle

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "course.md"
    source.write_text("课程资料", encoding="utf-8")
    monkeypatch.setattr(recycle.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(recycle, "TRASH_DIR", tmp_path / "trash")
    monkeypatch.setattr(
        recycle.shutil,
        "move",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("file busy")),
    )

    with Session(engine) as db:
        user = User(account="26000003", password_hash="x", username="回收测试", role="admin")
        db.add(user)
        db.flush()
        base = KnowledgeBase(name="操作系统", description="", created_by=user.id)
        db.add(base)
        db.flush()
        doc = Document(
            filename="course.md",
            file_type="md",
            stored_path=str(source),
            status="ready",
            course=base.name,
            uploaded_by=user.id,
        )
        db.add(doc)
        db.commit()

        with pytest.raises(OSError, match="file busy"):
            recycle.snapshot_base(db, base, user.id)
        db.rollback()

    assert source.exists()


def test_recycle_restore_keeps_snapshot_when_vectorization_fails(monkeypatch):
    """恢复时向量化失败必须回滚并保留回收站快照。"""
    from backend.routers import recycle

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        recycle,
        "add_knowledge_point",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("embedding down")),
    )
    monkeypatch.setattr(
        recycle,
        "delete_knowledge_base_vectors",
        lambda *_args, **_kwargs: True,
    )

    with Session(engine) as db:
        admin = User(account="26000006", password_hash="x", username="恢复测试", role="admin")
        db.add(admin)
        db.flush()
        item = RecycleItem(
            kind="base",
            name="操作系统",
            payload=json.dumps(
                {
                    "base": {"name": "操作系统", "description": ""},
                    "points": [
                        {
                            "title": "进程与线程",
                            "content": "进程是资源分配单位。",
                            "tags": "",
                            "created_by": admin.id,
                            "is_auto": 0,
                        }
                    ],
                    "docs": [],
                },
                ensure_ascii=False,
            ),
            deleted_by=admin.id,
        )
        db.add(item)
        db.commit()
        item_id = item.id

        with pytest.raises(HTTPException) as exc:
            recycle.restore_item(item_id, admin, db)
        assert exc.value.status_code == 500
        db.expire_all()
        assert db.get(RecycleItem, item_id) is not None


def test_remove_document_persists_vector_cleanup_task(monkeypatch):
    """数据库删除后向量清理失败时应留下可重试任务。"""
    from backend.routers import documents
    from backend.services import vector_reconcile

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        vector_reconcile,
        "delete_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("chroma down")),
    )

    with Session(engine) as db:
        user = User(account="26000004", password_hash="x", username="文档测试", role="admin")
        db.add(user)
        db.flush()
        doc = Document(
            filename="course.md",
            file_type="md",
            stored_path="",
            status="ready",
            course="操作系统",
            uploaded_by=user.id,
        )
        db.add(doc)
        db.commit()

        documents.remove_document(doc.id, user, db)
        tasks = list(db.scalars(select(VectorCleanupTask)))

    assert len(tasks) == 1
    assert tasks[0].kind == "document"
    assert tasks[0].object_id == str(doc.id)
    assert tasks[0].status == "pending"


def test_document_detail_limits_returned_chunks(monkeypatch):
    """大文档详情默认只返回有限分块，避免浏览器一次渲染数万块。"""
    from backend.routers import documents

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    chunks = 80

    class FakeCollection:
        def get(self, ids=None, where=None, include=None, limit=None):
            del where
            all_ids = [f"doc-1-{i}" for i in range(chunks)]
            all_meta = [
                {"source_type": "document", "doc_id": "1", "chunk_index": i}
                for i in range(chunks)
            ]
            if ids is None:
                selected = list(range(chunks))[:limit] if limit else list(range(chunks))
                return {
                    "ids": [all_ids[i] for i in selected],
                    "metadatas": [all_meta[i] for i in selected],
                    **(
                        {"documents": [f"chunk-{i}" for i in selected]}
                        if include and "documents" in include
                        else {}
                    ),
                }
            by_id = {all_ids[i]: i for i in range(chunks)}
            selected = [by_id[item] for item in ids]
            return {
                "ids": [all_ids[i] for i in selected],
                "metadatas": [all_meta[i] for i in selected],
                "documents": [f"chunk-{i}" for i in selected],
            }

    monkeypatch.setattr(documents, "get_collection", lambda: FakeCollection())

    with Session(engine) as db:
        user = User(account="26000005", password_hash="x", username="详情测试", role="admin")
        db.add(user)
        db.flush()
        doc = Document(
            filename="large.md",
            file_type="md",
            stored_path="",
            status="ready",
            course="操作系统",
            uploaded_by=user.id,
            chunk_count=chunks,
        )
        db.add(doc)
        db.commit()

        result = documents.document_detail(doc.id, user, db)

    assert len(result.chunks) == 50


def test_admin_stats_does_not_count_failed_placeholder_as_hit():
    """模型失败留下的占位提示不能计入问答命中率。"""
    from backend.models import Message
    from backend.routers.chat import _NO_ANSWER_HINT
    from backend.routers.stats import admin_stats

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        admin = User(account="26000007", password_hash="x", username="统计管理员", role="admin")
        student = User(account="26000008", password_hash="x", username="统计学生", role="student")
        db.add_all([admin, student])
        db.flush()
        conv = Conversation(user_id=student.id, title="失败统计")
        db.add(conv)
        db.flush()
        db.add_all(
            [
                Message(conversation_id=conv.id, role="user", content="进程和线程的区别？"),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=_NO_ANSWER_HINT,
                    interrupted=0,
                    rejected=0,
                ),
            ]
        )
        db.commit()

        stats = admin_stats(admin, db)

    assert stats.total_questions == 1
    assert stats.hit_rate == 0.0
    assert stats.reject_rate == 0.0


def test_port_probe_only_accepts_this_application(monkeypatch):
    """端口被其他程序占用时不能误报本服务已在运行。"""
    import importlib

    from backend.config import settings

    run = importlib.import_module("run")

    class Response:
        def __init__(self, payload: dict):
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

    monkeypatch.setattr(
        run,
        "urlopen",
        lambda *_args, **_kwargs: Response({"app": "另一个系统"}),
    )
    assert not run._is_our_service("127.0.0.1", 8000)

    monkeypatch.setattr(
        run,
        "urlopen",
        lambda *_args, **_kwargs: Response({"app": settings.APP_NAME}),
    )
    assert run._is_our_service("127.0.0.1", 8000)


def test_existing_service_start_opens_browser(monkeypatch):
    """重复双击 start.bat 时应打开已经在运行的本系统页面。"""
    import importlib

    run = importlib.import_module("run")
    opened: list[str] = []

    monkeypatch.setattr(run, "_port_in_use", lambda *_args: True)
    monkeypatch.setattr(run, "_is_our_service", lambda *_args: True)
    monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(run.sys, "argv", ["run.py"])

    run.main()

    assert len(opened) == 1
    assert opened[0].startswith("http://127.0.0.1:")


@pytest.mark.skipif(os.name != "nt", reason="start.bat 只在 Windows 执行")
def test_start_bat_falls_back_when_venv_python_is_broken(tmp_path):
    """venv 存在但解释器损坏时，start.bat 应退回 PATH 中的 Python。"""
    root = Path(__file__).resolve().parent.parent
    source_venv_python = root / "venv" / "Scripts" / "python.exe"
    if not source_venv_python.exists():
        pytest.skip("当前没有可复用的 venv 启动器样本")

    shutil.copy2(root / "start.bat", tmp_path / "start.bat")
    (tmp_path / "run.py").write_text("print('fallback ok')\n", encoding="utf-8")
    broken_scripts = tmp_path / "venv" / "Scripts"
    broken_scripts.mkdir(parents=True)
    shutil.copy2(source_venv_python, broken_scripts / "python.exe")
    (tmp_path / "venv" / "pyvenv.cfg").write_text(
        "home = Z:\\missing-python\nversion = 3.11.9\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    marker = tmp_path / "fallback.marker"
    (fake_bin / "python.cmd").write_text(
        f"@echo off\r\n> \"{marker}\" echo used\r\nexit /b 0\r\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        ["cmd.exe", "/c", "start.bat", "--probe"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
