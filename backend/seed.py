"""初始化数据：默认管理员账号 + 四门课程知识库与知识点（向量化失败自动回滚，不阻塞启动）。

知识库（课程）：
  - 数据结构
  - 计算机组成原理
  - 操作系统
  - 计算机网络
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from .database import SessionLocal
from .models import KnowledgeBase, KnowledgePoint
from .seed_data import SAMPLE_BASES, SAMPLE_POINTS
from .services.admin_security import create_default_admin
from .services.vector_store import add_knowledge_point

logger = logging.getLogger(__name__)


def init_seed() -> None:
    """幂等初始化：管理员账号 + 四门课程知识库与知识点。"""
    session = SessionLocal()
    try:
        # 1. 默认管理员
        admin = create_default_admin(session)

        # 2. 四门课程知识库（按名称幂等创建）
        for item in SAMPLE_BASES:
            if session.scalar(select(KnowledgeBase).where(KnowledgeBase.name == item["name"])):
                continue
            session.add(
                KnowledgeBase(
                    name=item["name"],
                    description=item["description"],
                    created_by=admin.id,
                )
            )
        session.commit()

        # 3. 示例知识点（按标题幂等创建；向量化失败回滚该条）
        for item in SAMPLE_POINTS:
            if session.scalar(
                select(KnowledgePoint).where(KnowledgePoint.title == item["title"])
            ):
                continue
            p = KnowledgePoint(
                title=item["title"],
                content=item["content"],
                course=item["course"],
                tags=item["tags"],
                created_by=admin.id,
            )
            session.add(p)
            session.commit()
            session.refresh(p)
            try:
                add_knowledge_point(p.id, p.title, p.content, p.course, p.tags)
            except Exception:  # noqa: BLE001
                logger.warning("知识点「%s」写入向量库失败，已回滚（请确认 Ollama 已启动并拉取向量模型）", p.title)
                session.delete(p)
                session.commit()
    finally:
        session.close()
