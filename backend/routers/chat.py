"""对话路由：会话管理 + SSE 流式问答。"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user, require_role
from ..models import Conversation, Message, UnansweredQuestion, User, utcnow
from ..schemas import (
    AskRequest,
    ConversationOut,
    CreateConversationRequest,
    FeedbackRequest,
    MessageOut,
    RenameConversationRequest,
)
from ..services import rag_service
from ..services.kb_scope import allowed_course_names
from ..services.like import contains
from ..services.rag_service import is_unmatched_answer

router = APIRouter()
logger = logging.getLogger(__name__)

# 一个字都没生成就结束时落库的占位提示，不当作回答喂给模型
_NO_ANSWER_HINT = "（这条提问没有生成出回答，可能被中断了，重新发一次试试。）"

# ---------- 每用户问答限流 ----------
# 单机本地模型一次只吐一路 token，一个学生连点就能把闸门与排队打满。
# 这里给 ask 加「每用户并发上限 + 时间窗口频率上限」，与 rag_service 的全局闸门互补。
_ASK_MAX_CONCURRENT = 2          # 同一用户同时进行的生成路数上限
_ASK_WINDOW_SECONDS = 60.0       # 频率窗口
_ASK_MAX_PER_WINDOW = 20         # 窗口内最多提问次数
_user_ask_state: dict[int, dict] = {}
_ask_lock = threading.Lock()


def _ask_allow(user_id: int) -> tuple[bool, str]:
    """判断该用户这一问是否放行；返回 (是否放行, 拒绝原因)。"""
    now = time.monotonic()
    with _ask_lock:
        st = _user_ask_state.get(user_id)
        if st is None:
            st = {"active": 0, "stamps": []}
            _user_ask_state[user_id] = st
        # 清过期时间戳
        st["stamps"] = [t for t in st["stamps"] if now - t < _ASK_WINDOW_SECONDS]
        if st["active"] >= _ASK_MAX_CONCURRENT:
            return False, "你还有回答正在生成，请等它完成后再提问"
        if len(st["stamps"]) >= _ASK_MAX_PER_WINDOW:
            return False, f"提问太频繁，请 {_ASK_WINDOW_SECONDS:.0f} 秒后再试"
        st["active"] += 1
        st["stamps"].append(now)
        return True, ""


def _ask_release(user_id: int) -> None:
    with _ask_lock:
        st = _user_ask_state.get(user_id)
        if st is not None:
            st["active"] = max(0, st["active"] - 1)


def _bump_gen_seq(conversation_id: int) -> int:
    """原子自增会话生成序号并返回新值（用于同会话新提问顶掉旧流）。"""
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None:
            return 0
        conv.gen_seq = int(conv.gen_seq or 0) + 1
        session.commit()
        return conv.gen_seq


def _current_gen_seq(conversation_id: int) -> int:
    """读会话当前生成序号（供 still_wanted 判定最新一轮）。"""
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        return int(conv.gen_seq or 0) if conv is not None else 0


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _save_answer(
    conversation_id: int,
    turn_user_id: int | None,
    content: str,
    sources: str,
    interrupted: int,
    rejected: int = 0,
) -> int | None:
    """把一轮回答落库并返回 message.id。

    这一轮之后已有新消息就不再落库，避免出现两条答案；失败只记日志。
    rejected：这一轮是否拒答（1=是），供统计拒答率直接读字段。
    """
    session = SessionLocal()
    try:
        latest = session.scalar(
            select(func.max(Message.id)).where(Message.conversation_id == conversation_id)
        )
        if turn_user_id is not None and latest is not None and latest > turn_user_id:
            logger.info("会话 %s 已进入下一轮，丢弃上一轮回答", conversation_id)
            return None
        msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            sources=sources,
            interrupted=interrupted,
            rejected=rejected,
        )
        session.add(msg)
        session.commit()
        return msg.id
    except Exception:  # noqa: BLE001
        logger.exception("保存回答失败 conversation_id=%s", conversation_id)
        session.rollback()
        return None
    finally:
        session.close()


def _load_history(db: Session, conversation_id: int, limit: int) -> list[tuple[str, str]]:
    """按时间升序取最近 limit 轮对话，并按 token 预算从旧到新丢弃。

    这里做粗裁（省内存），最终由 rag_service 按 token 预算精裁（防超窗）。
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
    # 粗裁预算：与 rag_service 的 token 预算同量级（char × CHAR_TO_TOKEN）
    budget_chars = int((settings.LLM_CTX - settings.LLM_MAX_TOKENS) * 0.8 / settings.CHAR_TO_TOKEN)
    picked: list[tuple[str, str]] = []
    used = 0
    for m in msgs:  # 从最新往前挑
        # 没生成出回答的占位提示、中断的半截回答都不是有效上下文
        if m.role == "assistant" and (m.interrupted or (m.content or "").startswith(_NO_ANSWER_HINT)):
            continue
        size = len(m.content or "")
        if picked and used + size > budget_chars:
            break
        picked.append((m.role, m.content))
        used += size
    return list(reversed(picked))


