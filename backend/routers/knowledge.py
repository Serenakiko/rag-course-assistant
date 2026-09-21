"""知识点 / 知识库路由：浏览（所有登录用户）+ 维护（创建者本人或管理员）。

权限规则：
  - 新增知识点、新增知识库：所有登录用户
  - 编辑知识点：所有登录用户（方便纠错）
  - 删除知识点 / 编辑或删除知识库：创建者本人或管理员
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Document, KnowledgeBase, KnowledgePoint, User
from ..schemas import (
    KnowledgeBaseIn,
    KnowledgeBaseOut,
    KnowledgeBaseSummary,
    KnowledgePointIn,
    KnowledgePointOut,
)
from ..services.document_parser import parse_file
from ..services.vector_store import (
    add_knowledge_point,
    delete_document,
    delete_knowledge_point,
    update_knowledge_point,
)

router = APIRouter()


def can_manage(user: User, creator_id: int | None) -> bool:
    """是否可管理某条记录：管理员，或记录创建者本人。"""
    if user.role == "admin":
        return True
    return creator_id is not None and creator_id == user.id


# ==================== 知识库 ====================

@router.get("/bases", response_model=list[KnowledgeBaseSummary], summary="知识库列表（含统计）")
def list_bases(
    keyword: str = Query(default="", max_length=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KnowledgeBaseSummary]:
    stmt = select(KnowledgeBase)
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(KnowledgeBase.name.like(like), KnowledgeBase.description.like(like))
        )
    stmt = stmt.order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id.desc())
    bases = list(db.scalars(stmt))

    # 一次查出各知识库的知识点数量
    counts = dict(
        db.execute(
            select(KnowledgePoint.course, func.count(KnowledgePoint.id))
            .group_by(KnowledgePoint.course)
        ).all()
    )
    result: list[KnowledgeBaseSummary] = []
    for b in bases:
        summary = KnowledgeBaseSummary.model_validate(b)
        summary.point_count = counts.get(b.name, 0)
        summary.creator_name = (
            b.creator.real_name or b.creator.username if b.creator else ""
        )
        result.append(summary)
    return result


@router.post("/bases", response_model=KnowledgeBaseOut, summary="新增知识库")
def create_base(
    payload: KnowledgeBaseIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeBase:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    exists = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
    if exists is not None:
        raise HTTPException(status_code=409, detail="已存在同名知识库")
    base = KnowledgeBase(
        name=name,
        description=payload.description.strip(),
        created_by=user.id,
    )
    db.add(base)
    db.commit()
    db.refresh(base)
    return base


@router.put("/bases/{base_id}", response_model=KnowledgeBaseOut, summary="更新知识库")
def update_base(
    base_id: int,
    payload: KnowledgeBaseIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeBase:
    base = db.get(KnowledgeBase, base_id)
    if base is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if not can_manage(user, base.created_by):
        raise HTTPException(status_code=403, detail="只能编辑自己创建的知识库")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    if name != base.name:
        dup = db.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.name == name, KnowledgeBase.id != base.id
            )
        )
        if dup is not None:
            raise HTTPException(status_code=409, detail="已存在同名知识库")
        # 同步更新该知识库下知识点的课程字段
        db.execute(
            KnowledgePoint.__table__.update()
            .where(KnowledgePoint.course == base.name)
            .values(course=name)
        )
    base.name = name
    base.description = payload.description.strip()
    db.commit()
    db.refresh(base)
    return base


@router.delete("/bases/{base_id}", summary="删除知识库（级联删除其下知识点）")
def remove_base(
    base_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    base = db.get(KnowledgeBase, base_id)
    if base is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if not can_manage(user, base.created_by):
        raise HTTPException(status_code=403, detail="只能删除自己创建的知识库")
    # 先删向量，再删记录
    points = list(
        db.scalars(select(KnowledgePoint).where(KnowledgePoint.course == base.name))
    )
    for p in points:
        delete_knowledge_point(p.id)
    db.execute(
        KnowledgePoint.__table__.delete().where(KnowledgePoint.course == base.name)
    )
    db.delete(base)
    db.commit()
    return {"ok": True, "removed_points": len(points)}


# ==================== 知识点 ====================

@router.get("/points", response_model=list[KnowledgePointOut], summary="知识点列表")
def list_points(
    keyword: str = Query(default="", max_length=100),
    course: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KnowledgePoint]:
    stmt = select(KnowledgePoint)
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                KnowledgePoint.title.like(like),
                KnowledgePoint.content.like(like),
                KnowledgePoint.tags.like(like),
            )
        )
    if course.strip():
        stmt = stmt.where(KnowledgePoint.course == course.strip())
    stmt = stmt.order_by(KnowledgePoint.updated_at.desc(), KnowledgePoint.id.desc())
    return list(db.scalars(stmt))


@router.get("/courses", summary="课程（知识库）名称列表")
def list_courses(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[str]:
    base_names = list(db.scalars(select(KnowledgeBase.name).order_by(KnowledgeBase.name)))
    # 兼容历史数据：旧知识点课程不在知识库表中时也返回
    old_courses = db.execute(
        select(KnowledgePoint.course).distinct().order_by(KnowledgePoint.course)
    ).all()
    for (c,) in old_courses:
        if c and c not in base_names:
            base_names.append(c)
    return base_names


@router.post("/points", response_model=KnowledgePointOut, summary="新增知识点")
def create_point(
    payload: KnowledgePointIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgePoint:
    point = KnowledgePoint(
        title=payload.title.strip(),
        content=payload.content.strip(),
        course=payload.course.strip(),
        tags=payload.tags.strip(),
        created_by=user.id,
    )
    db.add(point)
    db.commit()
    db.refresh(point)
    try:
        add_knowledge_point(point.id, point.title, point.content, point.course, point.tags)
    except Exception:  # noqa: BLE001
        db.delete(point)
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="知识点写入向量库失败，请确认大模型服务已启动并拉取了向量模型",
        )
    return point


@router.put("/points/{point_id}", response_model=KnowledgePointOut, summary="更新知识点")
def update_point(
    point_id: int,
    payload: KnowledgePointIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgePoint:
    # 所有登录用户都可编辑知识点，便于修正内容错误
    point = db.get(KnowledgePoint, point_id)
    if point is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    point.title = payload.title.strip()
    point.content = payload.content.strip()
    point.course = payload.course.strip()
    point.tags = payload.tags.strip()
    db.commit()
    db.refresh(point)
    try:
        update_knowledge_point(point.id, point.title, point.content, point.course, point.tags)
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail="向量库更新失败，请确认大模型服务已启动",
        )
    return point


@router.delete("/points/{point_id}", summary="删除知识点（若为文档自动生成的知识点，联动删除对应文档）")
def remove_point(
    point_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    point = db.get(KnowledgePoint, point_id)
    if point is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    if not can_manage(user, point.created_by):
        raise HTTPException(status_code=403, detail="只能删除自己创建的知识点")
    # 联动：文档自动生成的知识点被删除时，一并删除对应文档
    # （原始文件 + 文档分块向量 + 文档记录），避免学生端删除后管理员端仍残留文档
    removed_doc = None
    auto_doc = _find_auto_doc(db, point)
    if auto_doc is not None:
        removed_doc = auto_doc.filename
        if auto_doc.stored_path:
            Path(auto_doc.stored_path).unlink(missing_ok=True)
        delete_document(auto_doc.id)
        db.delete(auto_doc)
    db.delete(point)
    db.commit()
    delete_knowledge_point(point_id)
    return {"ok": True, "removed_doc": removed_doc}


def _find_auto_doc(db: Session, point: KnowledgePoint) -> Document | None:
    """查找与知识点对应的自动生成文档。

    匹配条件：标题 = 文档名去扩展名、同课程、同上传者，且知识点内容与文档正文前 2000 字一致
    （文档上传时自动生成的知识点内容即正文前 2000 字）。内容不一致视为手工/已编辑知识点，不联动。
    """
    docs = db.scalars(
        select(Document).where(
            Document.course == (point.course or ""),
            Document.uploaded_by == point.created_by,
        )
    ).all()
    for d in docs:
        if Path(d.filename).stem != point.title:
            continue
        try:
            text = parse_file(Path(d.stored_path))
        except Exception:  # noqa: BLE001  文件缺失/解析失败按空处理
            text = ""
        if not text or text[:2000] == (point.content or ""):
            return d
    return None


@router.get("/stats/count", summary="知识点总数")
def point_count(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    count = db.scalar(select(func.count(KnowledgePoint.id))) or 0
    return {"count": count}
