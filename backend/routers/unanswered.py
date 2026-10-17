"""未命中问题路由：管理员查看 / 重新生成回答 / 统计 / 标记 / 删除知识库未覆盖的问题记录。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_role
from ..models import UnansweredQuestion, User

router = APIRouter()


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
    limit: int = 200,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """按 id 升序（1→n）返回记录；status 可选过滤 pending / resolved。"""
    limit = min(max(limit, 1), 500)
    query = select(UnansweredQuestion).order_by(UnansweredQuestion.id.asc()).limit(limit)
    if status in ("pending", "resolved"):
        query = query.where(UnansweredQuestion.status == status)
    rows = db.execute(query).scalars().all()
    return {"items": [_to_dict(q) for q in rows], "total": len(rows)}


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
    today_local = datetime.now(timezone.utc) + timedelta(hours=8)
    start_local = today_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local - timedelta(hours=8)
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
        # 知识库仍未收录：清空旧回答并保持待处理，提示先补充资料
        q.answer = ""
        message = "知识库还没有收录相关内容，请先添加资料（上传文档或添加知识点）后再生成"
        answer = ""
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
