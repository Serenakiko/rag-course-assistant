"""统计路由：管理员仪表盘数据。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import Conversation, Document, KnowledgeBase, KnowledgePoint, Message, UnansweredQuestion, User
from ..services.kb_scope import visible_base_names
from ..schemas import StatsOut

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

    # 近 7 天每天的提问数（提问 = 学生发的 user 消息，不是会话数）
    # 以前按 Conversation 计数：一个会话里问 5 个问题只算 1，和学生首页的
    # 「提问消息」口径对不上；时区按配置折算，不写死 +8。
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
            select(UnansweredQuestion).order_by(UnansweredQuestion.created_at.desc()).limit(4)
        ).scalars()
    ]

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
    )
