"""对话路由：会话管理 + SSE 流式问答。"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

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
    """按时间升序取最近 limit 轮对话，并按字符预算从旧到新丢弃。

    只按轮数截断的话，长回答 + 长资料很容易把 8192 的上下文窗口挤爆。
    """
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
    picked: list[tuple[str, str]] = []
    used = 0
    for m in msgs:  # 从最新往前挑
        size = len(m.content or "")
        if picked and used + size > settings.HISTORY_CHAR_BUDGET:
            break
        picked.append((m.role, m.content))
        used += size
    return list(reversed(picked))


def _next_unanswered_no(db: Session) -> int:
    """反馈的下一个展示编号；删除后会被重排，不依赖主键。"""
    return (db.scalar(select(func.max(UnansweredQuestion.display_no))) or 0) + 1


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

@router.get("/admin/conversations", summary="全部会话（管理员，分页）")
def admin_list_conversations(
    limit: int = 50,
    offset: int = 0,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """分页返回会话列表；数据量大的时候不再整表塞给前端。"""
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    total = db.scalar(select(func.count(Conversation.id))) or 0
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
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": conv.id,
                "title": conv.title,
                "username": username,
                "real_name": real_name,
                "message_count": count,
                "created_at": conv.created_at,
            }
            for conv, username, real_name, count in rows
        ],
    }


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


def _kb_list(raw: str | None) -> list[str]:
    """把会话里存的 knowledge_bases JSON 解析成列表。"""
    try:
        arr = json.loads(raw or "[]")
        return [c for c in arr if c] if isinstance(arr, list) else []
    except ValueError:
        return []


def _kb_from_conv(raw: str | None) -> str:
    """从会话的 knowledge_bases（JSON 数组）取第一个作为知识库名。"""
    try:
        arr = json.loads(raw or "[]")
        if arr:
            return arr[0]
    except Exception:
        pass
    return ""


@router.get("/admin/messages", summary="全部问答对（管理员，分页）")
def admin_qa_pairs(
    q: str = "",
    field: str = "question",
    limit: int = 20,
    offset: int = 0,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """分页返回 user 提问 + 紧随其后的 assistant 回答，用于对话历史表格。

    以前是把 messages 整表拉出来在 Python 里配对再切片；消息一多就要把整个表
    读进内存。现在只取当前页的提问（每页最多 100 条），回答按需查。
    """
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    base = (
        select(
            Message.id,
            Message.content,
            Message.conversation_id,
            Message.created_at,
            Conversation.knowledge_bases,
            User.username,
            User.real_name,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .where(Message.role == "user")
    )
    # 搜索范围：问题 / 回答 / 提问人（回答按"同一会话中此后的 assistant 消息"匹配）
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        if field == "answer":
            later = aliased(Message)
            base = base.where(
                select(later.id)
                .where(
                    later.conversation_id == Message.conversation_id,
                    later.role == "assistant",
                    later.id > Message.id,
                    later.content.like(like),
                )
                .exists()
            )
        elif field == "user":
            base = base.where(or_(User.username.like(like), User.real_name.like(like)))
        else:
            base = base.where(Message.content.like(like))

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(Message.id.asc()).limit(limit).offset(offset)
    ).all()

    items: list[dict] = []
    for r in rows:
        ans = db.execute(
            select(Message.content, Message.sources)
            .where(
                Message.conversation_id == r.conversation_id,
                Message.role == "assistant",
                Message.id > r.id,
            )
            .order_by(Message.id.asc())
            .limit(1)
        ).first()
        try:
            sources = json.loads(ans[1] or "[]") if ans else []
        except ValueError:
            sources = []
        items.append(
            {
                "id": r.id,
                "question": r.content,
                "answer": ans[0] if ans else "",
                "knowledge_base": _kb_from_conv(r.knowledge_bases),
                "sources": sources if isinstance(sources, list) else [],
                "conversation_id": r.conversation_id,
                "username": r.username,
                "real_name": r.real_name,
                "created_at": r.created_at,
            }
        )
    return {"total": total, "items": items}



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

    selected = [c for c in payload.knowledge_bases if c]

    # 会话记录本次使用的知识库；重新生成且没带选择时沿用原记录，不能清空
    if selected:
        conv.knowledge_bases = json.dumps(selected, ensure_ascii=False)
        db.commit()
    elif not payload.regenerate:
        conv.knowledge_bases = "[]"
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
        if not selected:
            selected = _kb_list(conv.knowledge_bases)
    history = _load_history(db, conversation_id, settings.HISTORY_LIMIT)
    if not payload.regenerate:
        db.add(Message(conversation_id=conversation_id, role="user", content=payload.message))
        db.commit()

    # 检索范围：学生只能在自己可见的知识库里检索。
    # 不做这一步的话，"列表里看不到的知识库"照样能被提问捞出来。
    allowed = allowed_course_names(user, db)
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
        failed = False
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
                    # 失败不写进正文，也不落库成正式回答
                    failed = True
                    yield _sse(event)
                    break
                yield _sse(event)
            if failed:
                return
            # 记录“未命中问题”：距离门控拦截（unmatched 事件）或模型判定知识库未收录（未收录特征短语）
            full_answer = "".join(answer_parts) or ""
            if unmatched or is_unmatched_answer(full_answer):
                session = SessionLocal()
                try:
                    course = (unmatched or {}).get("course", "") or ""
                    related = json.dumps(
                        (unmatched or {}).get("recommended", []),
                        ensure_ascii=False,
                    )
                    # 同一学生反复问同一个问题时不重复刷反馈，更新已存在的那条
                    existing = session.scalar(
                        select(UnansweredQuestion).where(
                            UnansweredQuestion.question == payload.message,
                            UnansweredQuestion.created_by == user.id,
                            UnansweredQuestion.status == "pending",
                        )
                    )
                    if existing is not None:
                        existing.course = course
                        existing.related_points = related
                    else:
                        session.add(
                            UnansweredQuestion(
                                display_no=_next_unanswered_no(session),
                                question=payload.message,
                                course=course,
                                related_points=related,
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
