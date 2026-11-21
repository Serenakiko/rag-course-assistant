"""此前零测试的纯函数与服务：chunker / rag_prompts / notify / 启动维护。

这些模块要么是纯函数（好测但没人测），要么是「失败也不能影响主流程」的旁路
（notify），或者只在 lifespan 里跑（启动维护）——没测试的时候坏了没人发现。
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.models import Base, Notification, User


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# ---------------- chunker ----------------


def test_split_text_returns_empty_for_blank():
    from backend.services.chunker import split_text

    assert split_text("") == []
    assert split_text("   \n\t ") == []


def test_split_text_keeps_sentence_punctuation_and_overlap(monkeypatch):
    from backend.services import chunker

    monkeypatch.setattr(chunker.settings, "CHUNK_SIZE", 20)
    monkeypatch.setattr(chunker.settings, "CHUNK_OVERLAP", 6)
    text = "第一句话说明课程目标。第二句话说明考核方式。第三句话说明参考资料。"
    chunks = chunker.split_text(text)

    assert len(chunks) > 1
    # keep_separator="end"：块尾必须留着句号，不能把「目标。」切成「目标」
    assert all(c.endswith("。") for c in chunks)
    # 每块都不能超过设定长度（中文字符按 1 计）
    assert all(len(c) <= 20 for c in chunks)
    # 相邻块有重叠：上一块结尾的内容要能在下一块开头附近找到
    joined = "".join(chunks)
    assert joined.count("。") == 3


# ---------------- rag_prompts ----------------


def test_is_unmatched_answer_matches_leading_marker_only():
    from backend.services.rag_prompts import is_unmatched_answer

    assert is_unmatched_answer("【知识库未收录】知识库中暂无相关内容。") is True
    assert is_unmatched_answer("知识库未收录 这门课没有讲") is True
    # 正文中间出现不算拒答：这是正常回答里在解释概念
    assert is_unmatched_answer("先说结论：这句话和「知识库未收录」是两个概念。" + "x" * 40) is False
    # 开头 20 字之外的特征短语不触发
    assert is_unmatched_answer("好的，我来回答这个问题。补充说明：知识库未收录") is False
    assert is_unmatched_answer("") is False


def test_system_prompt_demands_citation_and_unmatched_marker():
    from backend.services.rag_prompts import SYSTEM_PROMPT

    assert "{context}" in SYSTEM_PROMPT
    assert "【知识库未收录】" in SYSTEM_PROMPT
    assert "[编号]" in SYSTEM_PROMPT


# ---------------- notify ----------------


def _user(db: Session, account: str, role: str = "student") -> User:
    user = User(account=account, password_hash="x", username=account, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_notify_skips_without_user(db):
    from backend.services.notify import notify

    assert notify(db, None, "system", "标题") is None
    assert db.query(Notification).count() == 0


def test_notify_writes_truncated_row(db):
    from backend.services.notify import notify

    user = _user(db, "30000001")
    item = notify(db, user.id, "x" * 100, "t" * 500, "body", "/static/app.html")
    assert item is not None
    assert item.kind == "x" * 32
    assert item.title == "t" * 200
    assert item.link == "/static/app.html"


def test_notify_swallows_failure_and_rolls_back(db, monkeypatch):
    from backend.services.notify import notify

    user = _user(db, "30000002")

    def boom(*_args, **_kwargs):
        raise RuntimeError("锁冲突")

    monkeypatch.setattr(db, "commit", boom)
    assert notify(db, user.id, "system", "标题") is None


def test_notify_admins_only_targets_admins(db):
    from backend.services.notify import notify_admins

    _user(db, "30000003", "student")
    admin = _user(db, "30000004", "admin")
    assert notify_admins(db, "system", "有文档处理失败") == 1
    rows = db.query(Notification).all()
    assert [r.user_id for r in rows] == [admin.id]


# ---------------- 启动维护（lifespan 路径） ----------------


def test_run_startup_maintenance_tolerates_failures(monkeypatch):
    from backend.services import vector_reconcile

    calls = []

    def boom(*_args, **_kwargs):
        calls.append("sweep")
        raise RuntimeError("向量库没起来")

    def boom2(*_args, **_kwargs):
        calls.append("rebuild")
        raise RuntimeError("重建失败")

    monkeypatch.setattr(vector_reconcile, "sweep_orphan_vectors", boom)
    monkeypatch.setattr(vector_reconcile, "rebuild_vectors_for_embedding_model_change", boom2)

    # 不许抛：启动阶段向量库不可用很常见（还没拉模型），直接把服务起不来才是灾难
    vector_reconcile.run_startup_maintenance()
    # 顺序：先按模型对账（可能要重建全库），再扫孤儿；两步失败都只记日志
    assert calls == ["rebuild", "sweep"]
