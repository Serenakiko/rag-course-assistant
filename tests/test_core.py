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


def test_vector_doc_helper():
    from backend.services.vector_store import _doc

    doc = _doc("正文", {"source_type": "document", "doc_id": "1"})
    assert doc.page_content == "正文"
    assert doc.metadata["doc_id"] == "1"


def test_course_related_short_circuit(monkeypatch):
    from backend.services import rag_service

    # 只命中一组关键词：不调模型，直接短路
    assert asyncio.run(rag_service.is_course_related("红黑树的插入过程")) is True
    assert asyncio.run(rag_service.is_course_related("今天天气如何")) is False

    # 「考研」和「多线程」两组都命中，交给模型判；模型不可用时保守按不相关处理
    def boom():
        raise RuntimeError("模型不可用")

    monkeypatch.setattr(rag_service, "get_llm", boom)
    assert asyncio.run(rag_service.is_course_related("考研面试里的多线程")) is False


def test_course_related_ambiguous_asks_model(monkeypatch):
    """问句里同时出现课程词和「考研/面试」这类词时，别直接拒答。

    以前先判无关词就 return False：学生问「考研 408 里的红黑树怎么考」会被
    一句「不在我的回答范围内」挡掉，还不会进反馈列表。现在这种含糊句子交给
    只答「是/否」的模型判。
    """
    from backend.services import rag_service

    class FakeLLM:
        def __init__(self, reply):
            self.reply = reply

        async def ainvoke(self, _messages):
            class Resp:
                content = self.reply

            return Resp()

    monkeypatch.setattr(rag_service, "get_llm", lambda: FakeLLM("是"))
    assert asyncio.run(rag_service.is_course_related("考研 408 里的红黑树怎么考")) is True

    monkeypatch.setattr(rag_service, "get_llm", lambda: FakeLLM("否"))
    assert asyncio.run(rag_service.is_course_related("考研面试要注意什么")) is False


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

    page = list_sessions(q="", limit=2, offset=0, user=user, db=db)
    assert page["total"] == 5
    assert [c.title for c in page["items"]] == ["线性代数 4", "线性代数 3"]
    assert page["items"][0].message_count == 2

    last = list_sessions(q="", limit=2, offset=4, user=user, db=db)
    assert [c.title for c in last["items"]] == ["线性代数 0"]

    hit = list_sessions(q="代数 1", limit=10, offset=0, user=user, db=db)
    assert hit["total"] == 1 and hit["items"][0].title == "线性代数 1"


def test_regenerate_unmatched_returns_to_pending(monkeypatch, db):
    """已解决的反馈重新生成失败后，不能停在「已解决 + 空答案」。

    以前 else 分支只清 q.answer，status 留在 resolved：学生端看到「已解决」
    角标、点开是空白，管理员之前生成好的答案也没了。
    """
    from backend.models import UnansweredQuestion, utcnow
    from backend.routers import unanswered as uq_mod
    from backend.services import rag_service

    admin = _user(db, "10000091", "admin")
    student = _user(db, "10000090", "student")
    rec = UnansweredQuestion(
        display_no=1,
        question="红黑树的性质",
        course="",
        status="resolved",
        answer="旧的正确答案",
        answered_at=utcnow(),
        created_by=student.id,
    )
    db.add(rec)
    db.commit()

    async def fake_generate(question, courses=None):
        return {
            "answer": "【知识库未收录】知识库中暂无相关内容",
            "recommended": [],
            "matched": False,
        }

    monkeypatch.setattr(rag_service, "generate_answer", fake_generate)
    out = asyncio.run(uq_mod.regenerate_answer(rec.id, admin, db))

    assert out["resolved"] is False
    db.refresh(rec)
    assert rec.status == "pending"       # 退回待处理，不能被当成已解决
    assert rec.answer == ""
    assert rec.answered_at is None
    assert rec.read_at is None           # 重新计为未读，学生端能看到“有更新”


