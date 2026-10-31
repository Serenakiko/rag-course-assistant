"""统计路由：管理员仪表盘数据。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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

    # 质量指标：拒答率 = 未命中反馈数 / 提问总数；命中率 = 1 - 拒答率
    total_questions = db.scalar(
        select(func.count(Message.id)).where(Message.role == "user")
    ) or 0
    unanswered_total = db.scalar(select(func.count(UnansweredQuestion.id))) or 0
    hit_rate = None
    reject_rate = None
    if total_questions > 0:
        reject_rate = round(min(unanswered_total / total_questions, 1.0), 4)
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
    question_rows = db.execute(
        select(Message.content).where(
            Message.role == "user", Message.content.isnot(None), Message.content != ""
        )
    ).scalars().all()
    if question_rows:
        qcount: dict[str, int] = {}
        for raw in question_rows:
            norm = " ".join((raw or "").strip().split())
            if not norm:
                continue
            qcount[norm] = qcount.get(norm, 0) + 1
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
    rows = db.execute(
        select(Message.sources).where(
            Message.role == "assistant", Message.sources.isnot(None), Message.sources != "[]"
        )
    ).scalars().all()

    title_count: dict[str, int] = {}
    title_course: dict[str, str] = {}
    title_type: dict[str, str] = {}
    for raw in rows:
        try:
            sources = json.loads(raw or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(sources, list):
            continue
        for src in sources:
            if not isinstance(src, dict):
                continue
            # 只统计有明确来源类型的条目，排除未命中的「相近资料推荐」
            source_type = src.get("source_type")
            if source_type not in ("document", "knowledge_point"):
                continue
            title = (src.get("title") or "").strip()
            if not title:
                continue
            title_count[title] = title_count.get(title, 0) + 1
            if not title_course.get(title) and src.get("course"):
                title_course[title] = src.get("course")
            title_type.setdefault(title, source_type)

    # 全量资料（文档 + 手工知识点），用于找「从未被引用」的死资料
    docs = db.execute(
        select(Document.filename, Document.course).where(Document.status == "ready")
    ).all()
    points = db.execute(select(KnowledgePoint.title, KnowledgePoint.course)).all()

    ranked = [
        {
            "title": t,
            "count": c,
            "course": title_course.get(t, ""),
            "source_type": title_type.get(t, ""),
        }
        for t, c in title_count.items()
    ]
    ranked.sort(key=lambda x: (-x["count"], x["title"]))

    cited_titles = set(title_count.keys())
    dead_docs = [
        {"title": filename, "course": course or ""}
        for filename, course in docs
        if filename not in cited_titles
    ]
    dead_docs += [
        {"title": title, "course": course or ""}
        for title, course in points
        if title not in cited_titles
    ]
    # 按标题去重并排序，保持稳定输出
    seen: set[str] = set()
    dead_unique: list[dict] = []
    for item in dead_docs:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        dead_unique.append(item)
    dead_unique.sort(key=lambda x: x["title"])

    return {
        "ranked": ranked,          # 引用排行（降序）
        "dead_docs": dead_unique,  # 从未被引用的资料（文档+知识点）
        "cited_count": len(ranked),
        "dead_count": len(dead_unique),
    }
