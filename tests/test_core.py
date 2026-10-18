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

    assert_writable_course(alice, db, "小A私有库")
    # 能看见 ≠ 能写：管理员发布的公共库学生只能读。以前按「可见」放行，
    # 一个学生就能往公共库里塞错误知识点，别人的回答也会引用它。
    with pytest.raises(HTTPException) as exc:
        assert_writable_course(alice, db, "公开库")
    assert exc.value.status_code == 403
    assert_writable_course(admin, db, "公开库")
    assert_writable_course(admin, db, "小A私有库")


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

# ---------------- 回归：踩过的坑 ----------------

def test_prompt_messages_tolerate_braces_in_history():
    """历史消息里出现花括号（代码块、JSON）不能再让整轮问答报错。

    以前历史用 ("user", "字符串") 元组拼进 ChatPromptTemplate，字符串会被当模板解析，
    上一轮回答里只要有个 { 就 KeyError，用户看到的是「回答生成失败：\' \'」。
    """
    from langchain_core.prompts import ChatPromptTemplate

    from backend.services.rag_service import SYSTEM_PROMPT, build_prompt_messages

    history = [
        ("user", "用花括号 { } 包起来"),
        ("assistant", '代码是 if (a) { b(); }，JSON: {"k": 1}'),
    ]
    chain = ChatPromptTemplate.from_messages(build_prompt_messages(SYSTEM_PROMPT, history, "第二问"))
    out = chain.format_messages(context="资料【正文】")

    assert len(out) == 4  # system + 2 条历史 + 当前问题
    assert "{ b(); }" in out[2].content          # 花括号原样保留，没被当变量
    assert out[-1].content == "第二问"
    assert "资料【正文】" in out[0].content       # 系统提示里的 {context} 仍然被替换


def test_save_answer_dropped_when_next_turn_started(monkeypatch):
    """上一轮的迟到回答不能落到下一轮后面。"""
    from sqlalchemy import func, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.models import Base, Conversation, Message, User
    from backend.routers import chat as chat_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestSession() as s:
        user = User(username="10000098", password_hash="x", real_name="t", role="student")
        s.add(user)
        s.commit()
        conv = Conversation(user_id=user.id, title="会话")
        s.add(conv)
        s.commit()
        s.add(Message(conversation_id=conv.id, role="user", content="第一问"))
        s.commit()
        first_user_id = s.scalar(select(func.max(Message.id)))
        s.add(Message(conversation_id=conv.id, role="user", content="第二问"))
        s.commit()
        conv_id = conv.id

    monkeypatch.setattr(chat_mod, "SessionLocal", TestSession)
    assert chat_mod._save_answer(conv_id, first_user_id, "迟到的回答", "[]", 0) is None
    with TestSession() as s:
        assert s.scalar(select(func.count(Message.id)).where(Message.role == "assistant")) == 0


def test_find_auto_doc_skips_when_parse_fails(monkeypatch, db):
    """解析不出正文时不能把整篇文档当成「自动生成的知识点」连带删掉。"""
    from backend.models import Document
    from backend.routers import knowledge as kb_mod

    user = _user(db, "10000097", "student")
    db.add(KnowledgeBase(name="私有库", created_by=user.id))
    db.add(
        Document(
            filename="第七讲.pdf",
            file_type="pdf",
            stored_path='不存在.pdf',
            course="私有库",
            uploaded_by=user.id,
        )
    )
    point = KnowledgePoint(title="第七讲", content="正文", course="私有库", created_by=user.id)
    db.add(point)
    db.commit()

    monkeypatch.setattr(kb_mod, "parse_file", lambda _p: (_ for _ in ()).throw(RuntimeError("解析失败")))
    assert kb_mod._find_auto_doc(db, point) is None


def test_list_sessions_paging_and_search(db):
    """会话列表：只返回当前页 + 总数，最新在前，标题可搜。"""
    from backend.models import Conversation, Message
    from backend.routers.chat import list_sessions

    user = _user(db, "10000096", "student")
    for i in range(5):
        conv = Conversation(user_id=user.id, title=f"线性代数 {i}")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(Message(conversation_id=conv.id, role="user", content="问"))
        db.add(Message(conversation_id=conv.id, role="assistant", content="答"))
        db.commit()

    page = list_sessions(q="", limit=2, offset=0, kb=None, user=user, db=db)
    assert page["total"] == 5
    assert [c.title for c in page["items"]] == ["线性代数 4", "线性代数 3"]
    assert page["items"][0].message_count == 2

    last = list_sessions(q="", limit=2, offset=4, kb=None, user=user, db=db)
    assert [c.title for c in last["items"]] == ["线性代数 0"]

    hit = list_sessions(q="代数 1", limit=10, offset=0, kb=None, user=user, db=db)
    assert hit["total"] == 1 and hit["items"][0].title == "线性代数 1"