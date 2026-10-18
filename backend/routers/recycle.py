"""回收站路由：知识库误删后 30 天内可恢复（只有管理员看得到）。"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import settings
from ..database import get_db
from ..deps import require_role
from ..models import Document, KnowledgeBase, KnowledgePoint, RecycleItem, User
from ..services.chunker import split_text
from ..services.document_parser import parse_file
from ..services.recycle import RETENTION_DAYS, drop_files, item_dir
from ..services.vector_store import add_document_chunks, add_knowledge_point

logger = logging.getLogger(__name__)
router = APIRouter()


def _days_left(deleted_at) -> int:
    if deleted_at is None:
        return RETENTION_DAYS
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    used = (now - deleted_at.replace(tzinfo=None)).days
    return max(0, RETENTION_DAYS - used)


def _summary(item: RecycleItem) -> dict:
    try:
        data = json.loads(item.payload or "{}")
    except ValueError:
        data = {}
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "points": len(data.get("points") or []),
        "documents": len(data.get("docs") or []),
        "deleted_by": item.deleted_by,
        "deleter_name": item.deleter_name,
        "deleted_at": item.deleted_at,
        "days_left": _days_left(item.deleted_at),
    }


@router.get("", summary="回收站列表（管理员）")
def list_recycle(
    q: str = "",
    field: str = "name",
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """回收站条数不会多（只有被删的知识库），搜索在内存里过滤即可，
    不必为此加一个联表查询。"""
    if field not in ("name", "user"):
        raise HTTPException(status_code=400, detail="field 仅支持 name / user")
    rows = list(
        db.scalars(
            select(RecycleItem)
            .options(joinedload(RecycleItem.deleter))
            .order_by(RecycleItem.id.asc())
        )
    )
    items = [_summary(i) for i in rows]
    keyword = q.strip().lower()
    if keyword:
        key = "deleter_name" if field == "user" else "name"
        items = [i for i in items if keyword in (i.get(key) or "").lower()]
    return {"items": items, "retention_days": RETENTION_DAYS}


@router.post("/{item_id}/restore", summary="恢复（管理员）")
def restore_item(
    item_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """按快照重建知识库：重建知识点与文档分块并重新向量化。

    重名时自动加「（恢复N）」后缀，不会覆盖同名库。
    """
    item = db.get(RecycleItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="回收站里没有这条记录")
    if item.kind != "base":
        raise HTTPException(status_code=400, detail="这种内容暂不支持恢复")

    try:
        data = json.loads(item.payload or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="快照已损坏，无法恢复") from None

    base_info = data.get("base") or {}
    origin = (base_info.get("name") or item.name or "已恢复的知识库").strip()
    origin = origin or "已恢复的知识库"

    name = origin
    suffix = 0
    while db.scalar(select(KnowledgeBase.id).where(KnowledgeBase.name == name)) is not None:
        suffix += 1
        name = f"{origin}（恢复{suffix}）"

    base = KnowledgeBase(
        name=name,
        description=base_info.get("description") or "",
        created_by=item.deleted_by or admin.id,
    )
    db.add(base)
    db.commit()

    # 知识点先全部 add、一次 commit 拿到 id，再统一写向量。
    # 逐个 commit 时恢复 100 个知识点要等 100 次落盘。
    new_points: list[KnowledgePoint] = []
    for p in data.get("points") or []:
        kp = KnowledgePoint(
            title=(p.get("title") or "未命名知识点")[:255],
            content=p.get("content") or "",
            course=name,
            tags=p.get("tags") or "",
            created_by=p.get("created_by"),
            is_auto=int(p.get("is_auto") or 0),
        )
        db.add(kp)
        new_points.append(kp)
    db.commit()

    restored_points = failed_points = 0
    for kp in new_points:
        db.refresh(kp)
        restored_points += 1
        # 文档自动生成的知识点只作展示，不重复入向量（和重建脚本保持一致）
        if kp.is_auto:
            continue
        try:
            add_knowledge_point(kp.id, kp.title, kp.content, kp.course, kp.tags)
        except Exception:  # noqa: BLE001
            failed_points += 1
            logger.exception("恢复知识点向量失败 point_id=%s", kp.id)

    restored_docs = failed_docs = 0
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for d in data.get("docs") or []:
        trash_name = d.get("trash_name") or ""
        src = item_dir(item_id) / trash_name if trash_name else None
        if src is None or not src.exists():
            logger.warning("回收站里找不到文档源文件，跳过：%s", d.get("filename"))
            continue
        dest = settings.UPLOAD_DIR / f"{uuid.uuid4().hex}{src.suffix}"
        try:
            shutil.move(str(src), str(dest))
        except OSError:
            logger.exception("恢复文档文件失败 %s", src)
            continue

        doc = Document(
            filename=d.get("filename") or dest.name,
            file_type=d.get("file_type") or dest.suffix.lstrip("."),
            file_size=int(d.get("file_size") or 0),
            stored_path=str(dest),
            status="processing",
            course=name,
            uploaded_by=d.get("uploaded_by"),
            is_seed=int(d.get("is_seed") or 0),
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        if doc.is_seed:
            # 种子展示文档的正文已经作为知识点入过库，不再重复切分入库
            doc.status = "ready"
            db.commit()
            restored_docs += 1
            continue
        try:
            text = parse_file(dest)
            chunks = split_text(text)
            if not chunks:
                raise ValueError("没有提取到有效文本")
            count = add_document_chunks(doc.id, doc.filename, name, chunks)
            doc.status = "ready"
            doc.chunk_count = count
            doc.error = ""
            restored_docs += 1
        except Exception as exc:  # noqa: BLE001
            doc.status = "failed"
            doc.error = str(exc)[:500]
            failed_docs += 1
            logger.exception("恢复文档向量失败 doc_id=%s", doc.id)
        db.commit()

    db.delete(item)
    db.commit()
    shutil.rmtree(item_dir(item_id), ignore_errors=True)

    return {
        "ok": True,
        "name": name,
        "points": restored_points,
        "failed_points": failed_points,
        "documents": restored_docs,
        "failed_documents": failed_docs,
    }


@router.delete("/{item_id}", summary="彻底删除（管理员）")
def purge_item(
    item_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(RecycleItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="回收站里没有这条记录")
    drop_files(item)
    db.delete(item)
    db.commit()
    return {"ok": True}
