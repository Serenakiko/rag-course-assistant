"""回收站：删除知识库 / 文档前先落一份快照，30 天内可以恢复。

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
from ..models import Document, KnowledgeBase, KnowledgePoint, RecycleItem, VectorCleanupTask
from .chunker import split_text
from .document_parser import parse_file
from .vector_store import add_document_chunks, add_knowledge_point

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
TRASH_DIR = settings.DATA_DIR / "trash"


def item_dir(item_id: int) -> Path:
    """某条回收站记录的文件目录。"""
    return TRASH_DIR / str(item_id)


def drop_files(item: RecycleItem) -> None:
    """删掉这条记录归档的源文件（彻底删除 / 过期清理时用）。"""
    shutil.rmtree(item_dir(item.id), ignore_errors=True)


def _restore_moved_files(moved_files: list[tuple[Path, Path]]) -> bool:
    """把快照归档文件移回原路径；任意一条失败都返回 False。"""
    restored_all = True
    for original, archived in reversed(moved_files):
        if not archived.exists():
            if not original.exists():
                restored_all = False
            continue
        if original.exists():
            restored_all = False
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archived), str(original))
        except OSError:
            restored_all = False
            logger.exception("快照失败后恢复文件失败：%s", original)
    return restored_all


def _doc_entry(doc: Document) -> dict:
    """文档快照：除展示元信息外保留内容指纹，恢复后去重仍然有效。"""
    return {
        "doc_id": doc.id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "course": doc.course or "",
        "uploaded_by": doc.uploaded_by,
        "is_seed": doc.is_seed or 0,
        "content_hash": doc.content_hash or "",
        "content_simhash": doc.content_simhash or "",
        "content_length": doc.content_length or 0,
        "chunk_count": doc.chunk_count or 0,
        "trash_name": "",
    }


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
    moved_files: list[tuple[Path, Path]] = []
    try:
        for d in docs:
            entry = _doc_entry(d)
            src = Path(d.stored_path) if d.stored_path else None
            if src is not None and src.exists():
                name = f"{uuid.uuid4().hex}{src.suffix}"
                try:
                    destination = target / name
                    shutil.move(str(src), str(destination))
                    moved_files.append((src, destination))
                    entry["trash_name"] = name
                except OSError as exc:
                    logger.warning("回收站归档文件失败：%s", src)
                    raise OSError(f"无法归档源文件：{src}（{exc}）") from exc
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
    except Exception:
        if _restore_moved_files(moved_files):
            shutil.rmtree(target, ignore_errors=True)
        else:
            logger.error(
                "快照文件未能全部恢复，保留回收站归档目录：%s", target
            )
        raise
    return item


def recycle_document(
    db: Session, doc: Document, user_id: int | None
) -> RecycleItem:
    """把单个文档移入回收站，返回 kind="document" 的 RecycleItem。

    事务只做「归档文件 + 建回收站记录 + 删文档行 + 登记向量清理任务」，
    向量删除在提交之后执行（失败也留持久化任务），不在事务里调用嵌入。
    调用方负责权限判断；本函数自行提交事务。
    """
    entry = _doc_entry(doc)
    item = RecycleItem(
        kind="document", name=doc.filename, deleted_by=user_id, payload="{}"
    )
    db.add(item)
    db.flush()  # 先拿到 id，文件按 id 归档

    target = item_dir(item.id)
    target.mkdir(parents=True, exist_ok=True)

    src = Path(doc.stored_path) if doc.stored_path else None
    moved: tuple[Path, Path] | None = None
    try:
        if src is not None and src.exists():
            archived = target / f"{uuid.uuid4().hex}{src.suffix}"
            shutil.move(str(src), str(archived))
            moved = (src, archived)
            entry["trash_name"] = archived.name
        item.payload = json.dumps({"docs": [entry]}, ensure_ascii=False)
        task = VectorCleanupTask(kind="document", object_id=str(doc.id))
        db.add(task)
        db.delete(doc)
        db.commit()
    except Exception:
        db.rollback()
        if moved is not None:
            try:
                moved[0].parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved[1]), str(moved[0]))
            except OSError:
                logger.exception("文档回收失败，恢复源文件失败：%s", moved[0])
        shutil.rmtree(target, ignore_errors=True)
        raise

    # 提交之后再清向量：此时即使向量服务不可用，任务已随文档删除一起落库，
    # 进程重启时由 vector_reconcile 继续重试。
    from .vector_reconcile import run_vector_cleanup_task

    try:
        run_vector_cleanup_task(task)
    except Exception as exc:  # noqa: BLE001
        task.last_error = str(exc)[:1000]
        logger.error(
            "文档已移入回收站，但向量清理失败，已保留重试任务 doc_id=%s", entry["doc_id"]
        )
    else:
        task.status = "done"
        task.last_error = ""
    db.commit()
    return item


def rebuild_base_vectors(
    db: Session,
    base_id: int,
    course: str,
    knowledge_base_id: str | None,
    *,
    add_point=None,
    add_chunks=None,
    parse=None,
    split=None,
    commit: bool = True,
) -> tuple[int, int]:
    """重建某知识库的知识点 / 文档向量，返回（知识点失败数，文档失败数）。

    默认每次写文档状态后立即 commit，慢的嵌入调用不占用写事务。
    ``commit=False`` 供旧的同步恢复路径使用（失败时整体回滚）。
    可注入嵌入函数，便于测试替换。
    """
    add_point = add_point or add_knowledge_point
    add_chunks = add_chunks or add_document_chunks
    parse = parse or parse_file
    split = split or split_text

    base = db.get(KnowledgeBase, base_id)
    if base is None:
        raise RuntimeError(f"知识库不存在：{base_id}")

    points = list(
        db.scalars(select(KnowledgePoint).where(KnowledgePoint.course == course))
    )
    failed_points = 0
    for kp in points:
        # 文档自动生成的知识点只作展示，不重复入向量（和重建脚本保持一致）
        if kp.is_auto:
            continue
        try:
            add_point(
                kp.id,
                kp.title,
                kp.content,
                kp.course,
                kp.tags,
                knowledge_base_id=knowledge_base_id,
            )
        except Exception:  # noqa: BLE001
            failed_points += 1
            logger.exception("恢复知识点向量失败 point_id=%s", kp.id)

    docs = list(db.scalars(select(Document).where(Document.course == course)))
    failed_docs = 0
    for doc in docs:
        if doc.is_seed:
            # 种子展示文档的正文已经作为知识点入过库，不再重复切分入库
            doc.status = "ready"
            if commit:
                db.commit()
            continue
        try:
            if not doc.stored_path:
                raise ValueError("文档没有源文件")
            text = parse(Path(doc.stored_path))
            chunks = split(text)
            if not chunks:
                raise ValueError("没有提取到有效文本")
            count = add_chunks(
                doc.id,
                doc.filename,
                course,
                chunks,
                knowledge_base_id=knowledge_base_id,
            )
            doc.status = "ready"
            doc.chunk_count = count
            doc.error = ""
        except Exception as exc:  # noqa: BLE001
            doc.status = "failed"
            doc.error = str(exc)[:500]
            failed_docs += 1
            logger.exception("恢复文档向量失败 doc_id=%s", doc.id)
        if commit:
            db.commit()
    return failed_points, failed_docs


def rebuild_document_vectors(
    db: Session,
    doc_id: int,
    knowledge_base_id: str | None = None,
    *,
    add_chunks=None,
    parse=None,
    split=None,
    commit: bool = True,
) -> bool:
    """重建单个文档的向量并更新状态，成功返回 True。

    ``commit=False`` 供同步恢复路径使用。
    """
    add_chunks = add_chunks or add_document_chunks
    parse = parse or parse_file
    split = split or split_text

    doc = db.get(Document, doc_id)
    if doc is None:
        raise RuntimeError(f"文档不存在：{doc_id}")
    if doc.is_seed:
        doc.status = "ready"
        if commit:
            db.commit()
        return True
    try:
        if not doc.stored_path:
            raise ValueError("文档没有源文件")
        text = parse(Path(doc.stored_path))
        chunks = split(text)
        if not chunks:
            raise ValueError("没有提取到有效文本")
        count = add_chunks(
            doc.id,
            doc.filename,
            doc.course or "",
            chunks,
            knowledge_base_id=knowledge_base_id,
        )
        doc.status = "ready"
        doc.chunk_count = count
        doc.error = ""
    except Exception as exc:  # noqa: BLE001
        doc.status = "failed"
        doc.error = str(exc)[:500]
        if commit:
            db.commit()
        logger.exception("恢复文档向量失败 doc_id=%s", doc_id)
        return False
    if commit:
        db.commit()
    return True


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