def _record_unmatched(
    question: str,
    user_id: int,
    unmatched: dict | None,
) -> None:
    """把未命中问题落库（同人同问题去重：已解决的保留，待处理的更新关联信息）。

    并发下同一学生几乎同时问同一个未命中问题，可能出现两条重复记录：
    这里在唯一索引（question + created_by）基础上用 on_conflict_do_nothing 兜底，
    落在后面的那条直接忽略，不再产生重复。
    """
    course = (unmatched or {}).get("course", "") or ""
    related = json.dumps((unmatched or {}).get("recommended", []), ensure_ascii=False)
    session = SessionLocal()
    try:
        question_text = question.strip()
        existing = session.scalar(
            select(UnansweredQuestion)
            .where(
                UnansweredQuestion.question == question_text,
                UnansweredQuestion.created_by == user_id,
            )
            .order_by(UnansweredQuestion.id.desc())
            .limit(1)
        )
        if existing is not None:
            if existing.status == "pending":
                existing.course = course
                existing.related_points = related
            session.commit()
            return
        # 不存在时用 upsert 语义插入，避免「先查后插」的竞态窗口产生重复。
        # display_no 不再维护（展示编号统一用主键 id），避免 max(display_no)+1 与
        # 删除时的全表重排两套机制打架：并发拿到同号、编号漂移。
        session.execute(
            sqlite_insert(UnansweredQuestion)
            .values(
                question=question_text,
                course=course,
                related_points=related,
                status="pending",
                created_by=user_id,
            )
            .on_conflict_do_nothing(index_elements=["question", "created_by"])
        )
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("记录未命中问题失败 question=%s", question_text)
    finally:
        session.close()


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


