"""回收站路由：知识库 / 文档误删后 30 天内可恢复（只有管理员看得到）。"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import settings
from ..database import get_db
from ..deps import require_role
from ..models import (
    Document,
    KnowledgeBase,
    KnowledgePoint,
    RecycleItem,
    User,
    VectorCleanupTask,
)
from ..services.chunker import split_text
from ..services.document_parser import parse_file
from ..services.recycle import (
    RETENTION_DAYS,
    drop_files,
    item_dir,
    rebuild_base_vectors,
    rebuild_document_vectors,
)
from ..services.vector_reconcile import run_vector_cleanup_task_by_id
from ..services.vector_store import (
    add_document_chunks,
    add_knowledge_point,
    delete_knowledge_base_vectors,
)

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


def _failed(item: RecycleItem, name: str, failed: list[dict], message: str):
    """恢复失败：抛 409，把逐条失败清单放在 detail 里。

    以前失败也返回 200 + ok:false，脚本 / 第三方调用方看状态码会以为成功。
    前端仍然按 detail 里的 failed 列表弹窗（见 recycle.js）。
    """
    raise HTTPException(status_code=409, detail=_failed_payload(item, name, failed, message))


def _failed_payload(item: RecycleItem, name: str, failed: list[dict], message: str) -> dict:
    """恢复失败时的响应体（作为 409 的 detail 返回）。"""
    return {
        "ok": False,
        "restored": False,
        "kind": item.kind,
        "name": name,
        "points": 0,
        "failed_points": 0,
        "documents": 0,
        "failed_documents": len(failed),
        "failed": failed,
        "message": message,
        "vectors_pending": False,
    }


def _success_payload(
    kind: str,
    name: str,
    points: int,
    documents: int,
    failed: list[dict],
    message: str,
    vectors_pending: bool,
) -> dict:
    return {
        "ok": True,
        "restored": True,
        "kind": kind,
        "name": name,
        "points": points,
        "failed_points": 0,
        "documents": documents,
        "failed_documents": len(failed),
        "failed": failed,
        "message": message,
        "vectors_pending": vectors_pending,
    }


@router.post("/{item_id}/restore", summary="恢复（管理员）")
def restore_item(
    item_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
    background: BackgroundTasks = None,  # FastAPI 注入；直接调用时为 None
) -> dict:
    """按快照重建知识库 / 文档。

    写库只做短事务（重建记录 + 搬文件），向量重建交给持久化的后台任务
    （vector_cleanup_tasks，kind=restore / restore_document），进程重启后
    由启动时的重试逻辑继续跑完。

    ``background`` 由 FastAPI 注入；直接调用（脚本 / 旧测试）时为空，
    此时走同步重建并在失败时回滚，保持原有的 500 语义。
    """
    item = db.get(RecycleItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="回收站里没有这条记录")
    if item.kind not in ("base", "document"):
        raise HTTPException(status_code=400, detail="这种内容暂不支持恢复")

    try:
        data = json.loads(item.payload or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="快照已损坏，无法恢复") from None

    # 快照里 created_by / uploaded_by 指向的用户可能已删，恢复时直接传那个 id 会
    # 因外键违约 500。这里先查出还存在的用户 id，不存在的置 None。
    creator_ids = {int(x) for x in (item.deleted_by,) if x is not None}
    for p in data.get("points") or []:
        if p.get("created_by") is not None:
            creator_ids.add(int(p["created_by"]))
    for d in data.get("docs") or []:
        if d.get("uploaded_by") is not None:
            creator_ids.add(int(d["uploaded_by"]))
    existing_ids: set[int] = set()
    if creator_ids:
        existing_ids = set(
            db.scalars(select(User.id).where(User.id.in_(sorted(creator_ids))))
        )

    def _safe_uid(uid) -> int | None:
        if uid is None:
            return None
        return int(uid) if int(uid) in existing_ids else None

    if item.kind == "document":
        return _restore_document(item, data, admin, db, background, _safe_uid)
    return _restore_base(item, data, admin, db, background, _safe_uid)


def _restore_base(item, data, admin, db, background, _safe_uid) -> dict:
    base_info = data.get("base") or {}
    origin = (base_info.get("name") or item.name or "已恢复的知识库").strip()
    origin = origin or "已恢复的知识库"

    docs_data = list(data.get("docs") or [])
    # 先确认所有源文件都在：缺文件时一条都不写库，直接把清单返回给前端。
    missing = []
    for d in docs_data:
        trash_name = d.get("trash_name") or ""
        src = item_dir(item.id) / trash_name if trash_name else None
        if src is None or not src.exists():
            missing.append(
                {
                    "name": d.get("filename") or trash_name or "文档",
                    "reason": "回收站中缺少源文件",
                }
            )
    if missing:
        _failed(item, origin, missing, "恢复失败：部分文档源文件缺失，未做任何改动")

    name = origin
    suffix = 0
    while db.scalar(select(KnowledgeBase.id).where(KnowledgeBase.name == name)) is not None:
        suffix += 1
        name = f"{origin}（恢复{suffix}）"

    # 复制文件放在写事务之前：复制可能较慢，源件留在 trash 里，失败后仍可重试。
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    copies: list[tuple[dict, Path]] = []
    try:
        for d in docs_data:
            src = item_dir(item.id) / d["trash_name"]
            dest = settings.UPLOAD_DIR / f"{uuid.uuid4().hex}{src.suffix}"
            copies.append((d, dest))
            shutil.copy2(src, dest)
    except Exception:  # noqa: BLE001
        for _, dest in copies:
            dest.unlink(missing_ok=True)
        logger.exception("恢复知识库复制文件失败 item_id=%s", item.id)
        raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

    try:
        base = KnowledgeBase(
            name=name,
            description=base_info.get("description") or "",
            created_by=_safe_uid(item.deleted_by) or admin.id,
        )
        db.add(base)
        db.flush()

        new_points: list[KnowledgePoint] = []
        for p in data.get("points") or []:
            kp = KnowledgePoint(
                title=(p.get("title") or "未命名知识点")[:255],
                content=p.get("content") or "",
                course=name,
                tags=p.get("tags") or "",
                created_by=_safe_uid(p.get("created_by")),
                is_auto=int(p.get("is_auto") or 0),
            )
            db.add(kp)
            new_points.append(kp)
        db.flush()

        new_docs: list[Document] = []
        for d, dest in copies:
            doc = Document(
                filename=d.get("filename") or dest.name,
                file_type=d.get("file_type") or dest.suffix.lstrip("."),
                file_size=int(d.get("file_size") or 0),
                stored_path=str(dest),
                status="processing",
                course=name,
                uploaded_by=_safe_uid(d.get("uploaded_by")),
                is_seed=int(d.get("is_seed") or 0),
                content_hash=d.get("content_hash") or "",
                content_simhash=d.get("content_simhash") or "",
                content_length=int(d.get("content_length") or 0),
                chunk_count=0,
            )
            db.add(doc)
            new_docs.append(doc)
        db.flush()

        task: VectorCleanupTask | None = None
        if new_docs or new_points:
            task = VectorCleanupTask(
                kind="restore",
                knowledge_base_id=base.vector_key,
                object_id=str(base.id),
                course_name=name,
            )
            db.add(task)
            db.flush()
    except Exception:  # noqa: BLE001
        db.rollback()
        for _, dest in copies:
            dest.unlink(missing_ok=True)
        logger.exception("恢复知识库写库失败，已回滚 item_id=%s", item.id)
        raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

    restored_points = len(new_points)
    restored_docs = len(new_docs)
    trash_path = item_dir(item.id)

    if background is None:
        # 直接调用（脚本 / 旧测试）的同步回退：失败整体回滚并抛 500。
        try:
            failed_points, failed_docs = rebuild_base_vectors(
                db,
                base.id,
                name,
                base.vector_key,
                add_point=add_knowledge_point,
                add_chunks=add_document_chunks,
                parse=parse_file,
                split=split_text,
                commit=False,
            )
            if failed_points or failed_docs:
                raise RuntimeError(
                    f"恢复未完成：知识点失败 {failed_points} 条，文档失败 {failed_docs} 条"
                )
        except Exception:  # noqa: BLE001
            db.rollback()
            try:
                delete_knowledge_base_vectors(base.vector_key, name, strict=False)
            except Exception:  # noqa: BLE001
                logger.exception("恢复失败后清理孤儿向量失败：%s", base.vector_key)
            for _, dest in copies:
                dest.unlink(missing_ok=True)
            logger.exception("恢复知识库失败，已回滚 item_id=%s", item.id)
            raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

        db.delete(item)
        db.commit()
        shutil.rmtree(trash_path, ignore_errors=True)
        return _success_payload(
            "base",
            name,
            restored_points,
            restored_docs,
            [],
            f"已恢复知识库「{name}」",
            False,
        )

    task_id = task.id
    db.delete(item)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        for _, dest in copies:
            dest.unlink(missing_ok=True)
        logger.exception("恢复知识库提交失败，已回滚 item_id=%s", item.id)
        raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

    shutil.rmtree(trash_path, ignore_errors=True)
    background.add_task(run_vector_cleanup_task_by_id, task_id)
    return _success_payload(
        "base",
        name,
        restored_points,
        restored_docs,
        [],
        f"已恢复知识库「{name}」，向量正在后台重建",
        True,
    )


def _restore_document(item, data, admin, db, background, _safe_uid) -> dict:
    docs_data = list(data.get("docs") or [])
    if not docs_data:
        _failed(item, item.name, [{"name": item.name, "reason": "快照中没有文档信息"}], "恢复失败")
    entry = docs_data[0]
    name = entry.get("filename") or item.name
    course = entry.get("course") or ""

    trash_name = entry.get("trash_name") or ""
    src = item_dir(item.id) / trash_name if trash_name else None
    if src is None or not src.exists():
        _failed(
            item,
            name,
            [{"name": name, "reason": "回收站中缺少源文件"}],
            "恢复失败：文档源文件缺失，未做任何改动",
        )

    kb = None
    if course:
        kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == course))
    vector_key = kb.vector_key if kb is not None else ""

    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = settings.UPLOAD_DIR / f"{uuid.uuid4().hex}{src.suffix}"
    try:
        shutil.copy2(src, dest)
    except Exception:  # noqa: BLE001
        logger.exception("恢复文档复制文件失败 item_id=%s", item.id)
        raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

    try:
        doc = Document(
            filename=name,
            file_type=entry.get("file_type") or dest.suffix.lstrip("."),
            file_size=int(entry.get("file_size") or 0),
            stored_path=str(dest),
            status="ready" if int(entry.get("is_seed") or 0) else "processing",
            course=course,
            uploaded_by=_safe_uid(entry.get("uploaded_by")),
            is_seed=int(entry.get("is_seed") or 0),
            content_hash=entry.get("content_hash") or "",
            content_simhash=entry.get("content_simhash") or "",
            content_length=int(entry.get("content_length") or 0),
            chunk_count=0,
        )
        db.add(doc)
        db.flush()

        task: VectorCleanupTask | None = None
        if not doc.is_seed:
            task = VectorCleanupTask(
                kind="restore_document",
                knowledge_base_id=vector_key,
                object_id=str(doc.id),
                course_name=course,
            )
            db.add(task)
            db.flush()
    except Exception:  # noqa: BLE001
        db.rollback()
        dest.unlink(missing_ok=True)
        logger.exception("恢复文档写库失败，已回滚 item_id=%s", item.id)
        raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

    trash_path = item_dir(item.id)

    if background is None:
        try:
            if task is not None:
                ok = rebuild_document_vectors(
                    db,
                    doc.id,
                    vector_key or None,
                    add_chunks=add_document_chunks,
                    parse=parse_file,
                    split=split_text,
                    commit=False,
                )
                if not ok:
                    raise RuntimeError("文档向量重建失败")
        except Exception:  # noqa: BLE001
            db.rollback()
            dest.unlink(missing_ok=True)
            logger.exception("恢复文档失败，已回滚 item_id=%s", item.id)
            raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

        db.delete(item)
        db.commit()
        shutil.rmtree(trash_path, ignore_errors=True)
        return _success_payload("document", name, 0, 1, [], f"已恢复文档「{name}」", False)

    task_id = task.id if task is not None else None
    db.delete(item)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        dest.unlink(missing_ok=True)
        logger.exception("恢复文档提交失败，已回滚 item_id=%s", item.id)
        raise HTTPException(status_code=500, detail="恢复失败，已回滚，请重试") from None

    shutil.rmtree(trash_path, ignore_errors=True)
    if task_id is not None:
        background.add_task(run_vector_cleanup_task_by_id, task_id)
    return _success_payload(
        "document",
        name,
        0,
        1,
        [],
        f"已恢复文档「{name}」，向量正在后台重建",
        task_id is not None,
    )


@router.delete("/{item_id}", summary="彻底删除（管理员）")
def purge_item(
    item_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(RecycleItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="回收站里没有这条记录")
    # 先提交删除、再清文件：删文件失败只留下孤儿文件，不会出现
    # 「记录没了但文件还在 / 记录还在但文件没了」互相矛盾的中间态。
    db.delete(item)
    db.commit()
    try:
        drop_files(item)
    except Exception:  # noqa: BLE001
        logger.exception("彻底删除后清理归档文件失败 item_id=%s", item_id)
    return {"ok": True}
