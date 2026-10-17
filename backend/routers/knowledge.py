"""知识点 / 知识库路由：浏览（按可见性过滤）+ 维护（创建者本人或管理员）。

可见性规则：
  - 管理员：能看到所有人的知识库
  - 学生：只看到管理员发布的知识库 + 自己创建的

权限规则：
  - 新增知识点、新增知识库：所有登录用户
  - 编辑 / 删除知识点、编辑或删除知识库：创建者本人或管理员
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

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
from ..services.chunker import split_text
from ..services.document_parser import parse_file
from ..services.recycle import snapshot_base
from ..services.kb_scope import (
    admin_user_ids as _admin_user_ids,
    all_course_names,
    assert_writable_course,
    can_view_point,
    point_scope_condition,
    visible_course_names,
)
from ..services.vector_store import (
    add_document_chunks,
    add_knowledge_point,
    delete_document,
    delete_knowledge_point,
    update_knowledge_point,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def can_manage(user: User, creator_id: int | None) -> bool:
    """是否可管理某条记录：管理员，或记录创建者本人。"""
    if user.role == "admin":
        return True
    return creator_id is not None and creator_id == user.id


# 可见性规则统一在 services/kb_scope.py，浏览和问答检索共用同一套


# ==================== 知识库 ====================

@router.get("/bases/page", summary="知识库列表（分页 + 服务端搜索）")
def page_bases(
    keyword: str = Query(default="", max_length=100),
    field: str = Query(default="name", max_length=16),
    sort: str = Query(default="updated", max_length=16),
    limit: int = 10,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """列表页用：只返回当前页，前端不再全量拉回再过滤。"""
    if field not in ("name", "description", "creator"):
        raise HTTPException(status_code=400, detail="field 仅支持 name / description / creator")
    if sort not in ("updated", "id"):
        raise HTTPException(status_code=400, detail="sort 仅支持 updated / id")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    conditions = []
    if user.role != "admin":
        conditions.append(
            or_(
                KnowledgeBase.created_by == user.id,
                KnowledgeBase.created_by.in_(_admin_user_ids()),
            )
        )
    kw = keyword.strip()
    if kw:
        like = f"%{kw}%"
        if field == "description":
            conditions.append(KnowledgeBase.description.like(like))
        elif field == "creator":
            conditions.append(or_(User.real_name.like(like), User.username.like(like)))
        else:
            conditions.append(KnowledgeBase.name.like(like))

    join = select(KnowledgeBase).select_from(KnowledgeBase).outerjoin(
        User, KnowledgeBase.created_by == User.id
    )
    count_query = select(func.count(KnowledgeBase.id)).select_from(KnowledgeBase).outerjoin(
        User, KnowledgeBase.created_by == User.id
    )
    if conditions:
        join = join.where(*conditions)
        count_query = count_query.where(*conditions)
    total = db.scalar(count_query) or 0

    if sort == "id":
        join = join.order_by(KnowledgeBase.id.asc())
    else:
        join = join.order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id.desc())
    bases = list(
        db.scalars(
            join.options(joinedload(KnowledgeBase.creator)).limit(limit).offset(offset)
        )
    )
    names = [b.name for b in bases]
    counts = (
        dict(
            db.execute(
                select(KnowledgePoint.course, func.count(KnowledgePoint.id))
                .where(KnowledgePoint.course.in_(names))
                .group_by(KnowledgePoint.course)
            ).all()
        )
        if names
        else {}
    )
    # 文档条数与分块总数：一次 group by 查完，不做逐库统计
    doc_stats: dict[str, tuple[int, int]] = {}
    if names:
        for course, doc_count, chunk_total in db.execute(
            select(
                Document.course,
                func.count(Document.id),
                func.coalesce(func.sum(Document.chunk_count), 0),
            )
            .where(Document.course.in_(names))
            .group_by(Document.course)
        ).all():
            doc_stats[course] = (int(doc_count or 0), int(chunk_total or 0))

    items: list[KnowledgeBaseSummary] = []
    for b in bases:
        summary = KnowledgeBaseSummary.model_validate(b)
        summary.point_count = counts.get(b.name, 0)
        summary.document_count, summary.chunk_count = doc_stats.get(b.name, (0, 0))
        summary.creator_name = (
            b.creator.real_name or b.creator.username if b.creator else ""
        )
        items.append(summary)
    return {"total": total, "items": items}


@router.get("/bases", response_model=list[KnowledgeBaseSummary], summary="知识库列表（含统计）")
def list_bases(
    keyword: str = Query(default="", max_length=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KnowledgeBaseSummary]:
    stmt = select(KnowledgeBase).options(joinedload(KnowledgeBase.creator))
    if user.role != "admin":
        # 学生只收集：管理员发布的知识库 + 自己创建的
        stmt = stmt.where(
            or_(
                KnowledgeBase.created_by == user.id,
                KnowledgeBase.created_by.in_(_admin_user_ids()),
            )
        )
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
        # 同步更新该知识库下知识点的课程字段与向量元数据
        points = list(
            db.scalars(select(KnowledgePoint).where(KnowledgePoint.course == base.name))
        )
        for p in points:
            p.course = name
            update_knowledge_point(p.id, p.title, p.content, name, p.tags)

        # 文档的课程归属和向量也要一起改，否则学生按新库名检索不到
        docs = list(db.scalars(select(Document).where(Document.course == base.name)))
        for d in docs:
            d.course = name
            try:
                text = parse_file(Path(d.stored_path))
                chunks = split_text(text)
                delete_document(d.id)
                if chunks:
                    add_document_chunks(d.id, d.filename, name, chunks)
            except Exception:  # noqa: BLE001
                logger.exception("重命名知识库时重建文档向量失败 doc_id=%s", d.id)
                # 旧课程名的向量必须清掉，避免检索到过期归属
                delete_document(d.id)
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
    # 先落一份回收站快照（库 + 知识点 + 文档，源文件移到 trash 目录），
    # 再照常删除；30 天内误删可恢复
    snapshot_base(db, base, user.id)
    # 先删向量，再删记录
    points = list(
        db.scalars(select(KnowledgePoint).where(KnowledgePoint.course == base.name))
    )
    for p in points:
        delete_knowledge_point(p.id)
    db.execute(
        KnowledgePoint.__table__.delete().where(KnowledgePoint.course == base.name)
    )
    # 文档连同向量、源文件一起删，否则会变成孤儿继续出现在文档管理里
    docs = list(db.scalars(select(Document).where(Document.course == base.name)))
    for d in docs:
        delete_document(d.id)
        Path(d.stored_path).unlink(missing_ok=True)
        db.delete(d)
    db.delete(base)
    db.commit()
    return {"ok": True, "removed_points": len(points), "removed_documents": len(docs)}


# ==================== 知识点 ====================

@router.get("/points/page", summary="知识点列表（分页 + 服务端搜索）")
def page_points(
    keyword: str = Query(default="", max_length=100),
    course: str = Query(default=""),
    limit: int = 12,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """知识点列表页用：服务端过滤课程与关键词，只返回当前页。"""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    conditions: list = []
    scope = point_scope_condition(user, db)
    if scope:
        conditions.extend(scope)
    if course.strip():
        conditions.append(KnowledgePoint.course == course.strip())
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                KnowledgePoint.title.like(like),
                KnowledgePoint.content.like(like),
                KnowledgePoint.tags.like(like),
            )
        )

    count_query = select(func.count(KnowledgePoint.id))
    stmt = (
        select(KnowledgePoint)
        .options(
            joinedload(KnowledgePoint.creator),
            joinedload(KnowledgePoint.updater),
        )
        .order_by(KnowledgePoint.updated_at.desc(), KnowledgePoint.id.desc())
    )
    if conditions:
        count_query = count_query.where(*conditions)
        stmt = stmt.where(*conditions)
    total = db.scalar(count_query) or 0
    rows = list(db.scalars(stmt.limit(limit).offset(offset)))
    return {
        "total": total,
        "items": [KnowledgePointOut.model_validate(p) for p in rows],
    }


@router.get("/points", response_model=list[KnowledgePointOut], summary="知识点列表")
def list_points(
    keyword: str = Query(default="", max_length=100),
    course: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KnowledgePoint]:
    stmt = select(KnowledgePoint)
    # 知识点跟随所属知识库的可见性；历史课程和未分类只对创建者本人可见
    conditions = point_scope_condition(user, db)
    if conditions:
        stmt = stmt.where(*conditions)
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
    visible = visible_course_names(user, db)
    if visible is None:
        # 管理员：全部知识库 + 历史课程
        return sorted(all_course_names(db))
    # 学生：可见知识库 + 自己（或管理员）留下的历史课程
    return sorted(visible)


@router.post("/points", response_model=KnowledgePointOut, summary="新增知识点")
def create_point(
    payload: KnowledgePointIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgePoint:
    # 只能写进自己可见的知识库（管理员不受限）
    assert_writable_course(user, db, payload.course)
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
    # 只有创建者本人或管理员能改内容：以前所有登录用户都能编辑，
    # 改错了既没有留痕也追不回来。改挂知识库同样要目标库可写，
    # 否则等于借编辑把自己挪进别人的库。
    point = db.get(KnowledgePoint, point_id)
    if point is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    if not can_view_point(user, db, point):
        raise HTTPException(status_code=403, detail="只能编辑自己能访问的知识库下的知识点")
    if not can_manage(user, point.created_by):
        raise HTTPException(status_code=403, detail="只能编辑自己创建的知识点")
    assert_writable_course(user, db, payload.course)

    new_values = (
        payload.title.strip(),
        payload.content.strip(),
        payload.course.strip(),
        payload.tags.strip(),
    )
    old_values = (point.title, point.content, point.course, point.tags)

    # 先写向量再提交数据库：向量失败时数据库还停在旧值，rollback 即可；
    # 数据库提交失败则把向量恢复成旧内容，避免两边不一致。
    try:
        update_knowledge_point(point_id, *new_values)
    except Exception:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="向量库更新失败，请确认大模型服务已启动",
        )
    point.title, point.content, point.course, point.tags = new_values
    # 改过内容就不再是“自动生成”的展示记录，重建时要重新入库
    point.is_auto = 0
    # 留痕：谁改的，前端知识点卡片上直接显示
    point.updated_by = user.id
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        try:
            update_knowledge_point(point_id, *old_values)
        except Exception:  # noqa: BLE001
            logger.exception("知识点 %s 保存失败后回滚向量也失败", point_id)
        raise HTTPException(status_code=500, detail="知识点保存失败，已回滚")
    db.refresh(point)
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
