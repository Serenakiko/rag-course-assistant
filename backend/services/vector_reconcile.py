"""向量清理 / 恢复重建任务的启动重试。"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from ..database import SessionLocal
from ..models import KnowledgeBase, VectorCleanupTask, utcnow
from .recycle import rebuild_base_vectors, rebuild_document_vectors
from .vector_store import (
    delete_document,
    delete_knowledge_base_vectors,
    delete_knowledge_point,
)

logger = logging.getLogger(__name__)


def _task_session(task: VectorCleanupTask) -> Session:
    """给需要读库的重建任务开一个独立 Session。

    用任务所在 Session 的 bind（而不是 SessionLocal），这样测试里用内存库时
    也能读到同一份数据；独立 Session 可避免重建期间的写操作占用外层事务。
    """
    outer = object_session(task)
    if outer is None:
        return SessionLocal()
    return Session(bind=outer.get_bind())


def run_vector_cleanup_task(task: VectorCleanupTask) -> None:
    """执行一条持久化向量任务；失败时抛异常由调用方记录。"""
    task.attempts = int(task.attempts or 0) + 1
    task.updated_at = utcnow()
    if task.kind == "document":
        delete_document(int(task.object_id), strict=True)
    elif task.kind == "knowledge_point":
        delete_knowledge_point(int(task.object_id), strict=True)
    elif task.kind == "knowledge_base_rebuild":
        # 知识库改名后的重建任务：重启后按当前库名重新入库。
        # 注意不能落到最后的 else 分支——那会按旧 course 删向量，把改名后的库清空。
        with _task_session(task) as rebuild_db:
            base = rebuild_db.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.vector_key == task.knowledge_base_id
                )
            )
            if base is None:
                # 库已在排队期间被删除：任务作废，不重建也不删别的课程
                return
            course = base.name
            delete_knowledge_base_vectors(base.vector_key, task.course_name)
            failed_points, failed_docs = rebuild_base_vectors(
                rebuild_db, base.id, course, base.vector_key
            )
        if failed_points or failed_docs:
            raise RuntimeError(
                f"向量重建未完成：知识点失败 {failed_points} 条，文档失败 {failed_docs} 条"
            )
    elif task.kind == "restore":
        # 恢复知识库后的向量重建：慢操作放在独立 Session 里，且每条文档
        # 提交一次，避免长时间占用 SQLite 写锁。
        with _task_session(task) as rebuild_db:
            failed_points, failed_docs = rebuild_base_vectors(
                rebuild_db,
                int(task.object_id or 0),
                task.course_name,
                task.knowledge_base_id or None,
            )
        if failed_points or failed_docs:
            raise RuntimeError(
                f"向量重建未完成：知识点失败 {failed_points} 条，文档失败 {failed_docs} 条"
            )
    elif task.kind == "restore_document":
        with _task_session(task) as rebuild_db:
            restored = rebuild_document_vectors(
                rebuild_db,
                int(task.object_id or 0),
                task.knowledge_base_id or None,
            )
        if not restored:
            raise RuntimeError("文档向量重建失败")
    else:
        delete_knowledge_base_vectors(task.knowledge_base_id, task.course_name)


def run_vector_cleanup_task_by_id(task_id: int) -> bool:
    """按 id 执行一条待处理任务（后台任务入口），返回是否成功。"""
    with SessionLocal() as db:
        task = db.get(VectorCleanupTask, task_id)
        if task is None or task.status != "pending":
            return False
        try:
            run_vector_cleanup_task(task)
        except Exception as exc:  # noqa: BLE001
            task.last_error = str(exc)[:1000]
            logger.warning(
                "向量任务执行失败 task_id=%s kind=%s course=%s",
                task.id,
                task.kind,
                task.course_name,
            )
            db.commit()
            return False
        task.status = "done"
        task.last_error = ""
        db.commit()
        return True


def retry_pending_vector_cleanups(limit: int = 20) -> int:
    """重试上次未完成的向量删除 / 恢复重建；返回本轮成功数。"""
    completed = 0
    with SessionLocal() as db:
        tasks = list(
            db.scalars(
                select(VectorCleanupTask)
                .where(VectorCleanupTask.status == "pending")
                .order_by(VectorCleanupTask.id)
                .limit(limit)
            )
        )
        for task in tasks:
            try:
                run_vector_cleanup_task(task)
            except Exception as exc:  # noqa: BLE001
                task.last_error = str(exc)[:1000]
                logger.warning(
                    "向量清理任务重试失败 task_id=%s kind=%s course=%s",
                    task.id,
                    task.kind,
                    task.course_name,
                )
            else:
                task.status = "done"
                task.last_error = ""
                completed += 1
            db.commit()
    return completed


def sweep_orphan_vectors(db: Session | None = None) -> int:
    """删除 DB 里已不存在的文档 / 知识点留下的孤儿向量，返回删除条数。

    向量库和 SQLite 是两套存储，删除流程中断（进程被结束、向量服务当时不可用）
    会留下「检索得到、点开 404」的分块。启动时按 metadata 里的 doc_id / point_id
    对一遍 DB，把对不上的分块清掉，比等用户点开报错再人工排查靠谱。
    """
    from ..models import Document, KnowledgePoint
    from .vector_store import delete_vectors_by_ids, iter_vectors

    rows = iter_vectors()
    if not rows:
        return 0

    own_db = db is None
    session = db or SessionLocal()
    try:
        doc_ids = {str(i) for i in session.scalars(select(Document.id))}
        point_ids = {str(i) for i in session.scalars(select(KnowledgePoint.id))}
    finally:
        if own_db:
            session.close()

    orphan_ids: list[str] = []
    for vector_id, meta in rows:
        source_type = meta.get("source_type")
        if source_type == "document":
            if str(meta.get("doc_id")) not in doc_ids:
                orphan_ids.append(vector_id)
        elif source_type == "knowledge_point":
            if str(meta.get("point_id")) not in point_ids:
                orphan_ids.append(vector_id)

    if not orphan_ids:
        return 0
    deleted = delete_vectors_by_ids(orphan_ids)
    logger.warning("已清理孤儿向量 %s 条（对应文档 / 知识点已不存在）", deleted)
    return deleted


def rebuild_vectors_for_embedding_model_change() -> tuple[int, int]:
    """embedding 模型变了：清空向量并按新模型重建全部知识库，返回（成功库数，失败库数）。"""
    from .vector_store import (
        get_embedding_model_name,
        record_embedding_model,
        recorded_embedding_model,
        reset_vector_store,
    )

    current = get_embedding_model_name()
    recorded = recorded_embedding_model()
    if recorded is None:
        # 标记文件在 CHROMA_DIR 里：有人按旧的报错提示删掉 data/chroma 时它也没了。
        # 这种「库没了但 DB 里文档还是 ready」的情况不能当全新安装放过去，
        # 否则页面上文档显示就绪、检索却什么都搜不到。
        from ..models import Document

        with SessionLocal() as db:
            ready_docs = int(
                db.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.status == "ready", Document.is_seed == 0)
                )
                or 0
            )
        if ready_docs == 0:
            # 全新安装：没有旧向量，也没有可比对的模型，直接记下当前模型即可
            record_embedding_model(current)
            return 0, 0
        logger.warning(
            "向量库缺少 embedding 模型标记，但数据库里有 %s 份就绪文档，按当前模型 %s 重建",
            ready_docs,
            current,
        )
        recorded = f"(unknown, {ready_docs} docs pending)"
    if recorded == current:
        return 0, 0

    logger.warning(
        "embedding 模型已变化（%s → %s），开始重建向量库（旧向量在新语义空间里不可用）",
        recorded,
        current,
    )
    reset_vector_store()

    ok = failed = 0
    with SessionLocal() as db:
        bases = list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.id)))
        for base in bases:
            try:
                failed_points, failed_docs = rebuild_base_vectors(
                    db, base.id, base.name, base.vector_key
                )
            except Exception:  # noqa: BLE001
                failed += 1
                logger.exception("重建知识库向量失败 base_id=%s course=%s", base.id, base.name)
                continue
            if failed_points or failed_docs:
                failed += 1
                logger.error(
                    "重建知识库 %s 未完全成功：知识点失败 %s 条，文档失败 %s 条",
                    base.name,
                    failed_points,
                    failed_docs,
                )
            else:
                ok += 1

    if not bases:
        ok = 1  # 没有知识库也算对齐（空库）
    if failed == 0:
        record_embedding_model(current)
        logger.info("向量库已按新模型 %s 重建完成", current)
    else:
        logger.error("向量库重建未完成（失败 %s 个知识库），保留旧模型标记，下次启动重试", failed)
    return ok, failed


def run_startup_maintenance() -> None:
    """启动维护：对齐 embedding 模型、清理孤儿向量。失败只记日志，不阻断启动。"""
    try:
        rebuild_vectors_for_embedding_model_change()
    except Exception:  # noqa: BLE001
        logger.exception("embedding 模型对齐失败，向量库可能与当前模型不一致")
    try:
        sweep_orphan_vectors()
    except Exception:  # noqa: BLE001
        logger.exception("孤儿向量清理失败")
