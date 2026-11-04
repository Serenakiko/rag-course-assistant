"""统计路由：管理员仪表盘数据。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import Conversation, Document, KnowledgeBase, KnowledgePoint, Message, UnansweredQuestion, User
from ..schemas import StatsOut
from ..services.kb_scope import visible_base_names

router = APIRouter()


@router.get("/me", summary="个人统计（当前用户，工作台）")
def my_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversations = (
        db.scalar(select(func.count(Conversation.id)).where(Conversation.user_id == user.id)) or 0
    )
    # 提问数只算学生自己发的 user 消息（回答不算提问）
    questions = (
        db.scalar(
            select(func.count(Message.id))
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == user.id, Message.role == "user")
        )
        or 0
    )
    # 后两张卡换成学生自己有的东西：自己传的文档、自己看得到的知识库
    my_documents = (
        db.scalar(select(func.count(Document.id)).where(Document.uploaded_by == user.id)) or 0
    )
    base_names = visible_base_names(user, db)
    knowledge_bases = (
        (db.scalar(select(func.count(KnowledgeBase.id))) or 0)
        if base_names is None
        else len(base_names)
    )

    # 最近 5 个会话（含消息数）
    recent_rows = db.execute(
        select(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user.id)
        .group_by(Conversation.id)
        .order_by(Conversation.id.desc())
        .limit(5)
    ).all()
    recent = [
        {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at,
            "message_count": count,
        }
        for c, count in recent_rows
    ]

    return {
        "conversations": conversations,
        "questions": questions,
        "my_documents": my_documents,
        "knowledge_bases": knowledge_bases,
        "recent_conversations": recent,
    }


@router.get("/admin/stats", response_model=StatsOut, summary="系统统计（管理员）")
def admin_stats(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> StatsOut:
    users = db.scalar(select(func.count(User.id))) or 0
    students = db.scalar(select(func.count(User.id)).where(User.role == "student")) or 0
    admins = db.scalar(select(func.count(User.id)).where(User.role == "admin")) or 0
    knowledge_bases = db.scalar(select(func.count(KnowledgeBase.id))) or 0
    documents = db.scalar(select(func.count(Document.id))) or 0
    documents_ready = (
        db.scalar(select(func.count(Document.id)).where(Document.status == "ready")) or 0
    )
    knowledge_points = db.scalar(select(func.count(KnowledgePoint.id))) or 0
    conversations = db.scalar(select(func.count(Conversation.id))) or 0
    messages = db.scalar(select(func.count(Message.id))) or 0

    # 近 7 天每天的提问数（提问 = 学生发的 user 消息）；时区按配置折算
    today_local = datetime.now(timezone.utc) + timedelta(hours=settings.UTC_OFFSET_HOURS)
    start_local = today_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
    start_utc = start_local - timedelta(hours=settings.UTC_OFFSET_HOURS)
    tz_modifier = f"{settings.UTC_OFFSET_HOURS:+d} hours"

    question_day = func.strftime("%Y-%m-%d", Message.created_at, tz_modifier)
    rows = db.execute(
        select(question_day, func.count(Message.id))
        .where(Message.role == "user", Message.created_at >= start_utc)
        .group_by(question_day)
    ).all()
    daily_map = {str(d): c for d, c in rows}

    daily: list[dict] = []
    for i in range(7):
        day = start_local + timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        daily.append({"date": key, "count": daily_map.get(key, 0)})

    today_key = today_local.strftime("%Y-%m-%d")
    today_questions = daily_map.get(today_key, 0)

    # 各知识库文档数分布：文档表本身带 course，直接按文档计数（和内容管理里的「文档数」同口径）
    kb_rows = db.execute(
        select(KnowledgeBase.name, func.count(Document.id))
        .outerjoin(Document, Document.course == KnowledgeBase.name)
        .group_by(KnowledgeBase.id)
        .order_by(KnowledgeBase.id)
    ).all()
    kb_doc_distribution = [{"name": name, "count": count or 0} for name, count in kb_rows]

    # 反馈：待处理 / 已解答 + 最近 4 条
    unanswered_pending = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(UnansweredQuestion.status == "pending")
        )
        or 0
    )
    unanswered_resolved = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(UnansweredQuestion.status == "resolved")
        )
        or 0
    )
    recent_unanswered = [
        {
            "id": u.id,
            "question": u.question,
            "status": u.status,
            "created_at": u.created_at,
            "answered_at": u.answered_at,
        }
        for u in db.execute(
            select(UnansweredQuestion).order_by(UnansweredQuestion.created_at.desc()).limit(5)
        ).scalars()
    ]

    # 质量指标：统一到 message 级事件口径。
    # 拒答率 = 拒答的 assistant 回答数 / 用户提问总数（user 消息条数）。
    # 分子分母都是 message 级：分子是「这一轮是否拒答」，直接读落库时写好的 rejected 字段，
    # 而不是拿中文文案 LIKE（门控拒答与模型拒答的措辞不同，LIKE 会漏掉最主要的一类拒答，
    # 导致 hit_rate 系统性虚高）。
    total_questions = db.scalar(
        select(func.count(Message.id)).where(Message.role == "user")
    ) or 0

    rejected = db.scalar(
        select(func.count(Message.id)).where(
            Message.role == "assistant", Message.rejected == 1
        )
    ) or 0

    hit_rate = None
    reject_rate = None
    if total_questions > 0:
        # 拒答率按 message 级事件：拒答轮次 / 提问总数（同一学生同一问题问两次算两次提问）
        reject_rate = round(min(rejected / total_questions, 1.0), 4)
        hit_rate = round(1 - reject_rate, 4)

    # 反馈平均解决时长（小时）：已解决反馈的 answered_at - created_at 均值
    avg_resolve_hours = None
    resolve_rows = db.execute(
        select(UnansweredQuestion.created_at, UnansweredQuestion.answered_at)
        .where(
            UnansweredQuestion.status == "resolved",
            UnansweredQuestion.answered_at.isnot(None),
        )
    ).all()
    if resolve_rows:
        deltas = [
            (answered - created).total_seconds() / 3600
            for created, answered in resolve_rows
            if answered and created
        ]
        if deltas:
            avg_resolve_hours = round(sum(deltas) / len(deltas), 1)

    # 学生评价计数
    feedback_up = db.scalar(
        select(func.count(Message.id)).where(Message.feedback == "up")
    ) or 0
    feedback_down = db.scalar(
        select(func.count(Message.id)).where(Message.feedback == "down")
    ) or 0

    # 热门问题 Top N：学生提问原文去重计数（归一化：去首尾空白、统一空白符），
    # 取被问得最多的问题。不用分词（无 jieba），原问归一化更能反映「高频困惑点」，
    # 便于管理员针对性补料。
    hot_questions: list[dict] = []
    question_result = db.execute(
        select(Message.content).where(
            Message.role == "user", Message.content.isnot(None), Message.content != ""
        )
    )
    qcount: dict[str, int] = {}
    # 分批取，避免消息量上来后一次性把全部提问读进内存
    while batch := question_result.fetchmany(1000):
        for (raw,) in batch:
            norm = " ".join((raw or "").strip().split())
            if not norm:
                continue
            qcount[norm] = qcount.get(norm, 0) + 1
    if qcount:
        top = sorted(qcount.items(), key=lambda x: (-x[1], x[0]))[:8]
        hot_questions = [{"question": q, "count": c} for q, c in top]

    return StatsOut(
        users=users,
        students=students,
        admins=admins,
        knowledge_bases=knowledge_bases,
        documents=documents,
        documents_ready=documents_ready,
        knowledge_points=knowledge_points,
        conversations=conversations,
        messages=messages,
        today_questions=today_questions,
        daily_questions=daily,
        kb_doc_distribution=kb_doc_distribution,
        unanswered_pending=unanswered_pending,
        unanswered_resolved=unanswered_resolved,
        recent_unanswered=recent_unanswered,
        hit_rate=hit_rate,
        reject_rate=reject_rate,
        avg_resolve_hours=avg_resolve_hours,
        feedback_up=feedback_up,
        feedback_down=feedback_down,
        hot_questions=hot_questions,
    )


@router.get("/admin/doc-usage", summary="文档引用排行（管理员）")
def doc_usage_stats(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """统计每份资料被回答引用（检索命中）的次数，识别「死资料」。

    只统计真实来源：messages.sources 里 source_type 为 document / knowledge_point
    的条目（这些才有 doc_id/point_id，title 是真实的文档名/知识点名）。
    未命中时系统塞进 sources 的「相近资料推荐」没有 source_type，
    title 是被截断的问题文本，必须排除，否则会把问题当资料名混进排行。

    死资料 = documents（ready 状态）的 filename + knowledge_points 的 title
    中，从未作为真实来源被引用的那一部分。
    """
    source_result = db.execute(
        select(Message.sources).where(
            Message.role == "assistant", Message.sources.isnot(None), Message.sources != "[]"
        )
    )

    # 用 (source_type, doc_id/point_id) 作为统计键，而不是标题字符串：
    # 不同课程的文档可能同名（都叫「第一章.pdf」），按标题统计会把它们的引用
    # 互相叠加、死资料判断也误判。id 才是资料在向量库里的唯一身份。
    key_count: dict[tuple, int] = {}
    key_course: dict[tuple, str] = {}
    key_title: dict[tuple, str] = {}
    # 分批取，避免历史回答多时把全部 sources 一次性读进内存
    while batch := source_result.fetchmany(1000):
        for (raw,) in batch:
            try:
                sources = json.loads(raw or "[]")
            except (ValueError, TypeError):
                continue
            if not isinstance(sources, list):
                continue
            for src in sources:
                if not isinstance(src, dict):
                    continue
                source_type = src.get("source_type")
                if source_type not in ("document", "knowledge_point"):
                    continue
                oid = src.get("doc_id") if source_type == "document" else src.get("point_id")
                if oid is None:
                    # 老数据可能没有 id（只有 title），退回用 title 兜底，保证不丢历史计数
                    oid = src.get("title") or ""
                key = (source_type, str(oid))
                title = (src.get("title") or "").strip() or str(oid)
                key_count[key] = key_count.get(key, 0) + 1
                if not key_course.get(key) and src.get("course"):
                    key_course[key] = src.get("course")
                key_title[key] = title

    # 全量资料（文档 + 手工知识点），用于找「从未被引用」的死资料
    docs = db.execute(
        select(Document.id, Document.filename, Document.course).where(Document.status == "ready")
    ).all()
    points = db.execute(
        select(KnowledgePoint.id, KnowledgePoint.title, KnowledgePoint.course).where(
            KnowledgePoint.is_auto == 0
        )
    ).all()

    cited_keys = set(key_count.keys())
    # 每份资料的「展示名」：文档去扩展名，知识点用标题
    doc_display = {("document", str(d)): (Path(f).stem or f, c or "") for d, f, c in docs}
    point_display = {("knowledge_point", str(p)): (t, c or "") for p, t, c in points}
    display = {**doc_display, **point_display}

    ranked = [
        {
            "title": key_title.get(k) or display.get(k, (str(k[1]), ""))[0],
            "count": c,
            "course": key_course.get(k) or display.get(k, ("", ""))[1],
            "source_type": k[0],
        }
        for k, c in key_count.items()
    ]
    ranked.sort(key=lambda x: (-x["count"], x["title"]))

    # 死资料 = 从未作为真实来源被引用的文档（ready）+ 手工知识点（is_auto=0）
    dead_docs = [
        {"title": name, "course": course}
        for (key, (name, course)) in doc_display.items()
        if key not in cited_keys
    ]
    dead_docs += [
        {"title": name, "course": course}
        for (key, (name, course)) in point_display.items()
        if key not in cited_keys
    ]
    dead_docs.sort(key=lambda x: x["title"])

    return {
        "ranked": ranked,          # 引用排行（降序）
        "dead_docs": dead_docs,    # 从未被引用的资料（文档+知识点）
        "cited_count": len(ranked),
        "dead_count": len(dead_docs),
    }
