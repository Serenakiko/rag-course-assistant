"""未命中问题路由：管理员查看 / 重新生成回答 / 统计 / 标记 / 删除知识库未覆盖的问题记录。"""
from __future__ import annotations

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
from ..models import QuestionVector, UnansweredQuestion, User
from ..services.embedding_service import get_embeddings

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
        "no": q.display_no or q.id,
        "question": q.question,
        "course": q.course or "",
        "status": q.status,
        "related_points": related if isinstance(related, list) else [],
        "answer": q.answer or "",
        "answered_at": q.answered_at,
        "created_by": q.created_by,
        "creator_name": q.creator_name,
        "created_at": q.created_at,
    }


@router.get("", summary="未命中问题列表（管理员）")
def list_unanswered(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """按 id 升序（1→n）返回记录；status 可选过滤 pending / resolved。

    total 必须单独 count：之前直接返回本页条数（最多 500），
    前端拿它当总数展示就会少算。creator 用 joinedload 一次查出来，
    避免逐行懒加载用户表。
    """
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    query = (
        select(UnansweredQuestion)
        .options(joinedload(UnansweredQuestion.creator))
        .order_by(UnansweredQuestion.id.asc())
    )
    count_query = select(func.count(UnansweredQuestion.id))
    if status in ("pending", "resolved"):
        query = query.where(UnansweredQuestion.status == status)
        count_query = count_query.where(UnansweredQuestion.status == status)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.limit(limit).offset(offset)).scalars().all()
    return {"items": [_to_dict(q) for q in rows], "total": total}


def _bigrams(text: str) -> set[str]:
    """问句的字符二元组：中文不用分词，向量模型不可用时靠这个兜底。"""
    s = re.sub(r"[\s，。？！、；：,.?!;:\"'“”‘’()（）\[\]【】<>《》/\\]+", "", text or "")
    return {s[i : i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _question_vectors(db: Session, texts: list[str]) -> dict[str, list[float]]:
    """问题向量：先查缓存，缺的现算并入库；模型不可用返回空，调用方退回字符相似。"""
    unique = list(dict.fromkeys(t.strip() for t in texts if t and t.strip()))
    if not unique:
        return {}
    hashes = {t: hashlib.sha1(t.encode("utf-8")).hexdigest() for t in unique}
    cached = {
        row.text_hash: json.loads(row.vector)
        for row in db.scalars(
            select(QuestionVector).where(QuestionVector.text_hash.in_(list(hashes.values())))
        )
    }
    out = {t: cached[h] for t, h in hashes.items() if h in cached}
    missing = [t for t in unique if t not in out]
    if not missing:
        return out
    try:
        vectors = get_embeddings().embed_documents(missing)
    except Exception:  # noqa: BLE001
        logger.warning("聚类：问题向量化失败，退回字符相似度")
        return {}
    for text, vec in zip(missing, vectors):
        vector = [float(x) for x in vec]
        out[text] = vector
        # upsert：聚类是个 GET，两个标签页同时打开时会同时算同一批问题，
        # 直接 add 会撞 text_hash 唯一索引（有 except 兜着，但那次向量就白算了）
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
    return out


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


@router.get("/clusters", summary="未命中问题聚类（管理员）")
def cluster_unanswered(
    status: str | None = "pending",
    threshold: float | None = None,
    limit: int = 200,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """把问法不同、意思相近的问题并成一组，按组内人数从多到少排。

    优先用向量相似度（bge-m3 余弦，默认 0.78），能合并「红黑树怎么插入」
    和「红黑树的插入过程」这类换说法；向量模型不可用时退回字符二元组
    Jaccard（默认 0.55）。问题向量带缓存表，同一句话只算一次。
    """
    limit = min(max(limit, 1), 500)
    stmt = select(UnansweredQuestion).options(joinedload(UnansweredQuestion.creator))
    if status in ("pending", "resolved"):
        stmt = stmt.where(UnansweredQuestion.status == status)
    rows = list(db.scalars(stmt.order_by(UnansweredQuestion.id.desc()).limit(limit)))

    if not rows:
        return {"total_questions": 0, "group_count": 0, "items": [], "method": "none"}

    vectors = _question_vectors(db, [q.question or "" for q in rows])
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
    out.sort(
        key=lambda x: (
            -x["count"],
            -(x["latest_at"].timestamp() if x["latest_at"] else 0),
        )
    )
    return {
        "total_questions": len(rows),
        "group_count": len(out),
        "items": out,
        "method": method,
    }


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


@router.post("/{qid}/regenerate", summary="补充资料后重新生成回答（管理员）")
async def regenerate_answer(
    qid: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """管理员补充资料后，对未命中问题重新生成回答并通知学生（学生端可查看）。

    生成结果仍判定未收录时保持 pending，提示继续补充；否则置为 resolved。
    """
    q = db.get(UnansweredQuestion, qid)
    if q is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    from ..models import utcnow
    from ..services.kb_scope import allowed_course_names
    from ..services.rag_service import generate_answer, is_unmatched_answer

    # 按提问学生当时的可见范围重新检索。不限定范围的话，管理员一按「生成回答」，
    # 检索会跨到别人的私有知识库，把那里的内容写进这个学生的反馈里。
    scope = allowed_course_names(q.creator, db) if q.creator is not None else None
    result = await generate_answer(q.question, courses=scope)
    if result.get("error"):
        # 生成失败（排队没排上 / 超时 / 空回答）不能写成“已解决”，也不覆盖之前
        # 可用的答案。三种原因要分开说，否则「没排上队」会被讲成「模型坏了」。
        message = {
            "busy": "后台正忙（同时最多 3 路生成），这次没排上队，稍后再点一次",
            "timeout": "生成超时了，这次没有保存回答，稍后再试",
        }.get(result["error"], "这次没生成出有效回答，检查模型服务后再试")
        return {"ok": False, "answer": "", "resolved": False, "message": message}
    if result["recommended"]:
        q.related_points = json.dumps(result["recommended"], ensure_ascii=False)
    if result["matched"]:
        # 知识库已能命中该问题：写入新回答并置为已解决
        q.answer = result["answer"]
        q.status = "resolved"
        q.answered_at = utcnow()
        message = "回答已生成，学生那边能看到了"
        answer = result["answer"]
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
    db.commit()

    return {
        "ok": True,
        "answer": answer,
        "resolved": result["matched"],
        "message": message,
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
    if q.status != status:
        # 状态变了旧答案就不再作数，重置已读让新结果重新提醒
        q.read_at = None
    q.status = status
    db.commit()
    return {"ok": True}


def _renumber_unanswered(db: Session) -> None:
    """删除后把展示编号重排成 1..n，主键 id 一律不动。

    以前是直接改主键：删掉一条之后其余记录的 id 全部前移，前端或脚本手里的
    旧编号就指向了别人，删起来会打偏（之前就是这么误删过反馈记录）。
    """
    rows = db.scalars(
        select(UnansweredQuestion).order_by(UnansweredQuestion.id.asc())
    ).all()
    for i, r in enumerate(rows, start=1):
        if r.display_no != i:
            r.display_no = i
    db.flush()


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
    db.flush()
    _renumber_unanswered(db)
    db.commit()
    return {"ok": True}
