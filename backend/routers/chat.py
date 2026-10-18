"""对话路由：会话管理 + SSE 流式问答。"""
from __future__ import annotations

import itertools
import json
import logging
from bisect import bisect_right
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user, require_role
from ..models import Conversation, Message, UnansweredQuestion, User, utcnow
from ..schemas import (
    AskRequest,
    ConversationOut,
    CreateConversationRequest,
    MessageOut,
    RenameConversationRequest,
)
from ..services import rag_service
from ..services.kb_scope import allowed_course_names
from ..services.like import contains
from ..services.rag_service import is_unmatched_answer

router = APIRouter()
logger = logging.getLogger(__name__)

# 每个会话的生成序号：新请求一进来就顶掉旧序号，旧事件流收尾时不再落库。
# 注意：这是**进程内**状态，必须单 worker 跑（run.py 默认就是单进程）。
# 换成 uvicorn --workers N 后每个 worker 各有一份，"顶掉旧流"会失效，
# 表现为偶尔出现两条答案、顺序错乱——真要横向扩展就得把序号落库。
_gen_counter = itertools.count(1)

# 一个字都没生成就结束时落库的占位提示：它不是回答，不该当上下文喂给模型
_NO_ANSWER_HINT = "（这条提问没有生成出回答，可能被中断了，重新发一次试试。）"
_gen_seq: dict[int, int] = {}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _save_answer(
    conversation_id: int,
    turn_user_id: int | None,
    content: str,
    sources: str,
    interrupted: int,
) -> int | None:
    """把一轮回答落库并返回 message.id。

    顺序保护：这一轮之后已经来了新消息就不再落库，否则会话里会出现两条答案、
    顺序错乱。失败只记日志，不影响已经推给前端的流。
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
        # 没生成出回答的占位提示、以及中断时留下的半截回答都不是有效上下文，
        # 喂给模型只会让它以为“我刚才答不上来”，把下一轮也带偏
        if m.role == "assistant" and (m.interrupted or (m.content or "").startswith(_NO_ANSWER_HINT)):
            continue
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


@router.get("/unanswered/mine", summary="我的问题反馈（学生，分页）")
def my_unanswered(
    status: str = "",
    limit: int = 10,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """当前学生的反馈记录：返回 {total, pending, resolved, items}。

    以前一次最多给 50 条、前端按 10 条/页翻：第 51 条以后学生永远看不到，
    「待处理 / 已解决」两个数字也跟着从这 50 条里算，跟管理员端对不上。
    现在分页取当前页，两个计数按全量单独算。
    read=False 表示已解决但尚未查看，用于未读角标；pending 记录 read 恒为 False。
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

    where = [mine]
    if status in ("pending", "resolved"):
        where.append(UnansweredQuestion.status == status)
    total = db.scalar(select(func.count(UnansweredQuestion.id)).where(*where)) or 0
    rows = db.scalars(
        select(UnansweredQuestion)
        .where(*where)
        # 按提交时间倒序（不是 id）：演示/导入数据的 id 顺序和时间不一定一致，
        # 前端的「最近几条」靠这个顺序取
        .order_by(UnansweredQuestion.created_at.desc(), UnansweredQuestion.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "pending": pending,
        "resolved": resolved,
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

    以前是全量返回，侧边栏和「对话历史」各自拉一遍再在前端分页；会话一多，
    每次进页面都要传几百 KB。消息条数仍用一次 group by 查出来，不逐会话 count。
    kb 传了课程名时只返回用过该知识库的会话。
    """
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    where = [Conversation.user_id == user.id]
    keyword = (q or "").strip()
    if keyword:
        where.append(contains(Conversation.title, keyword))

    total = db.scalar(select(func.count(Conversation.id)).where(*where)) or 0
    # order=asc：「对话历史」按从旧到新排，编号才能是 1..n 升序；
    # 侧边栏不传，默认 desc（最近的在最上面）。
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
    _gen_seq.pop(conv_id, None)
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
                or_(contains(User.username, keyword), contains(User.real_name, keyword))
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
            User.username,
            User.real_name,
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
    _gen_seq.pop(conv_id, None)
    db.delete(conv)
    db.commit()
    return {"ok": True}


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

    gen_token = next(_gen_counter)
    _gen_seq[conversation_id] = gen_token

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
        # 只删「当前提问之后」的回答。上次生成一个字都没吐出来时，最近一条
        # 助手消息属于上一轮，删掉会把上一轮的答案也弄丢。
        if (
            last_assistant is not None
            and last_user_id is not None
            and last_assistant.id > last_user_id
        ):
            db.delete(last_assistant)
            db.commit()
    history = _load_history(db, conversation_id, settings.HISTORY_LIMIT)
    # 重新生成时当前提问已经作为最后一条用户消息存在历史里，stream_answer 还会
    # 再拼一次 question；不剔掉的话模型会看到同一个问题两遍。
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

    # 检索范围：学生只能在自己可见的知识库里检索，管理员不限制（None）。
    # 不做这一步的话，"列表里看不到的知识库"照样能被提问捞出来。
    #
    # 以前这里还有一段按 payload.knowledge_bases 校验的 403 分支，但校验写在
    # 「保存用户提问」之后：越权请求会先落一条提问、再返回 403，会话里就多出
    # 一条永远没有回答的提问（下次打开还会自动补生成）。前端从来没有发过这个
    # 参数，这套逻辑等于白留，索性连 403 一起删掉，顺序问题也就不存在了。
    courses = allowed_course_names(user, db)

    # 3. SSE 流式生成（结束时把助手回答落库）
    async def event_gen() -> AsyncGenerator[str, None]:
        # 先发一个心跳：模型首个 token 可能要几十秒，前端按“收到数据”重置空闲计时，
        # 没有这个事件会被空闲超时误杀（结果就是会话里只剩提问、没有回答）。
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
                still_wanted=lambda: _gen_seq.get(conversation_id) == gen_token,
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
                    # 同一学生问同一个问题只留一条：已解决的不再重复建待处理，
                    # 否则管理员列表里会堆出同一问题的多张工单。
                    question_text = payload.message.strip()
                    existing = session.scalar(
                        select(UnansweredQuestion)
                        .where(
                            UnansweredQuestion.question == question_text,
                            UnansweredQuestion.created_by == user.id,
                        )
                        .order_by(UnansweredQuestion.id.desc())
                        .limit(1)
                    )
                    if existing is not None:
                        if existing.status == "pending":
                            existing.course = course
                            existing.related_points = related
                    else:
                        session.add(
                            UnansweredQuestion(
                                display_no=_next_unanswered_no(session),
                                question=question_text,
                                course=course,
                                related_points=related,
                                status="pending",
                                created_by=user.id,
                            )
                        )
                    session.commit()
                finally:
                    session.close()

            # 正常结束：先落库拿到 id，再把 id 告诉前端（评价按钮要用它）
            partial = "".join(answer_parts).strip()
            if partial:
                saved_id = _save_answer(
                    conversation_id, turn_user_id, partial, sources_json, 0
                )
                if saved_id:
                    saved = True
                    yield _sse({"type": "saved", "message_id": saved_id})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"服务异常：{exc}"})
        finally:
            if _gen_seq.get(conversation_id) != gen_token:
                # 已经有更新的提问/重生成顶掉了这一轮，旧流不能落库，
                # 否则会出现两条答案、顺序错乱
                logger.info("会话 %s 的生成已被新请求取代，丢弃旧的部分回答", conversation_id)
            else:
                _gen_seq.pop(conversation_id, None)

                # 超时、异常、客户端断开（切换会话/停止生成）走到这里：
                # 把已经生成的半截回答存下来，并标记是否中断。
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
                        # 一个字都没生成就结束（用户点停止 / 断网 / 模型报错）：
                        # 不落任何东西的话，会话里会留下一条永远没有回答的提问。
                        # 这里落一句提示（不标记 interrupted，免得重新打开时又自动生成一遍），
                        # 让用户看得出来这一问没答上、需要重发。
                        _save_answer(
                            conversation_id,
                            turn_user_id,
                            _NO_ANSWER_HINT,
                            "[]",
                            0,
                        )

    return StreamingResponse(event_gen(), media_type="text/event-stream")
