"""向量清理任务的启动重试。"""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..database import SessionLocal
from ..models import VectorCleanupTask, utcnow
from .vector_store import delete_knowledge_base_vectors

logger = logging.getLogger(__name__)


def retry_pending_vector_cleanups(limit: int = 20) -> int:
    """重试上次未完成的向量删除；返回本轮成功数。"""
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
            task.attempts = int(task.attempts or 0) + 1
            task.updated_at = utcnow()
            try:
                delete_knowledge_base_vectors(
                    task.knowledge_base_id, task.course_name
                )
            except Exception as exc:  # noqa: BLE001
                task.last_error = str(exc)[:1000]
                logger.warning(
                    "向量清理任务重试失败 task_id=%s course=%s",
                    task.id,
                    task.course_name,
                )
            else:
                task.status = "done"
                task.last_error = ""
                completed += 1
            db.commit()
    return completed
