"""文档路由：上传（后台解析入库）、列表、详情、删除。"""
from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import quote

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
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user, require_role
from ..models import Document, KnowledgePoint, User
from ..schemas import DocumentDetailOut, DocumentOut
from ..services.chunker import split_text
from ..services.document_dedup import find_duplicate, fingerprint_text
from ..services.document_parser import is_supported, parse_file
from ..services.kb_scope import assert_writable_course
from ..services.like import contains
from ..services.notify import notify_admins
from ..services.vector_store import add_document_chunks, delete_document, get_collection

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


@router.post("/upload", response_model=DocumentOut, summary="上传课程文档")
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    course: str = Form(default=""),
    user: User = Depends(require_role("admin")),
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
        # 原始异常里有服务器路径与底层库信息，只进日志，不回给前端
        logger.exception("保存上传文件失败：%s", stored_path.name)
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500, detail="文件保存失败，请稍后重试或联系管理员"
        ) from exc

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
    # DB 写入失败时把刚落盘的文件删掉，避免留下没人引用的孤儿文件。
    # 并发同名上传在这里兜底：模型没有唯一约束时主要靠上面的预检查，
    # 有唯一约束时则由此返回 409 而不是 500。
    try:
        db.commit()
        db.refresh(doc)
    except IntegrityError as exc:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail="这个知识库里已经上传过同名文档，请先删除旧文档或改用其他文件名",
        ) from exc
    except Exception:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise

    background.add_task(process_document, doc.id, course_name)
    return doc


_FINGERPRINT_BATCH = 20


def _update_document(doc_id: int, *, expected_status: str | None = None, **values) -> bool:
    """用独立短事务更新文档字段；行不存在或状态已变时返回 False。

    每次只持锁到本条 commit，绝不把 parse_file 等慢操作包进写事务。
    """
    with SessionLocal() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            return False
        if expected_status is not None and doc.status != expected_status:
            return False
        for key, value in values.items():
            setattr(doc, key, value)
        session.commit()
        return True


def _mark_failed(doc_id: int, error: str) -> None:
    try:
        _update_document(doc_id, status="failed", error=error[:500])
    except Exception:  # noqa: BLE001
        logger.exception("写入文档失败状态失败 doc_id=%s", doc_id)


def _update_fingerprints(rows: list[tuple[int, object]]) -> None:
    """按批用独立短事务回填历史文档指纹，每批结束立即释放写锁。"""
    for start in range(0, len(rows), _FINGERPRINT_BATCH):
        with SessionLocal() as session:
            for candidate_id, fingerprint in rows[start : start + _FINGERPRINT_BATCH]:
                candidate = session.get(Document, candidate_id)
                if candidate is None:
                    continue
                candidate.content_hash = fingerprint.content_hash
                candidate.content_simhash = str(fingerprint.simhash)
                candidate.content_length = fingerprint.length
            session.commit()


def _candidate_snapshot(course: str, doc_id: int) -> list[SimpleNamespace]:
    """只读地取出同课程候选文档，供补算 / 去重使用（不持有 ORM 会话）。"""
    with SessionLocal() as session:
        rows = session.execute(
            select(
                Document.id,
                Document.filename,
                Document.status,
                Document.content_hash,
                Document.content_simhash,
                Document.content_length,
                Document.stored_path,
            ).where(
                Document.course == course,
                Document.id != doc_id,
                Document.status.in_(("ready", "processing")),
            )
        ).all()
    return [
        SimpleNamespace(
            id=row.id,
            filename=row.filename,
            status=row.status,
            content_hash=row.content_hash,
            content_simhash=row.content_simhash,
            content_length=row.content_length or 0,
            stored_path=row.stored_path,
        )
        for row in rows
    ]