def test_like_escape():
    """搜索词里的 % 和 _ 不能当 LIKE 通配符用（全站共用 services.like）。"""
    from backend.services.like import LIKE_ESCAPE, escape_like

    assert escape_like("100%") == "100" + LIKE_ESCAPE + "%"
    assert escape_like("os_lab") == "os" + LIKE_ESCAPE + "_lab"
    # 转义符本身也得转义，否则尾部的反斜杠会把后面的 % 吃掉
    assert escape_like("a" + LIKE_ESCAPE) == "a" + LIKE_ESCAPE * 2


def test_queue_gate_gives_up_after_budget(monkeypatch):
    """排队超过上限要明确说「人多」，别让学生等到前端总超时才看到「生成超时」。"""
    from backend.services import rag_service

    monkeypatch.setattr(rag_service, "_llm_slots", lambda: asyncio.Semaphore(0))  # 永远拿不到
    monkeypatch.setattr(rag_service, "_QUEUE_PING_SECONDS", 0.01)
    monkeypatch.setattr(rag_service, "_QUEUE_MAX_WAIT", 0.05)

    events: list[dict] = []

    async def run() -> None:
        async for event in rag_service.stream_answer("红黑树的插入过程"):
            events.append(event)

    asyncio.run(run())

    assert events[-1]["type"] == "error"
    assert "排队" in events[-1]["message"]
    assert all(e["type"] == "ping" for e in events[:-1])   # 排队期间只有心跳


def test_generate_answer_uses_the_same_gate(monkeypatch):
    """管理员「生成回答」走非流式，但必须和流式共用一个闸门，用完要释放。"""
    from backend.services import rag_service

    async def fake_locked(question, courses=None):
        return {"answer": "好", "recommended": [], "matched": True}

    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(rag_service, "_llm_slots", lambda: sem)
    monkeypatch.setattr(rag_service, "_generate_answer_locked", fake_locked)

    async def scenario():
        out = await rag_service.generate_answer("红黑树")
        # 用完要释放：同一循环里再拿一次不该被卡住（不碰 sem 的私有属性）
        await asyncio.wait_for(sem.acquire(), timeout=0.2)
        return out

    assert asyncio.run(scenario())["answer"] == "好"


def test_generate_answer_reports_busy_instead_of_hanging(monkeypatch):
    """槽位占满时，管理员点「生成回答」要很快回 busy，而不是无限期挂着。"""
    from backend.services import rag_service

    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(rag_service, "_llm_slots", lambda: sem)
    monkeypatch.setattr(rag_service, "_QUEUE_MAX_WAIT", 0.05)

    async def scenario():
        await sem.acquire()   # 占住唯一槽位
        return await rag_service.generate_answer("红黑树")

    out = asyncio.run(scenario())
    assert out["error"] == "busy"
    assert out["answer"] == ""


def test_superseded_request_does_not_take_a_slot(monkeypatch):
    """被同会话新提问顶掉的请求，不该再占一路模型把答案生成完。"""
    from backend.services import rag_service

    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(rag_service, "_llm_slots", lambda: sem)
    called: list[str] = []

    async def fake_locked(question, history=None, courses=None):
        called.append(question)
        yield {"type": "token", "content": "不该出现"}

    monkeypatch.setattr(rag_service, "_stream_answer_locked", fake_locked)

    async def scenario():
        events = []
        async for event in rag_service.stream_answer("红黑树", still_wanted=lambda: False):
            events.append(event)
        # 槽位没被占着：同一循环里立刻能拿到
        await asyncio.wait_for(sem.acquire(), timeout=0.2)
        return events

    assert asyncio.run(scenario()) == []
    assert called == []          # 根本没进生成


def test_my_unanswered_rejects_unknown_status(db):
    """status 传非法值要 400，别静默当成「全部」。"""
    from backend.routers.chat import my_unanswered

    student = _user(db, "10000084", "student")
    with pytest.raises(HTTPException) as exc:
        my_unanswered(status="oops", user=student, db=db)
    assert exc.value.status_code == 400


