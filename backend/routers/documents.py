"""文档路由：上传（后台解析入库）、列表、详情、删除。"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user
from ..models import Document, KnowledgePoint, UnansweredQuestion, User, VectorCleanupTask
from ..schemas import DocumentDetailOut, DocumentOut
from ..services.chunker import split_text
from ..services.document_dedup import find_duplicate, fingerprint_text
from ..services.document_parser import is_supported, parse_file
from ..services.kb_scope import assert_writable_course
from ..services.like import LIKE_ESCAPE, contains, escape_like
from ..services.vector_reconcile import run_vector_cleanup_task
from ..services.vector_store import add_document_chunks, delete_document, get_collection

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


@router.post("/upload", response_model=DocumentOut, summary="上传课程文档")
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    course: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    filename = Path(file.filename or "unnamed").name
    if not is_supported(filename):
        raise HTTPException(
            status_code=400,
            detail="不支持的文件类型，仅支持 PDF / TXT / Markdown / CSV",
        )
    # 先看声明的文件大小，避免先把几十 MB 写进磁盘再报错
    if (file.size or 0) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件超过 20MB 限制")

    # 必须选一个已存在的知识库；空课程会变成问不出来的「未分类」资料，直接 400
    course_name = course.strip()
    assert_writable_course(user, db, course_name)

    # 内容去重：同一文件名、同一知识库、同一上传者的文档只保留一份。
    # 不拦的话，同一份课件反复上传会往向量库里灌进多份近乎相同的块，
    # 检索 Top-K 被重复内容挤占，真正相关的资料反而排不进来。
    dup = db.scalar(
        select(Document.id).where(
            Document.filename == filename,
            Document.course == course_name,
            Document.uploaded_by == user.id,
        )
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail="这个知识库里已经上传过同名文档，请先删除旧文档或改用其他文件名",
        )

    # 保存原始文件
    ext = Path(filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = settings.UPLOAD_DIR / stored_name
    size = 0
    try:
        with stored_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="文件超过 20MB 限制")
                out.write(chunk)
    except HTTPException:
        stored_path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"文件保存失败：{exc}") from exc

    doc = Document(
        filename=filename,
        file_type=ext.lstrip("."),
        file_size=size,
        stored_path=str(stored_path),
        status="processing",
        course=course_name,
        uploaded_by=user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    background.add_task(process_document, doc.id, course_name)
    return doc


def process_document(doc_id: int, course: str) -> None:
    """后台任务：解析 → 分块 → 向量入库。"""
    session = SessionLocal()
    try:
        doc = session.get(Document, doc_id)
        if doc is None:
            return
        text = parse_file(Path(doc.stored_path))
        fingerprint = fingerprint_text(text)
        doc.content_hash = fingerprint.content_hash
        doc.content_simhash = str(fingerprint.simhash)
        doc.content_length = fingerprint.length

        # 老数据没有指纹时按需补算一次；否则新上传只能和升级后的文档比较，
        # 历史资料仍会漏过去重。
        candidates = list(
            session.scalars(
                select(Document).where(
                    Document.course == course,
                    Document.id != doc.id,
                    Document.status.in_(("ready", "processing")),
                )
            ).all()
        )
        for candidate in candidates:
            if (
                candidate.content_hash
                and candidate.content_simhash
                and candidate.content_length > 0
            ):
                continue
            if not candidate.stored_path or not Path(candidate.stored_path).exists():
                continue
            try:
                candidate_text = parse_file(Path(candidate.stored_path))
                candidate_fingerprint = fingerprint_text(candidate_text)
                candidate.content_hash = candidate_fingerprint.content_hash
                candidate.content_simhash = str(candidate_fingerprint.simhash)
                candidate.content_length = candidate_fingerprint.length
            except Exception:  # noqa: BLE001
                logger.warning(
                    "补算历史文档内容指纹失败 doc_id=%s",
                    candidate.id,
                    exc_info=True,
                )

        duplicate = find_duplicate(doc.id, fingerprint, candidates)
        if duplicate is not None:
            doc.status = "failed"
            doc.chunk_count = 0
            doc.error = (
                f"检测到与「{duplicate.filename}」内容相同或高度相似，已跳过入库。"
                "如需更新，请先删除旧文档后重新上传。"
            )
            session.commit()
            return

        chunks = split_text(text)
        if not chunks:
            doc.status = "failed"
            doc.error = "文档未提取到有效文本"
            session.commit()
            return
        count = add_document_chunks(doc.id, doc.filename, course, chunks)
        # 写向量期间文档可能被删除：DB 行没了但向量已入库，留下检索得到、点开 404 的
        # 无主分块。落库前先确认行还在，没了就把刚写入的向量清掉再退出。
        # 必须用新 session 查：当前 session 的 identity map 会返回缓存对象，SQLite
        # 的读事务也可能停在旧快照上，两种都会让删除检测失效。
        with SessionLocal() as check:
            still_exists = check.scalar(select(Document.id).where(Document.id == doc_id))
        if still_exists is None:
            delete_document(doc_id, strict=False)
            return
        doc.status = "ready"
        doc.chunk_count = count
        doc.error = ""
        # 自动生成知识点：文档名即标题、正文前 2000 字，只作展示，不入向量
        try:
            title = Path(doc.filename).stem or doc.filename
            session.add(
                KnowledgePoint(
                    title=title[:255],
                    content=text[:2000],
                    course=course,
                    tags="",
                    created_by=doc.uploaded_by,
                    is_auto=1,
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("文档自动生成知识点失败 doc_id=%s", doc_id)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("文档处理失败 doc_id=%s", doc_id)
        try:
            doc = session.get(Document, doc_id)
            if doc is not None:
                doc.status = "failed"
                doc.error = str(exc)[:500]
                session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
    finally:
        session.close()


@router.get("", summary="文档列表（分页 + 搜索）")
def list_documents(
    keyword: str = Query(default="", max_length=100),
    field: str = Query(default="filename", max_length=16),
    limit: int = 10,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """管理员看全部；学生只看自己上传的。

    服务端分页 + 搜索。processing 按可见范围的全量统计，不受当前搜索词影响。
    """
    if field not in ("filename", "course", "type"):
        raise HTTPException(status_code=400, detail="field 仅支持 filename / course / type")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    # scope = 可见范围（学生只看自己上传的）；processing 不受搜索词影响
    scope: list = []
    if user.role != "admin":
        scope.append(Document.uploaded_by == user.id)
    conditions = list(scope)
    kw = keyword.strip()
    if kw:
        column = {"course": Document.course, "type": Document.file_type}.get(
            field, Document.filename
        )
        conditions.append(contains(column, kw))

    total = db.scalar(select(func.count(Document.id)).where(*conditions)) or 0
    processing = (
        db.scalar(
            select(func.count(Document.id)).where(
                *scope, Document.status == "processing"
            )
        )
        or 0
    )
    rows = db.scalars(
        select(Document)
        .where(*conditions)
        .order_by(Document.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "processing": processing,
        "items": [DocumentOut.model_validate(d) for d in rows],
    }


@router.post("/{doc_id}/reparse", summary="重新解析失败的文档（管理员 / 上传者本人）")
def reparse_document(
    doc_id: int,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """把解析失败的文档重跑一遍：源文件还在就不必重传。只允许 failed。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 学生端的「我的文档」也复用这张表格：自己的文档自己可以重试
    if user.role != "admin" and doc.uploaded_by != user.id:
        raise HTTPException(status_code=403, detail="只能重新解析自己上传的文档")
    if doc.status == "ready":
        raise HTTPException(status_code=400, detail="文档已经解析完成，不需要重新解析")
    if doc.status == "processing":
        raise HTTPException(status_code=400, detail="这个文档正在解析，稍等再看")
    if not doc.stored_path or not Path(doc.stored_path).exists():
        raise HTTPException(status_code=400, detail="原始文件已丢失，只能删除后重新上传")
    doc.status = "processing"
    doc.error = ""
    db.commit()
    background.add_task(process_document, doc.id, doc.course or "")
    return {"ok": True}


