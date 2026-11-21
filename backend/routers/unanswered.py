"""未命中问题路由：管理员查看 / 重新生成回答 / 统计 / 标记 / 删除知识库未覆盖的问题记录。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, joinedload

from ..config import settings
from ..database import get_db
from ..deps import require_role
from ..models import (
    Conversation,
    Message,
    QuestionVector,
    UnansweredQuestion,
    User,
    utcnow,
)
from ..services.embedding_service import get_embeddings
from ..services.notify import notify

router = APIRouter()
logger = logging.getLogger(__name__)


def _to_dict(q: UnansweredQuestion) -> dict:
    related = []
    try:
        related = json.loads(q.related_points or "[]")
    except Exception:
        pass
    return {
        "id": q.id,
        "no": q.id,
        "question": q.question,
        "course": q.course or "",
        "status": q.status,
        "related_points": related if isinstance(related, list) else [],
        "answer": q.answer or "",
        "answered_at": q.answered_at,
        "created_by": q.created_by,
        "creator_name": q.creator_name,
        "created_at": q.created_at,
        "admin_read": q.admin_read_at is not None,
        # 闭环用：原会话 / 消息定位，没有历史记录时为空
        "conversation_id": q.conversation_id,
        "message_id": q.message_id,
    }


@router.get("", summary="未命中问题列表（管理员）")
def list_unanswered(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """按 status 区分排序返回记录；status 可选过滤 pending / resolved。

    排序规则：处理中按提问时间从旧到新（最早待处理的排最前），已解决按解决
    时间从新到旧（最新解决的排最前），全部按提问时间从新到旧。

    total 必须单独 count：之前直接返回本页条数（最多 500），
    前端拿它当总数展示就会少算。creator 用 joinedload 一次查出来，
    避免逐行懒加载用户表。
    """
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    if status == "pending":
        order = UnansweredQuestion.created_at.asc()
    elif status == "resolved":
        # 按解决时间倒序；历史脏数据可能 answered_at 为空，落到末尾
        order = UnansweredQuestion.answered_at.desc().nullslast()
    else:
        order = UnansweredQuestion.created_at.desc()
    query = (
        select(UnansweredQuestion)
        .options(joinedload(UnansweredQuestion.creator))
        .order_by(order)
    )
    count_query = select(func.count(UnansweredQuestion.id))
    if status in ("pending", "resolved"):
        query = query.where(UnansweredQuestion.status == status)
        count_query = count_query.where(UnansweredQuestion.status == status)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.limit(limit).offset(offset)).scalars().all()
    # 管理员角标未读数：待处理（pending）的总数，与 status 过滤无关、恒按全量算，
    # 供顶部铃铛角标使用。生成回答解决掉一条（pending→resolved）即减一。
    admin_unread = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(
                UnansweredQuestion.status == "pending",
            )
        )
        or 0
    )
    return {"items": [_to_dict(q) for q in rows], "total": total, "unread": admin_unread}


def _bigrams(text: str) -> set[str]:
    """问句的字符二元组：中文不用分词，向量模型不可用时靠这个兜底。"""
    s = re.sub(r"[\s，。？！、；：,.?!;:\"'“”‘’()（）\[\]【】<>《》/\\]+", "", text or "")
    return {s[i : i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _question_vectors(
    db: Session, texts: list[str], *, embed_missing: bool
) -> tuple[dict[str, list[float]], bool, datetime | None]:
    """读问题向量缓存；embed_missing=True 时才补齐缺失项并写缓存。

    返回 ``(vectors, degraded, cached_at)``。``degraded=True`` 表示需要现算时
    嵌入模型失败；``cached_at`` 是本次命中缓存里最新的写入时间。
    GET 聚类传 False，保证纯读、不 embed、不 commit。
    """
    unique = list(dict.fromkeys(t.strip() for t in texts if t and t.strip()))
    if not unique:
        return {}, False, None
    hashes = {t: hashlib.sha1(t.encode("utf-8")).hexdigest() for t in unique}
    cached: dict[str, list[float]] = {}
    cached_at: datetime | None = None
    for text_hash, raw, created_at in db.execute(
        select(
            QuestionVector.text_hash,
            QuestionVector.vector,
            QuestionVector.created_at,
        ).where(QuestionVector.text_hash.in_(list(hashes.values())))
    ).all():
        try:
            cached[text_hash] = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if created_at is not None and (cached_at is None or created_at > cached_at):
            cached_at = created_at
    out = {t: cached[h] for t, h in hashes.items() if h in cached}
    missing = [t for t in unique if t not in out]
    if not missing or not embed_missing:
        return out, False, cached_at
    try:
        vectors = get_embeddings().embed_documents(missing)
    except Exception:  # noqa: BLE001
        logger.warning("聚类：问题向量化失败，降级为空聚类")
        return out, True, cached_at
    for text, vec in zip(missing, vectors):
        vector = [float(x) for x in vec]
        out[text] = vector
        # upsert：两个管理员同时点「重新聚类」会同时算同一批问题，
        # 直接 add 会撞 text_hash 唯一索引
        db.execute(
            sqlite_insert(QuestionVector)
            .values(text_hash=hashes[text], text=text, vector=json.dumps(vector))
            .on_conflict_do_nothing(index_elements=["text_hash"])
        )
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("聚类：问题向量缓存写入失败（不影响本次结果）")
    return out, False, utcnow()


def _group_rows(rows: list, same) -> list[dict]:
    """贪心聚类：和组内代表相似度达标就并进去。"""
    groups: list[dict] = []
    for q in rows:
        for g in groups:
            if same(g, q):
                g["items"].append(q)
                break
        else:
            groups.append({"items": [q]})
    return groups


def _clusters_payload(
    db: Session,
    status: str | None,
    threshold: float | None,
    limit: int,
    *,
    embed_missing: bool,
) -> dict:
    """聚类主体：GET 只读、POST 补齐向量缓存，两者返回同一结构。"""
    limit = min(max(limit, 1), 500)
    stmt = select(UnansweredQuestion).options(joinedload(UnansweredQuestion.creator))
    if status in ("pending", "resolved"):
        stmt = stmt.where(UnansweredQuestion.status == status)
    rows = list(db.scalars(stmt.order_by(UnansweredQuestion.id.desc()).limit(limit)))

    if not rows:
        return {
            "clusters": [],
            "items": [],
            "total_questions": 0,
            "group_count": 0,
            "method": "none",
            "generated_at": utcnow() if embed_missing else None,
        }

    vectors, degraded, cached_at = _question_vectors(
        db, [q.question or "" for q in rows], embed_missing=embed_missing
    )
    if degraded:
        # 嵌入模型不可用：按契约返回空聚类 + 提示，让前端明确知道「没算成」
        return {
            "clusters": [],
            "items": [],
            "total_questions": len(rows),
            "group_count": 0,
            "method": "none",
            "generated_at": utcnow(),
            "degraded": True,
            "message": "向量模型暂时不可用，聚类已降级为空结果，请稍后重试或检查模型服务",
        }

    if vectors:
        method = "vector"
        cut = 0.78 if threshold is None else min(max(threshold, 0.5), 0.98)

        def same(g: dict, q) -> bool:
            a = vectors.get((g["items"][0].question or "").strip())
            b = vectors.get((q.question or "").strip())
            return a is not None and b is not None and _cosine(a, b) >= cut

    else:
        method = "bigram"
        cut = 0.55 if threshold is None else min(max(threshold, 0.2), 0.95)

        def same(g: dict, q) -> bool:
            ga, gb = _bigrams(g["items"][0].question), _bigrams(q.question)
            union = len(ga | gb)
            return bool(union) and len(ga & gb) / union >= cut

    groups = _group_rows(rows, same)

    out: list[dict] = []
    for g in groups:
        items = g["items"]
        out.append(
            {
                "count": len(items),
                "question": items[0].question,
                "course": next((x.course for x in items if x.course), ""),
                "status": items[0].status,
                "ids": [x.id for x in items],
                "variants": [x.question for x in items[:8]],
                "creators": sorted({x.creator_name for x in items if x.creator_name}),
                "latest_at": max(
                    (x.created_at for x in items if x.created_at), default=None
                ),
            }
        )
    # 高频问题靠前：先按人数从多到少，人数相同再按时间从新到旧
    out.sort(
        key=lambda x: (
            -x["count"],
            -(x["latest_at"].timestamp() if x["latest_at"] else 0),
        )
    )
    return {
        # clusters 是契约字段；items 保留给现有管理端（admin-feedback.js）
        "clusters": out,
        "items": out,
        "total_questions": len(rows),
        "group_count": len(out),
        "method": method,
        "generated_at": utcnow() if embed_missing else cached_at,
    }


@router.get("/clusters", summary="未命中问题聚类（管理员，只读缓存）")
def cluster_unanswered(
    status: str | None = "pending",
    threshold: float | None = None,
    limit: int = 200,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """把问法不同、意思相近的问题并成一组，按组内人数从多到少排。

    只读 question_vectors 缓存：不现场 embed、不写库、不 commit。
    缓存缺失的问题各自成组；一条缓存都没有时退回字符二元组 Jaccard。
    需要「把缺失向量补齐并写缓存」请调用 POST /api/admin/unanswered/clusters。
    """
    return _clusters_payload(db, status, threshold, limit, embed_missing=False)


@router.post("/clusters", summary="未命中问题聚类（管理员，补齐向量缓存）")
def compute_clusters(
    status: str | None = "pending",
    threshold: float | None = None,
    limit: int = 200,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """计算聚类并写入缺失的问题向量缓存；请求体可为空。

    返回结构与 GET 一致；嵌入模型失败时返回空聚类并带 ``degraded: true``。
    """
    return _clusters_payload(db, status, threshold, limit, embed_missing=True)


@router.get("/stats", summary="未命中问题统计（管理员）")
def unanswered_stats(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """总数 / 待补充 / 已解决 / 今日新增 / 按课程分布。"""
    total = db.scalar(select(func.count(UnansweredQuestion.id))) or 0
    pending = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(
                UnansweredQuestion.status == "pending"
            )
        )
        or 0
    )
    resolved = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(
                UnansweredQuestion.status == "resolved"
            )
        )
        or 0
    )
    today_local = datetime.now(timezone.utc) + timedelta(hours=settings.UTC_OFFSET_HOURS)
    start_local = today_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local - timedelta(hours=settings.UTC_OFFSET_HOURS)
    today = (
        db.scalar(
            select(func.count(UnansweredQuestion.id)).where(
                UnansweredQuestion.created_at >= start_utc
            )
        )
        or 0
    )
    by_course_rows = db.execute(
        select(UnansweredQuestion.course, func.count(UnansweredQuestion.id))
        .where(UnansweredQuestion.course != "")
        .group_by(UnansweredQuestion.course)
        .order_by(func.count(UnansweredQuestion.id).desc())
        .limit(10)
    ).all()
    by_course = [{"course": c, "count": n} for c, n in by_course_rows]
    return {
        "total": total,
        "pending": pending,
        "resolved": resolved,
        "today": today,
        "by_course": by_course,
    }


def _collect_sources(question: str, courses) -> list[dict]:
    """重新检索该问题的引用并转成前端 sources 结构；失败只记日志。

    走 rag_service 的检索/格式化，保持和正常聊天回答的 sources 一致。
    """
    try:
        from ..services import rag_service

        docs = rag_service.vector_search(question, settings.TOP_K, courses)
        return rag_service._format_sources(docs)
    except Exception:  # noqa: BLE001
        logger.warning("重新生成：收集引用失败，写回消息不带 sources", exc_info=True)
        return []


@router.post("/{qid}/regenerate", summary="补充资料后重新生成回答（管理员）")
async def regenerate_answer(
    qid: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """管理员补充资料后，对未命中问题重新生成回答并通知学生（学生端可查看）。

    生成结果仍判定未收录时保持 pending，提示继续补充；否则置为 resolved。
    命中时把新回答作为 assistant 消息写回原会话（有 conversation_id 时），
    返回 message_id / written_back，闭环到学生端对话里。
    """
    q = db.get(UnansweredQuestion, qid)
    if q is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    from ..services.kb_scope import allowed_course_names
    from ..services.rag_service import generate_answer, model_resource_error_message

    # 按提问学生的可见范围重新检索，避免跨到别人的私有知识库
    scope = allowed_course_names(q.creator, db) if q.creator is not None else None
    result = await generate_answer(q.question, courses=scope)
    if result.get("error"):
        # 生成失败（排队没排上 / 超时 / 空回答）不能写成“已解决”，也不覆盖之前
        # 可用的答案。三种原因要分开说，否则「没排上队」会被讲成「模型坏了」。
        message = {
            "busy": "后台正忙（同时最多 3 路生成），这次没排上队，稍后再点一次",
            "timeout": "生成超时了，这次没有保存回答，稍后再试",
            "answerability": (
                "资料可答性判定超时或模型暂时不可用，这不表示知识库未收录，请稍后重试"
            ),
            "resource": model_resource_error_message(),
            "generation": "回答生成失败，模型服务暂时不可用，请稍后重试",
        }.get(result["error"], "这次没生成出有效回答，检查模型服务后再试")
        return {
            "ok": False,
            "answer": "",
            "resolved": False,
            "message": message,
            "message_id": None,
            "written_back": False,
        }
    message_id: int | None = None
    written_back = False
    if result["recommended"]:
        q.related_points = json.dumps(result["recommended"], ensure_ascii=False)
    if result["matched"]:
        # 知识库已能命中该问题：写入新回答并置为已解决
        q.answer = result["answer"]
        q.status = "resolved"
        q.answered_at = utcnow()
        # 生成回答即代表这条已处理完，管理员端铃铛角标应随之减一；
        # 若未置 admin_read_at，生成后角标仍会把它算作待处理未读。
        q.admin_read_at = q.admin_read_at or utcnow()
        message = "回答已生成，学生那边能看到了"
        answer = result["answer"]
        # 闭环：把新回答写回学生原来的会话，学生端对话里直接能看到。
        # 老记录可能没有 conversation_id（字段是后加的），此时跳过写回。
        if (
            q.conversation_id is not None
            and db.get(Conversation, q.conversation_id) is not None
        ):
            sources = await asyncio.to_thread(_collect_sources, q.question, scope)
            msg = Message(
                conversation_id=q.conversation_id,
                role="assistant",
                content=result["answer"],
                sources=json.dumps(sources, ensure_ascii=False),
                outcome="answered",
            )
            db.add(msg)
            db.flush()
            q.message_id = msg.id
            message_id = msg.id
            written_back = True
    else:
        # 知识库仍未收录：清空旧回答并**退回待处理**。
        # 只清 answer 不重置状态的话，一条「已解决」的记录重新生成失败后会变成
        # 「已解决 + 空答案」，学生端点开是空白，管理员之前写好的答案也没了。
        q.answer = ""
        q.status = "pending"
        q.answered_at = None
        message = "知识库还没有收录相关内容，请先添加资料（上传文档或添加知识点）后再生成"
        answer = ""
    # 回答发生了变化：重新计为未读，等学生自己点开再清角标
    q.read_at = None
    # commit 之后 ORM 对象会过期，先把发通知要用的字段取出来
    student_id = q.created_by
    question_text = q.question
    db.commit()

    # 站内通知：补答成功才发，让学生不用自己反复刷「答疑进度」页。
    # notify 内部独立事务 + 异常只记日志，通知写失败不影响本次补答结果。
    if result["matched"]:
        notify(
            db,
            student_id,
            "answer_ready",
            "你的提问已补答",
            f"你之前没得到回答的问题「{question_text}」已由管理员补答，点击查看",
            "#/feedback",
        )

    return {
        "ok": True,
        "answer": answer,
        "resolved": result["matched"],
        "message": message,
        "message_id": message_id,
        "written_back": written_back,
    }


@router.patch("/{qid}", summary="标记未命中问题状态（管理员）")
def update_unanswered(
    qid: int,
    status: str,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """status 取值 pending（待补充）/ resolved（已补充）。"""
    q = db.get(UnansweredQuestion, qid)
    if q is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if status not in ("pending", "resolved"):
        raise HTTPException(status_code=400, detail="status 仅支持 pending / resolved")
    # 手工置为「已解决」时必须已有答案：否则学生端点开是空白，还因为 read_at=None
    # 冒出红点。regenerate_answer 里已经为这个场景写过保护（未收录时退回 pending），
    # 这里补上同一规则，避免 UI 手点绕过。
    if status == "resolved" and not (q.answer or "").strip():
        raise HTTPException(
            status_code=400,
            detail="这条反馈还没有生成回答，不能直接标记为已解决（请先补充资料并重新生成）",
        )
    if q.status != status:
        # 状态变了旧答案就不再作数，重置已读让新结果重新提醒
        q.read_at = None
    became_resolved = q.status != status and status == "resolved"
    student_id = q.created_by
    question_text = q.question
    q.status = status
    db.commit()
    # 管理员把反馈置为已解决 = 给了学生一个结果，发一条站内通知提醒他回来看
    if became_resolved:
        notify(
            db,
            student_id,
            "feedback_reply",
            "你的反馈有新回复",
            f"你提交的问题「{question_text}」已被管理员标记为已解决，点击查看",
            "#/feedback",
        )
    return {"ok": True}


@router.delete("/{qid}", summary="删除未命中问题记录（管理员）")
def delete_unanswered(
    qid: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    q = db.get(UnansweredQuestion, qid)
    if q is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(q)
    db.commit()
    return {"ok": True}