def test_frontend_pagination_script():
    """前端分页条回归：管理员端不该再出现「第 x/y 页」（需要本机有 node）。"""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，跳过前端分页回归")
    script = Path(__file__).with_name("frontend_pagination.mjs")
    # Windows 上 text=True 默认用 GBK 解码，node 输出的是 UTF-8，会炸在读取线程里
    proc = subprocess.run(
        [node, str(script)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_documents_list_is_paginated_and_scoped(db):
    """文档列表：服务端分页 + 只看自己（学生） + 带上 processing 计数。"""
    from backend.models import Document
    from backend.routers.documents import list_documents

    admin = _user(db, "10000089", "admin")
    alice = _user(db, "10000088", "student")
    bob = _user(db, "10000087", "student")
    db.add(KnowledgeBase(name="共享库", created_by=admin.id))
    db.commit()
    for i in range(3):
        db.add(
            Document(
                filename=f"a{i}.txt",
                file_type="txt",
                course="共享库",
                uploaded_by=alice.id,
                status="ready",
            )
        )
    db.add(
        Document(
            filename="b.txt", file_type="txt", course="共享库", uploaded_by=bob.id, status="ready"
        )
    )
    db.add(
        Document(
            filename="c.txt",
            file_type="txt",
            course="共享库",
            uploaded_by=alice.id,
            status="processing",
        )
    )
    db.commit()

    page = list_documents(keyword="", field="filename", limit=2, offset=0, user=alice, db=db)
    assert page["total"] == 4                    # 只有 alice 的 4 篇
    assert len(page["items"]) == 2
    assert all(item.uploaded_by == alice.id for item in page["items"])
    assert page["processing"] == 1               # 全量里正在解析的条数，与当前页无关

    tail = list_documents(keyword="", field="filename", limit=2, offset=2, user=alice, db=db)
    assert len(tail["items"]) == 2

    hit = list_documents(keyword="a1", field="filename", limit=10, offset=0, user=alice, db=db)
    assert hit["total"] == 1 and hit["items"][0].filename == "a1.txt"
    assert hit["processing"] == 1    # 解析进度按可见范围全量算，不跟着搜索词走

    # 搜索框里的 % 是普通字符：不转义的话这条会把 alice 的 5 篇全捞出来
    db.add(
        Document(
            filename="50%.txt",
            file_type="txt",
            course="共享库",
            uploaded_by=alice.id,
            status="ready",
        )
    )
    db.commit()
    pct = list_documents(keyword="%", field="filename", limit=10, offset=0, user=alice, db=db)
    assert pct["total"] == 1 and pct["items"][0].filename == "50%.txt"


def test_my_unanswered_is_paginated_with_full_counts(db):
    """学生反馈：分页取当前页，待处理/已解决按全量计数（不再被 50 条截断）。"""
    from backend.models import UnansweredQuestion
    from backend.routers.chat import my_unanswered

    student = _user(db, "10000086", "student")
    other = _user(db, "10000085", "student")
    for i in range(12):
        db.add(
            UnansweredQuestion(
                display_no=i + 1,
                question=f"问题 {i}",
                status="resolved" if i < 5 else "pending",
                created_by=student.id,
            )
        )
    db.add(UnansweredQuestion(display_no=99, question="别人的", created_by=other.id))
    db.commit()

    first = my_unanswered(status="", limit=10, offset=0, user=student, db=db)
    assert first["total"] == 12
    assert len(first["items"]) == 10
    assert first["pending"] == 7 and first["resolved"] == 5   # 全量计数，按自己过滤

    second = my_unanswered(status="", limit=10, offset=10, user=student, db=db)
    assert len(second["items"]) == 2

    only_pending = my_unanswered(status="pending", limit=10, offset=0, user=student, db=db)
    assert only_pending["total"] == 7
    assert only_pending["pending"] == 7 and only_pending["resolved"] == 5
