"""对话路由：会话管理 + SSE 流式问答。"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user, require_role
from ..models import Conversation, Message, UnansweredQuestion, User, utcnow
from ..schemas import (
    AdminConversationOut,
    AskRequest,
    ConversationOut,
    CreateConversationRequest,
    MessageOut,
    RenameConversationRequest,
)
from ..services import rag_service
from ..services.kb_scope import allowed_course_names
from ..services.rag_service import is_unmatched_answer

router = APIRouter()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _load_history(db: Session, conversation_id: int, limit: int) -> list[tuple[str, str]]:
    """按时间升序取最近 limit 轮对话。"""
    msgs = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit * 2)
        )
        .scalars()
        .all()
    )
    return [(m.role, m.content) for m in reversed(msgs)]


@router.post("/sessions", response_model=ConversationOut, summary="新建会话")
def create_session(
    payload: CreateConversationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Conversation:
    conv = Conversation(
        user_id=user.id,
        title=payload.title.strip() or "新会话",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.get("/unanswered/mine", summary="我的问题反馈（学生）")
def my_unanswered(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """当前学生的全部反馈记录（pending=待处理 / resolved=已解决）。
    read=False 表示已解决但尚未查看，用于前端未读角标；pending 记录 read 恒为 False。"""
    rows = db.scalars(
        select(UnansweredQuestion)
        .where(UnansweredQuestion.created_by == user.id)
        .order_by(UnansweredQuestion.id.desc())
        .limit(50)
    ).all()
    return [
        {
            "id": q.id,
            "question": q.question,
            "course": q.course or "",
            "status": q.status,
            "answer": q.answer or "",
            "created_at": q.created_at,
            "answered_at": q.answered_at,
            "read": q.read_at is not None,
        }
        for q in rows
    ]


@router.post("/unanswered/{qid}/read", summary="标记我的反馈已读（学生）")
def mark_unanswered_read(
    qid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """学生打开「我的反馈」查看答案后标记已读，用于未读角标清零。"""
    q = db.get(UnansweredQuestion, qid)
    if q is None or q.created_by != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if q.read_at is None:
        q.read_at = utcnow()
        db.commit()
    return {"ok": True}


@router.get("/sessions", response_model=list[ConversationOut], summary="会话列表")
def list_sessions(
    kb: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Conversation]:
    """会话列表；传 kb 时只返回使用过该知识库的会话（空/不传返回全部）。"""
    stmt = select(Conversation).where(Conversation.user_id == user.id)
    if kb:
        stmt = stmt.where(Conversation.knowledge_bases.like(f'%"{kb}"%'))
    stmt = stmt.order_by(Conversation.id.desc())
    return list(db.scalars(stmt))


@router.patch("/sessions/{conv_id}", response_model=ConversationOut, summary="重命名会话")
def rename_session(
    conv_id: int,
    payload: RenameConversationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    conv.title = payload.title.strip() or conv.title
    db.commit()
    db.refresh(conv)
    return conv


@router.delete("/sessions/{conv_id}", summary="删除会话")
def delete_session(
    conv_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{conv_id}/messages", response_model=list[MessageOut], summary="会话消息")
def list_messages(
    conv_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Message]:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return list(db.scalars(select(Message).where(Message.conversation_id == conv_id)))


# ---------- 对话管理（管理员） ----------

@router.get("/admin/conversations", response_model=list[AdminConversationOut], summary="全部会话（管理员）")
def admin_list_conversations(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(
            Conversation,
            User.username,
            User.real_name,
            func.count(Message.id),
        )
        .join(User, Conversation.user_id == User.id)
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .group_by(Conversation.id)
        .order_by(Conversation.id.desc())
    ).all()
    return [
        {
            "id": conv.id,
            "title": conv.title,
            "username": username,
            "real_name": real_name,
            "message_count": count,
            "created_at": conv.created_at,
        }
        for conv, username, real_name, count in rows
    ]


@router.get("/admin/conversations/{conv_id}/messages", response_model=list[MessageOut], summary="会话消息（管理员）")
def admin_list_messages(
    conv_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[Message]:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return list(
        db.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id))
    )


@router.delete("/admin/conversations/{conv_id}", summary="删除会话（管理员）")
def admin_delete_conversation(
    conv_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(conv)
    db.commit()
    return {"ok": True}


def _kb_from_conv(raw: str | None) -> str:
    """从会话的 knowledge_bases（JSON 数组）取第一个作为知识库名。"""
    try:
        arr = json.loads(raw or "[]")
        if arr:
            return arr[0]
    except Exception:
        pass
    return ""


@router.get("/admin/messages", summary="全部问答对（管理员，按时间倒序）")
def admin_list_messages(
    course: str = "",
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    """返回每一轮 user 提问 + assistant 回答的配对列表，用于对话历史表格。"""
    rows = db.execute(
        select(
            Message.id,
            Message.conversation_id,
            Message.role,
            Message.content,
            Message.sources,
            Message.created_at,
            Conversation.knowledge_bases,
            Conversation.user_id,
            User.username,
            User.real_name,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .order_by(Message.id.asc())
    ).all()

    # 按会话把 user 提问与随后的 assistant 回答配对（升序遍历；
    # 提问落库后回答随即落库，故同会话中紧随其后、id 更大的第一条 assistant 即其回答）
    pairs: list[dict] = []
    pending: dict[int, object] = {}  # conversation_id -> 尚未配对的 user 行
    for r in rows:
        if r.role == "user":
            pending[r.conversation_id] = r
            continue
        if r.role != "assistant":
            continue
        u = pending.pop(r.conversation_id, None)
        if u is None:
            continue
        # 知识库取会话记录（提问时选择的知识库）；旧会话无记录则为空
        # （前端显示“全部知识库”），不用检索命中来源的课程名冒充归属
        kb_name = _kb_from_conv(r.knowledge_bases)
        pairs.append({
            "id": u.id,
            "question": u.content,
            "answer": r.content,
            "knowledge_base": kb_name,
            "sources": json.loads(r.sources or "[]"),
            "conversation_id": u.conversation_id,
            "username": u.username,
            "real_name": u.real_name,
            "created_at": u.created_at,
        })

    # 尚未配对的提问（无回答，如生成中断）也列出，answer 为空
    for u in pending.values():
        kb_name = _kb_from_conv(u.knowledge_bases)
        pairs.append({
            "id": u.id,
            "question": u.content,
            "answer": "",
            "knowledge_base": kb_name,
            "sources": [],
            "conversation_id": u.conversation_id,
            "username": u.username,
            "real_name": u.real_name,
            "created_at": u.created_at,
        })

    # 最新在前
    pairs.sort(key=lambda p: p["id"], reverse=True)

    if course.strip():
        pairs = [p for p in pairs if course.strip() in (p.get("knowledge_base") or "")]
    return pairs


@router.post("/ask", summary="智能问答（SSE 流式）")
async def ask(
    payload: AskRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    # 1. 定位或创建会话
    conversation_id = payload.conversation_id
    if conversation_id is None:
        conv = Conversation(user_id=user.id, title=payload.message.strip()[:24] or "新会话")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conversation_id = conv.id
    else:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在")

    # 会话记录本次使用的知识库（历史会话按库筛选）
    conv.knowledge_bases = json.dumps(payload.knowledge_bases, ensure_ascii=False)
    db.commit()

    # 2. 保存用户消息；重新生成模式不重复落库提问，并删除上一条待替换的回答
    if payload.regenerate:
        last_assistant = db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.id.desc())
            .limit(1)
        ).first()
        if last_assistant is not None:
            db.delete(last_assistant)
            db.commit()
    else:
        db.add(Message(conversation_id=conversation_id, role="user", content=payload.message))
        db.commit()
    history = _load_history(db, conversation_id, settings.HISTORY_LIMIT)

    # 检索范围：学生只能在自己可见的知识库里检索。
    # 不做这一步的话，"列表里看不到的知识库"照样能被提问捞出来。
    allowed = allowed_course_names(user, db)
    selected = [c for c in payload.knowledge_bases if c]
    if allowed is None:
        courses = selected or None          # 管理员：不选就是全部
    else:
        forbidden = [c for c in selected if c not in allowed]
        if forbidden:
            raise HTTPException(status_code=403, detail="没有权限检索该知识库")
        courses = selected or allowed       # 学生：不选就限定在可见范围内

    # 3. SSE 流式生成（结束时把助手回答落库）
    async def event_gen() -> AsyncGenerator[str, None]:
        answer_parts: list[str] = []
        sources_json: str = "[]"
        unmatched: dict | None = None
        try:
            async for event in rag_service.stream_answer(
                payload.message,
                history,
                courses=courses,
            ):
                if event["type"] == "token":
                    answer_parts.append(event["content"])
                elif event["type"] == "sources":
                    sources_json = json.dumps(event["sources"], ensure_ascii=False)
                elif event["type"] == "unmatched":
                    unmatched = event
                elif event["type"] == "error":
                    answer_parts.append(f"（回答失败：{event['message']}）")
                yield _sse(event)
            # 记录“未命中问题”：距离门控拦截（unmatched 事件）或模型判定知识库未收录（未收录特征短语）
            full_answer = "".join(answer_parts) or ""
            if unmatched or is_unmatched_answer(full_answer):
                session = SessionLocal()
                try:
                    session.add(
                        UnansweredQuestion(
                            question=payload.message,
                            course=(unmatched or {}).get("course", "") or "",
                            related_points=json.dumps(
                                (unmatched or {}).get("recommended", []),
                                ensure_ascii=False,
                            ),
                            status="pending",
                            created_by=user.id,
                        )
                    )
                    session.commit()
                finally:
                    session.close()
            # 落库
            session = SessionLocal()
            try:
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_answer or "（无回答）",
                        sources=sources_json,
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"服务异常：{exc}"})

    return StreamingResponse(event_gen(), media_type="text/event-stream")