@router.get("/unanswered/mine", summary="我的问题反馈（学生，分页）")
def my_unanswered(
    status: str = "",
    limit: int = 10,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """当前学生的反馈记录：返回 {total, pending, resolved, items}。

    分页取当前页，两个计数按全量单独算；read=False 表示已解决但未查看。
    """
    if status not in ("", "pending", "resolved"):
        raise HTTPException(status_code=400, detail="status 仅支持 pending / resolved")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    mine = UnansweredQuestion.created_by == user.id
    pending = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(
                mine, UnansweredQuestion.status == "pending"
            )
        )
        or 0
    )
    resolved = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(
                mine, UnansweredQuestion.status == "resolved"
            )
        )
        or 0
    )
    # 未读数：已解决但尚未查看（read_at 为空），用于顶部通知角标
    unread = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(
                mine,
                UnansweredQuestion.status == "resolved",
                UnansweredQuestion.read_at.is_(None),
            )
        )
        or 0
    )

    where = [mine]
    if status in ("pending", "resolved"):
        where.append(UnansweredQuestion.status == status)
    total = db.scalar(select(func.count(UnansweredQuestion.id)).where(*where)) or 0
    rows = db.scalars(
        select(UnansweredQuestion)
        .where(*where)
        # 按提交时间倒序（不是 id），前端「最近几条」靠这个顺序取
        .order_by(UnansweredQuestion.created_at.desc(), UnansweredQuestion.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "pending": pending,
        "resolved": resolved,
        "unread": unread,
        "items": [
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
        ],
    }


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
    if q.status != "resolved":
        raise HTTPException(status_code=400, detail="这条反馈还没有回答")
    if q.read_at is None:
        q.read_at = utcnow()
        db.commit()
    return {"ok": True}


@router.get("/sessions", summary="会话列表（分页 + 标题搜索）")
def list_sessions(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    order: str = "desc",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """我的会话列表：服务端分页 + 标题关键字，返回 {total, items}。

    消息条数用一次 group by 查出；kb 传课程名时只返回用过该知识库的会话。
    """
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    where = [Conversation.user_id == user.id]
    keyword = (q or "").strip()
    if keyword:
        where.append(contains(Conversation.title, keyword))

    total = db.scalar(select(func.count(Conversation.id)).where(*where)) or 0
    # order=asc：「对话历史」按从旧到新排；侧边栏不传，默认 desc
    order_by = Conversation.id.asc() if (order or "").lower() == "asc" else Conversation.id.desc()
    rows = db.execute(
        select(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(*where)
        .group_by(Conversation.id)
        .order_by(order_by)
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        ConversationOut(
            id=conv.id,
            title=conv.title,
            created_at=conv.created_at,
            message_count=count or 0,
        )
        for conv, count in rows
    ]
    return {"total": total, "items": items}

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
    return list(
        db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        )
    )


@router.post("/messages/{msg_id}/feedback", summary="评价回答（学生：有帮助 / 没帮助）")
def feedback_message(
    msg_id: int,
    payload: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """学生对一条助手回答点赞/点踩，作为 RAG 回答质量的反馈信号。

    只允许评价本人会话里的 assistant 消息；再次评价同一档直接幂等返回，
    切换评价（up↔down）直接覆盖；传空串（feedback=""）取消评价。
    评价数据供管理员数据概览的质量指标统计。
    """
    msg = db.get(Message, msg_id)
    if msg is None or msg.role != "assistant":
        raise HTTPException(status_code=404, detail="回答不存在")
    conv = db.get(Conversation, msg.conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="回答不存在")
    msg.feedback = payload.feedback
    db.commit()
    return {"ok": True, "feedback": payload.feedback}




# ---------- 对话管理（管理员） ----------

@router.get("/admin/conversations", summary="全部会话（管理员，分页）")
def admin_list_conversations(
    q: str = "",
    field: str = "title",
    limit: int = 10,
    offset: int = 0,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """分页返回会话列表（带提问者与消息数），支持按会话标题 / 提问者搜索。

    和学生端一样按 id 升序，前端把编号显示成 1..n。
    """
    if field not in ("title", "user"):
        raise HTTPException(status_code=400, detail="field 仅支持 title / user")
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    keyword = q.strip()
    conditions = []
    if keyword:
        if field == "user":
            conditions.append(
                or_(contains(User.username, keyword), contains(User.account, keyword))
            )
        else:
            conditions.append(contains(Conversation.title, keyword))

    total = (
        db.scalar(
            select(func.count(Conversation.id))
            .join(User, Conversation.user_id == User.id)
            .where(*conditions)
        )
        or 0
    )
    rows = db.execute(
        select(
            Conversation,
            User.account,
            User.username,
            func.count(Message.id),
        )
        .join(User, Conversation.user_id == User.id)
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(*conditions)
        .group_by(Conversation.id)
        .order_by(Conversation.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": conv.id,
                "title": conv.title,
                "account": account,
                "username": username,
                "message_count": count,
                "created_at": conv.created_at,
            }
            for conv, account, username, count in rows
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


async def _prepare_ask(
    payload: AskRequest, user: User, db: Session
) -> tuple[int, int, int | None, list[tuple[str, str]], list[str] | None]:
    """ask 的同步准备阶段：定位/创建会话、记录提问、确定课程检索范围。

    返回 (conversation_id, gen_token, turn_user_id, history, courses)。
    任何一步抛异常（会话 404、非本人 403、DB 失败）都会向上传播，由调用方负责释放限流计数。
    """
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

    gen_token = _bump_gen_seq(conversation_id)

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
        last_user_id = db.scalar(
            select(func.max(Message.id)).where(
                Message.conversation_id == conversation_id,
                Message.role == "user",
            )
        )
        # 只删「当前提问之后」的回答，避免误删上一轮的答案
        if (
            last_assistant is not None
            and last_user_id is not None
            and last_assistant.id > last_user_id
        ):
            db.delete(last_assistant)
            db.commit()
    history = _load_history(db, conversation_id, settings.HISTORY_LIMIT)
    # 重新生成时当前提问已在历史里，剔除末尾同一条，避免模型看到两遍
    if (
        payload.regenerate
        and history
        and history[-1][0] == "user"
        and history[-1][1].strip() == payload.message.strip()
    ):
        history.pop()
    if not payload.regenerate:
        db.add(Message(conversation_id=conversation_id, role="user", content=payload.message))
        db.commit()
    turn_user_id = db.scalar(
        select(func.max(Message.id)).where(
            Message.conversation_id == conversation_id,
            Message.role == "user",
        )
    )

    # 检索范围：学生只能在可见知识库内检索，管理员不限制（None）。
    # 前端传了 courses（本轮选定课程）则优先用，但学生仍需落到可见范围内，
    # 防止越权指定一个自己看不到的库。
    visible_courses = allowed_course_names(user, db)
    requested = [c for c in (payload.courses or []) if c]
    if requested:
        if visible_courses is None:
            courses = requested  # 管理员不限制
        else:
            visible_set = set(visible_courses)
            courses = [c for c in requested if c in visible_set]
            if not courses:
                # 前端传的课程一个都不可见，退回可见范围兜底（None 表示全部可见）
                courses = visible_courses or []
    else:
        courses = visible_courses

    return conversation_id, gen_token, turn_user_id, history, courses


@router.post("/ask", summary="智能问答（SSE 流式）")
async def ask(
    payload: AskRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    # 0. 每用户限流：并发 + 频率，防单点连点打满闸门与排队
    allowed, reason = _ask_allow(user.id)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    # 限流计数一旦自增，就必须在**所有**退出路径释放：事件流结束时释放，以及
    # 下面 return StreamingResponse 之前抛异常（会话 404 / 403 / DB 提交失败）也要释放，
    # 否则累计 2 次失败请求就把该用户永久锁死。用一个幂等标志，事件流 finally 与
    # 异常处理共用，保证只减一次。
    released = [False]

    def release() -> None:
        if not released[0]:
            released[0] = True
            _ask_release(user.id)

    try:
        conversation_id, gen_token, turn_user_id, history, courses = await _prepare_ask(
            payload, user, db
        )
    except Exception:
        release()
        raise

    # 3. SSE 流式生成（结束时把助手回答落库）
    async def event_gen() -> AsyncGenerator[str, None]:
        # 先发一个心跳，前端靠「收到数据」重置空闲计时，否则排队长时会被误杀
        yield _sse({"type": "ping"})
        answer_parts: list[str] = []
        sources_json: str = "[]"
        unmatched: dict | None = None
        failed = False
        completed = False
        saved = False   # 正常路径已经落过库，finally 里不再重复保存
        try:
            async for event in rag_service.stream_answer(
                payload.message,
                history,
                courses=courses,
                # 同一会话又发了一问：这一轮已被顶掉，别再占着一路模型跑完再丢
                still_wanted=lambda: _current_gen_seq(conversation_id) == gen_token,
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
            completed = not failed
            if failed:
                return
            # 记录未命中：距离门控（unmatched 事件）或模型判定未收录
            full_answer = "".join(answer_parts) or ""
            is_rejected = bool(unmatched) or is_unmatched_answer(full_answer)
            if is_rejected:
                _record_unmatched(payload.message, user.id, unmatched)

            # 正常结束：先落库拿 id，再告诉前端（评价按钮要用）
            partial = "".join(answer_parts).strip()
            if partial:
                saved_id = _save_answer(
                    conversation_id, turn_user_id, partial, sources_json, 0,
                    rejected=1 if is_rejected else 0,
                )
                if saved_id:
                    saved = True
                    yield _sse({"type": "saved", "message_id": saved_id})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"服务异常：{exc}"})
        finally:
            release()
            if _current_gen_seq(conversation_id) != gen_token:
                # 已被新请求取代，丢弃旧流的部分回答
                logger.info("会话 %s 的生成已被新请求取代，丢弃旧的部分回答", conversation_id)
            else:
                # 超时/异常/客户端断开：把半截回答存下来并标记是否中断
                if not saved:
                    partial = "".join(answer_parts).strip()
                    if partial:
                        _save_answer(
                            conversation_id,
                            turn_user_id,
                            partial,
                            sources_json,
                            0 if completed else 1,
                        )
                    else:
                        # 一个字都没生成就结束：落一句提示，避免留下没回答的提问
                        _save_answer(
                            conversation_id,
                            turn_user_id,
                            _NO_ANSWER_HINT,
                            "[]",
                            0,
                        )

    return StreamingResponse(event_gen(), media_type="text/event-stream")