@router.get("/{doc_id}", response_model=DocumentDetailOut, summary="文档详情（含分块预览）")
def document_detail(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    chunk_limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DocumentDetailOut:
    doc = db.get(Document, doc_id)
    if doc is None or (user.role != "admin" and doc.uploaded_by != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks: list[dict] = []
    chunks_total = 0
    if doc.status == "ready":
        try:
            collection = get_collection()
            # Chroma 的 where 只接受一个操作符，多条件必须写 $and
            metadata = collection.get(
                where={"$and": [{"source_type": "document"}, {"doc_id": str(doc.id)}]},
                include=["metadatas"],
            )
            ordered = sorted(
                zip(metadata["ids"], metadata["metadatas"]),
                key=lambda x: int(x[1].get("chunk_index", 0)),
            )
            chunks_total = len(ordered)
            selected = ordered[:chunk_limit]
            if selected:
                data = collection.get(
                    ids=[item_id for item_id, _meta in selected],
                    include=["documents", "metadatas"],
                )
                by_id = {
                    item_id: (text, meta)
                    for item_id, text, meta in zip(
                        data["ids"], data["documents"], data["metadatas"], strict=False
                    )
                }
                chunks = [
                    {
                        "index": int(meta.get("chunk_index", 0)),
                        "text": by_id.get(item_id, ("", meta))[0],
                    }
                    for item_id, meta in selected
                ]
        except Exception:  # noqa: BLE001
            logger.warning("读取文档分块失败 doc_id=%s", doc.id, exc_info=True)
            chunks = []
    # 无向量分块的文档（如种子文档）直接读原文件，别让详情页空着
    preview = ""
    if not chunks and doc.status == "ready" and doc.stored_path and Path(doc.stored_path).exists():
        try:
            preview = parse_file(Path(doc.stored_path))[:20000]
        except Exception:  # noqa: BLE001
            logger.warning("文档详情读取原文失败 doc_id=%s", doc.id, exc_info=True)
    return DocumentDetailOut(
        **{
            "id": doc.id,
            "filename": doc.filename,
            "file_type": doc.file_type,
            "file_size": doc.file_size,
            "status": doc.status,
            "course": doc.course or "",
            "chunk_count": doc.chunk_count,
            "error": doc.error,
            "uploaded_by": doc.uploaded_by,
            "created_at": doc.created_at,
        },
        chunks=chunks,
        chunks_total=chunks_total,
        chunks_truncated=chunks_total > len(chunks),
        preview=preview,
    )


@router.delete("/{doc_id}", summary="删除文档（连同向量）")
def remove_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if user.role != "admin" and doc.uploaded_by != user.id:
        raise HTTPException(status_code=403, detail="只能删除自己上传的文档")

    # 联动 1：先收集要删除的自动知识点（在删 DB 前确定，避免顺序影响匹配）
    stem = Path(doc.filename).stem
    auto_kps = list(
        db.scalars(
            select(KnowledgePoint).where(
                KnowledgePoint.title == stem,
                KnowledgePoint.course == (doc.course or ""),
                KnowledgePoint.created_by == doc.uploaded_by,
                KnowledgePoint.is_auto == 1,
            )
        )
    )
    # 联动：被删文档曾是某条反馈的推荐来源时，打回待处理。
    if doc.course:
        resolved_rows = db.scalars(
            select(UnansweredQuestion).where(
                UnansweredQuestion.course == doc.course,
                UnansweredQuestion.status == "resolved",
                UnansweredQuestion.related_points.like(
                    f"%{escape_like(doc.filename)}%", escape=LIKE_ESCAPE
                ),
            )
        ).all()
        for q in resolved_rows:
            q.status = "pending"
            q.answer = ""
            q.answered_at = None
            q.read_at = None

    # 先删 DB 记录（事务），成功后再异步清理向量与文件。
    # 反过来的话：文件/向量删了、DB 删除失败，就出现「文件没了引用还在」或「向量没了记录还在」。
    stored_path = doc.stored_path
    for kp in auto_kps:
        db.delete(kp)
    cleanup_tasks = [
        VectorCleanupTask(kind="document", object_id=str(doc.id)),
        *(
            VectorCleanupTask(kind="knowledge_point", object_id=str(kp.id))
            for kp in auto_kps
        ),
    ]
    db.add_all(cleanup_tasks)
    db.delete(doc)
    db.commit()

    # 记录与清理任务同一事务提交，向量服务异常时任务仍可在启动后重试。
    for task in cleanup_tasks:
        try:
            run_vector_cleanup_task(task)
        except Exception as exc:  # noqa: BLE001
            task.last_error = str(exc)[:1000]
            logger.error(
                "文档记录已删除，但向量清理失败，已保留重试任务 kind=%s object_id=%s",
                task.kind,
                task.object_id,
            )
        else:
            task.status = "done"
            task.last_error = ""
    db.commit()
    if stored_path:
        Path(stored_path).unlink(missing_ok=True)
    return {"ok": True}
