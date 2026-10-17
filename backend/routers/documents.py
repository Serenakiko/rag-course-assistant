"""文档路由：上传（后台解析入库）、列表、详情、删除。"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user
from ..models import Document, KnowledgePoint, UnansweredQuestion, User
from ..schemas import DocumentDetailOut, DocumentOut
from ..services.chunker import split_text
from ..services.document_parser import is_supported, parse_file
from ..services.kb_scope import assert_writable_course
from ..services.vector_store import (
    add_document_chunks,
    delete_document,
    delete_knowledge_point,
)

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
        chunks = split_text(text)
        if not chunks:
            doc.status = "failed"
            doc.error = "文档未提取到有效文本"
            session.commit()
            return
        count = add_document_chunks(doc.id, doc.filename, course, chunks)
        doc.status = "ready"
        doc.chunk_count = count
        doc.error = ""
        # 自动生成知识点：文档名即标题，内容取正文前 2000 字，只作详情页展示。
        # 不再写入向量：文档分块已经入过库，同一段内容存两份会在检索里重复命中。
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


@router.get("", response_model=list[DocumentOut], summary="文档列表")
def list_documents(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Document]:
    """管理员看全部；学生只看自己上传的，避免“能传却看不到/删不掉”。"""
    stmt = select(Document)
    if user.role != "admin":
        stmt = stmt.where(Document.uploaded_by == user.id)
    return list(db.scalars(stmt.order_by(Document.id.asc())))


@router.post("/{doc_id}/reparse", summary="重新解析失败的文档（管理员 / 上传者本人）")
def reparse_document(
    doc_id: int,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """把解析失败的文档重跑一遍：源文件还在，就不必让用户删掉重传。

    只允许 failed：ready 的再跑一次会多生成一条同名知识点，
    processing 的说明正在跑，别插队。
    """
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
) -> DocumentDetailOut:
    doc = db.get(Document, doc_id)
    if doc is None or (user.role != "admin" and doc.uploaded_by != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks: list[dict] = []
    if doc.status == "ready":
        try:
            from ..config import settings
            from ..services.vector_store import _get_client

            collection = _get_client().get_collection(settings.COLLECTION_NAME)
            data = collection.get(
                where={"source_type": "document", "doc_id": str(doc.id)},
                include=["documents", "metadatas"],
            )
            items = sorted(
                zip(data["ids"], data["documents"], data["metadatas"]),
                key=lambda x: int(x[2].get("chunk_index", 0)),
            )
            chunks = [
                {"index": int(meta.get("chunk_index", 0)), "text": text}
                for _id, text, meta in items
            ]
        except Exception:  # noqa: BLE001
            chunks = []
    return DocumentDetailOut(
        **{
            "id": doc.id,
            "filename": doc.filename,
            "file_type": doc.file_type,
            "file_size": doc.file_size,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "error": doc.error,
            "uploaded_by": doc.uploaded_by,
            "created_at": doc.created_at,
        },
        chunks=chunks,
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
    # 删除原始文件与文档向量
    if doc.stored_path:
        Path(doc.stored_path).unlink(missing_ok=True)
    delete_document(doc.id)
    # 联动 1：删除上传时自动生成的同名知识点（标题=文件名去扩展名，同课程、同上传者）
    stem = Path(doc.filename).stem
    auto_kps = db.scalars(
        select(KnowledgePoint).where(
            KnowledgePoint.title == stem,
            KnowledgePoint.course == (doc.course or ""),
            KnowledgePoint.created_by == doc.uploaded_by,
            KnowledgePoint.is_auto == 1,
        )
    ).all()
    for kp in auto_kps:
        delete_knowledge_point(kp.id)
        db.delete(kp)
    # 联动 2：知识来源被移除后，同课程的已解决反馈重置为待处理（答案快照不再有效）
    if doc.course:
        resolved_rows = db.scalars(
            select(UnansweredQuestion).where(
                UnansweredQuestion.course == doc.course,
                UnansweredQuestion.status == "resolved",
            )
        ).all()
        for q in resolved_rows:
            q.status = "pending"
            q.answer = ""
            q.answered_at = None
            q.read_at = None
    db.delete(doc)
    db.commit()
    return {"ok": True}
