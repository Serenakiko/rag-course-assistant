"""知识点 / 知识库路由：浏览（按可见性过滤）+ 维护（创建者本人或管理员）。

可见性规则：
  - 管理员：能看到所有人的知识库
  - 学生：只看到管理员发布的知识库 + 自己创建的

权限规则：
  - 新增知识点、新增知识库：所有登录用户
  - 编辑 / 删除知识点、编辑或删除知识库：创建者本人或管理员
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from ..database import SessionLocal, get_db
from ..deps import get_current_user
from ..models import (
    Document,
    KnowledgeBase,
    KnowledgeBaseTombstone,
    KnowledgePoint,
    UnansweredQuestion,
    User,
    VectorCleanupTask,
)
from ..schemas import (
    KnowledgeBaseIn,
    KnowledgeBaseOut,
    KnowledgeBaseSummary,
    KnowledgePointIn,
    KnowledgePointOut,
)
from ..services.chunker import split_text
from ..services.document_parser import parse_file
from ..services.kb_scope import (
    admin_user_ids as _admin_user_ids,
)
from ..services.kb_scope import (
    assert_writable_course,
    can_view_point,
    point_scope_condition,
)
from ..services.like import LIKE_ESCAPE, contains, escape_like
from ..services.recycle import item_dir, snapshot_base
from ..services.vector_store import (
    add_document_chunks,
    add_knowledge_point,
    delete_document,
    delete_knowledge_base_vectors,
    delete_knowledge_point,
    update_knowledge_point,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _restore_snapshot_files(
    payload: str, docs: list[Document], trash_dir: Path
) -> bool:
    """删除事务失败时，把快照已移动的源文件恢复到原路径。"""
    try:
        data = json.loads(payload or "{}")
    except ValueError:
        return False
    entries = data.get("docs") or []
    docs_by_id = {doc.id: doc for doc in docs if doc.id is not None}
    restored_all = True
    for index, entry in enumerate(entries):
        doc = docs_by_id.get(entry.get("doc_id"))
        if doc is None and not entry.get("doc_id") and index < len(docs):
            # 兼容旧快照：老 payload 没有 doc_id，只能按当时顺序回退
            doc = docs[index]
        if doc is None:
            restored_all = False
            continue
        trash_name = entry.get("trash_name") or ""
        if not trash_name:
            continue
        source = trash_dir / trash_name
        destination = Path(doc.stored_path)
        if not source.exists():
            restored_all = False
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError:
            restored_all = False
            logger.exception("回滚快照文件失败：%s", source)
    return restored_all


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
        if field == "description":
            conditions.append(contains(KnowledgeBase.description, kw))
        elif field == "creator":
            conditions.append(
                or_(contains(User.username, kw), contains(User.account, kw))
            )
        else:
            conditions.append(contains(KnowledgeBase.name, kw))

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
            b.creator.username or b.creator.account if b.creator else ""
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
        kw = keyword.strip()
        stmt = stmt.where(
            or_(
                contains(KnowledgeBase.name, kw),
                contains(KnowledgeBase.description, kw),
            )
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
    # 文档条数与分块总数：与 /bases/page 口径保持一致
    names = [b.name for b in bases]
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

    result: list[KnowledgeBaseSummary] = []
    for b in bases:
        summary = KnowledgeBaseSummary.model_validate(b)
        summary.point_count = counts.get(b.name, 0)
        summary.document_count, summary.chunk_count = doc_stats.get(b.name, (0, 0))
        summary.creator_name = (
            b.creator.username or b.creator.account if b.creator else ""
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


def _rebuild_base_vectors(
    point_rows: list[tuple[int, str, str, str]],
    doc_rows: list[tuple[int, str, str]],
    course: str,
) -> None:
    """改名后异步重建向量。

    改名已经提交，重建期间旧 course 的向量暂时查不到；放在后台是为了避免大库
    在请求内同步重算导致网关超时。只处理仍在库里、且 course 已改到新名字的记录，
    防止给刚被删除的资料写回孤儿向量。
    """
    session = SessionLocal()
    try:
        for point_id, title, content, tags in point_rows:
            still_exists = session.scalar(
                select(KnowledgePoint.id).where(
                    KnowledgePoint.id == point_id,
                    KnowledgePoint.course == course,
                )
            )
            if still_exists is None:
                continue
            try:
                update_knowledge_point(point_id, title, content, course, tags)
            except Exception:  # noqa: BLE001
                logger.exception("改名后重建知识点向量失败 point_id=%s", point_id)
                # 旧 course 的向量必须清掉，否则检索 filter 永远匹配不到
                delete_knowledge_point(point_id, strict=False)
        for doc_id, stored_path, filename in doc_rows:
            still_exists = session.scalar(
                select(Document.id).where(
                    Document.id == doc_id,
                    Document.course == course,
                )
            )
            if still_exists is None:
                continue
            try:
                text = parse_file(Path(stored_path))
                chunks = split_text(text)
                delete_document(doc_id, strict=False)
                if chunks:
                    add_document_chunks(doc_id, filename, course, chunks)
            except Exception:  # noqa: BLE001
                logger.exception("改名后重建文档向量失败 doc_id=%s", doc_id)
                delete_document(doc_id, strict=False)
    finally:
        session.close()


@router.put("/bases/{base_id}", response_model=KnowledgeBaseOut, summary="更新知识库")
def update_base(
    base_id: int,
    payload: KnowledgeBaseIn,
    background: BackgroundTasks,
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
        old_name = base.name
        points = list(
            db.scalars(select(KnowledgePoint).where(KnowledgePoint.course == old_name))
        )
        docs = list(db.scalars(select(Document).where(Document.course == old_name)))
        # 在提交前把重建所需的数据取成普通值，后台任务不再依赖 ORM 对象
        point_rows = [(p.id, p.title, p.content, p.tags) for p in points]
        doc_rows = [(d.id, d.stored_path, d.filename) for d in docs]
        # 先改库并提交，向量交给后台重建；单条失败只记日志
        for p in points:
            p.course = name
        for d in docs:
            d.course = name
        # 未命中问题也存了课程名，不同步会让改名前的问题继续挂在旧库下
        db.execute(
            update(UnansweredQuestion)
            .where(UnansweredQuestion.course == old_name)
            .values(course=name)
        )
        base.name = name
        base.description = payload.description.strip()
        db.commit()
        db.refresh(base)
        background.add_task(_rebuild_base_vectors, point_rows, doc_rows, name)
        return base
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
    try:
        recycle_item = snapshot_base(db, base, user.id)
    except Exception:
        db.rollback()
        raise
    snapshot_item_id = recycle_item.id
    snapshot_payload = recycle_item.payload
    vector_key = base.vector_key
    course_name = base.name
    tombstone = db.scalar(
        select(KnowledgeBaseTombstone).where(
            KnowledgeBaseTombstone.knowledge_base_id == vector_key
        )
    )
    if tombstone is None:
        db.add(
            KnowledgeBaseTombstone(
                knowledge_base_id=vector_key,
                course_name=course_name,
            )
        )
    task = db.scalar(
        select(VectorCleanupTask).where(
            VectorCleanupTask.knowledge_base_id == vector_key,
            VectorCleanupTask.status == "pending",
        )
    )
    if task is None:
        task = VectorCleanupTask(
            knowledge_base_id=vector_key,
            course_name=course_name,
        )
        db.add(task)
    points = list(
        db.scalars(select(KnowledgePoint).where(KnowledgePoint.course == course_name))
    )
    db.execute(
        KnowledgePoint.__table__.delete().where(KnowledgePoint.course == course_name)
    )
    # 文档连同向量、源文件一起删，否则会变成孤儿继续出现在文档管理里
    docs = list(db.scalars(select(Document).where(Document.course == course_name)))
    stored_paths = [Path(d.stored_path) for d in docs if d.stored_path]
    for d in docs:
        db.delete(d)
    db.delete(base)
    # 删除记录、tombstone 与清理任务在同一事务中提交；提交失败时库仍可用。
    try:
        db.commit()
    except Exception:
        db.rollback()
        trash_path = item_dir(snapshot_item_id)
        if _restore_snapshot_files(snapshot_payload, docs, trash_path):
            shutil.rmtree(trash_path, ignore_errors=True)
        raise

    for path in stored_paths:
        path.unlink(missing_ok=True)

    task.attempts = int(task.attempts or 0) + 1
    try:
        delete_knowledge_base_vectors(vector_key, course_name)
    except Exception as exc:  # noqa: BLE001
        task.last_error = str(exc)[:1000]
        logger.error(
            "知识库已删除，但向量清理失败，已保留待重试任务 knowledge_base_id=%s",
            vector_key,
        )
    else:
        task.status = "done"
        task.last_error = ""
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
        kw = keyword.strip()
        conditions.append(
            or_(
                contains(KnowledgePoint.title, kw),
                contains(KnowledgePoint.content, kw),
                contains(KnowledgePoint.tags, kw),
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
    # 只有创建者本人或管理员能改；改挂知识库同样要目标库可写
    point = db.get(KnowledgePoint, point_id)
    if point is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    if not can_view_point(user, db, point):
        raise HTTPException(status_code=403, detail="只能编辑自己能访问的知识库下的知识点")
    if not can_manage(user, point.created_by):
        raise HTTPException(status_code=403, detail="只能编辑自己创建的知识点")
    # 目标库要可写，**源库也要可写**：只校验新 course，学生会把自己放在公共库里的
    # 知识点「搬」进自己的私库，公共库资料被无写权限的人单方面抽走。
    assert_writable_course(user, db, payload.course)
    if point.course and point.course != payload.course.strip():
        assert_writable_course(user, db, point.course)

    new_values = (
        payload.title.strip(),
        payload.content.strip(),
        payload.course.strip(),
        payload.tags.strip(),
    )
    old_values = (point.title, point.content, point.course, point.tags)

    # 先写向量再提交数据库；失败时数据库回滚，向量恢复旧值
    try:
        update_knowledge_point(point_id, *new_values)
    except Exception:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="向量库更新失败，请确认大模型服务已启动",
        )
    point.title, point.content, point.course, point.tags = new_values
    # 改过内容就不再是自动生成的展示记录
    point.is_auto = 0
    # 记录最后修改者
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


@router.delete("/points/{point_id}", summary="删除知识点")
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

    # 删除顺序：先删向量、再删 DB 记录。反过来的话，向量删除抛异常（模型服务挂了）
    # 会返回 500，但 DB 已经删了——前端提示失败、刷新一看记录已没，两头不一致。
    # 先删向量失败时这里直接抛错，DB 记录原样保留，用户可以重试。
    delete_knowledge_point(point_id)

    # 联动：被删知识点曾是某条反馈的推荐来源时，打回待处理。
    # related_points 里知识点条目存的是 title（见 recommend_titles），这里按标题精确匹配。
    if point.course:
        resolved_rows = db.scalars(
            select(UnansweredQuestion).where(
                UnansweredQuestion.course == point.course,
                UnansweredQuestion.status == "resolved",
                UnansweredQuestion.related_points.like(
                    f"%{escape_like(point.title)}%", escape=LIKE_ESCAPE
                ),
            )
        ).all()
        for q in resolved_rows:
            q.status = "pending"
            q.answer = ""
            q.answered_at = None
            q.read_at = None
    db.delete(point)
    db.commit()
    return {"ok": True}