def process_document(doc_id: int, course: str) -> None:
    """后台任务：解析 → 分块 → 向量入库。

    慢操作（parse_file / 指纹计算 / embedding）全部放在写事务之外；
    DB 只通过若干独立短事务读快照、写结果，避免后台任务长时间占住写锁。
    """
    try:
        # 1) 只读取本次处理需要的字段，立即关闭会话
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            if doc is None:
                return
            stored_path = doc.stored_path
            filename = doc.filename
            uploaded_by = doc.uploaded_by

        # 2) 解析、指纹、历史文档补算都在会话外完成
        text = parse_file(Path(stored_path))
        fingerprint = fingerprint_text(text)
        candidates = _candidate_snapshot(course, doc_id)
        pending: list[tuple[int, object]] = []
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
                candidate_fingerprint = fingerprint_text(
                    parse_file(Path(candidate.stored_path))
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "补算历史文档内容指纹失败 doc_id=%s",
                    candidate.id,
                    exc_info=True,
                )
                continue
            pending.append((candidate.id, candidate_fingerprint))
            candidate.content_hash = candidate_fingerprint.content_hash
            candidate.content_simhash = str(candidate_fingerprint.simhash)
            candidate.content_length = candidate_fingerprint.length

        # 3) 补算结果按小批独立事务回填，解析阶段不持有任何写锁
        try:
            _update_fingerprints(pending)
        except Exception:  # noqa: BLE001
            logger.warning("回写历史文档内容指纹失败，本次跳过", exc_info=True)

        # 指纹写回用短事务；状态不是 processing（已删 / 已被并发处理）就退出
        if not _update_document(
            doc_id,
            expected_status="processing",
            content_hash=fingerprint.content_hash,
            content_simhash=str(fingerprint.simhash),
            content_length=fingerprint.length,
        ):
            return

        duplicate = find_duplicate(doc_id, fingerprint, candidates)
        if duplicate is not None:
            _update_document(
                doc_id,
                expected_status="processing",
                status="failed",
                chunk_count=0,
                error=(
                    f"检测到与「{duplicate.filename}」内容相同或高度相似，已跳过入库。"
                    "如需更新，请先删除旧文档后重新上传。"
                ),
            )
            return

        chunks = split_text(text)
        if not chunks:
            _update_document(
                doc_id,
                expected_status="processing",
                status="failed",
                error="文档未提取到有效文本",
            )
            return

        # 4) 慢操作：embedding + 向量写入，不持有 DB 事务
        count = add_document_chunks(doc_id, filename, course, chunks)

        # 写向量期间文档可能被删除：DB 行没了但向量已入库，留下检索得到、点开 404 的
        # 无主分块。落库前先确认行还在，没了就把刚写入的向量清掉再退出。
        if not _update_document(
            doc_id, expected_status="processing", status="ready", chunk_count=count, error=""
        ):
            delete_document(doc_id, strict=False)
            return

        # 5) 自动知识点单独短事务；失败只记日志，不回滚「文档已 ready」的结果
        try:
            with SessionLocal() as kp_session:
                kp_session.add(
                    KnowledgePoint(
                        title=(Path(filename).stem or filename)[:255],
                        content=text[:2000],
                        course=course,
                        tags="",
                        created_by=uploaded_by,
                        is_auto=1,
                    )
                )
                kp_session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("文档自动生成知识点失败 doc_id=%s", doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("文档处理失败 doc_id=%s", doc_id)
        _mark_failed(doc_id, str(exc))
        with SessionLocal() as notify_db:
            notify_admins(
                notify_db,
                "doc_failed",
                f"文档解析失败：{filename}",
                f"课程：{course or '未指定'}；原因：{str(exc)[:120]}",
                "#/documents",
            )


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


@router.post("/{doc_id}/reparse", summary="重新解析失败的文档（管理员）")
def reparse_document(
    doc_id: int,
    background: BackgroundTasks,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """把解析失败的文档重跑一遍：源文件还在就不必重传。只允许 failed。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
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


def _vector_version(item_id: str) -> int | None:
    """从向量 id 的 `-vN` 后缀取出写入版本；无版本后缀时返回 None。"""
    _head, sep, raw = str(item_id).rpartition("-v")
    return int(raw) if sep and raw.isdigit() else None


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
            entries = list(zip(metadata["ids"], metadata["metadatas"], strict=False))
            # 「先写新版本、再删旧版本」失败时旧版本可能残留；详情只展示当前版本，
            # 否则同一分块会重复出现，也与检索命中的内容不一致。
            versions = {
                version
                for item_id, _meta in entries
                if (version := _vector_version(item_id)) is not None
            }
            if versions:
                latest = max(versions)
                entries = [
                    (item_id, meta)
                    for item_id, meta in entries
                    if _vector_version(item_id) == latest
                ]
            ordered = sorted(
                entries,
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
    # 不在请求线程里解析原文件（20MB PDF 会把 worker 卡住）；
    # 预览只从已入库的分块拼装，取不到就留空，由前端提示「暂无预览」。
    preview = ""
    if chunks:
        preview = "\n\n".join(str(chunk.get("text") or "") for chunk in chunks)[:20000]
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


@router.get("/{doc_id}/file", summary="预览 / 下载文档原文件")
def document_file(
    doc_id: int,
    download: Annotated[int, Query(ge=0, le=1)] = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """给前端来源卡点击用：默认 inline 预览，`?download=1` 时以附件下载。"""
    doc = db.get(Document, doc_id)
    # 可见范围与文档列表一致：管理员全部，学生只看自己上传的文档
    if doc is None or (user.role != "admin" and doc.uploaded_by != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    path = Path(doc.stored_path) if doc.stored_path else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="原始文件不存在")
    headers = None
    if download:
        headers = {
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(doc.filename or path.name)}"
            )
        }
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers=headers)


@router.delete("/{doc_id}", summary="删除文档（进回收站）")
def remove_document(
    doc_id: int,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 回收站删除由 recycle 服务统一实现；这里只做鉴权与响应协议，
    # 快照、移文件、删除向量 cleanup 任务都交给它，避免两套删除逻辑。
    try:
        from ..services.recycle import recycle_document
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="回收站文档删除能力尚未就绪，暂时无法删除文档",
        ) from exc
    item = recycle_document(db, doc, user.id)
    return {"recycled": True, "item_id": item.id}
