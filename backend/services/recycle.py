"""回收站：删除知识库前先落一份快照，30 天内可以恢复。

快照只存元信息和知识点正文，向量在恢复时重新入库；
文档源文件从 data/uploads 移到 data/trash/<item_id>/，恢复时搬回去。
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Document, KnowledgeBase, KnowledgePoint, RecycleItem

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
TRASH_DIR = settings.DATA_DIR / "trash"


def item_dir(item_id: int) -> Path:
    """某条回收站记录的文件目录。"""
    return TRASH_DIR / str(item_id)


def drop_files(item: RecycleItem) -> None:
    """删掉这条记录归档的源文件（彻底删除 / 过期清理时用）。"""
    shutil.rmtree(item_dir(item.id), ignore_errors=True)


def snapshot_base(db: Session, base: KnowledgeBase, user_id: int | None) -> RecycleItem:
    """删除知识库前落快照：库、知识点、文档元信息，源文件移进 trash。

    调用方随后照常删除记录与向量。恢复时按快照重建，
    知识点与文档分块会重新向量化。
    """
    points = list(
        db.scalars(select(KnowledgePoint).where(KnowledgePoint.course == base.name))
    )
    docs = list(db.scalars(select(Document).where(Document.course == base.name)))

    item = RecycleItem(kind="base", name=base.name, deleted_by=user_id, payload="{}")
    db.add(item)
    db.flush()  # 先拿到 id，文件按 id 归档

    target = item_dir(item.id)
    target.mkdir(parents=True, exist_ok=True)

    moved: list[dict] = []
    for d in docs:
        entry = {
            "filename": d.filename,
            "file_type": d.file_type,
            "file_size": d.file_size,
            "uploaded_by": d.uploaded_by,
            "is_seed": d.is_seed or 0,
            "trash_name": "",
        }
        src = Path(d.stored_path) if d.stored_path else None
        if src is not None and src.exists():
            name = f"{uuid.uuid4().hex}{src.suffix}"
            try:
                shutil.move(str(src), str(target / name))
                entry["trash_name"] = name
            except OSError:
                logger.warning("回收站归档文件失败：%s", src)
        moved.append(entry)

    item.payload = json.dumps(
        {
            "base": {"name": base.name, "description": base.description or ""},
            "points": [
                {
                    "title": p.title,
                    "content": p.content,
                    "tags": p.tags or "",
                    "created_by": p.created_by,
                    "is_auto": p.is_auto or 0,
                }
                for p in points
            ],
            "docs": moved,
        },
        ensure_ascii=False,
    )
    db.flush()
    return item


def purge_expired(db: Session) -> int:
    """清掉超过保留期的回收站记录与归档文件，返回清理条数。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    rows = [
        item
        for item in db.scalars(select(RecycleItem))
        if item.deleted_at is not None
        and item.deleted_at.replace(tzinfo=None) < cutoff
    ]
    for item in rows:
        drop_files(item)
        db.delete(item)
    if rows:
        db.commit()
    return len(rows)
