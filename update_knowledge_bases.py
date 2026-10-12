"""知识库数据迁移脚本（幂等，可重复运行）。

功能：
  1. 删除旧的示例知识点（AI 通识 3 条），并清理其在向量库中的索引；
  2. 创建「数据结构 / 计算机组成原理 / 操作系统 / 计算机网络」四个知识库；
  3. 批量写入四门课程的知识点并向量化；
  4. 兼容历史数据：已有知识点的课程若未建知识库，自动补建。

用法（建议先停止后端服务，避免 SQLite 写锁冲突）：
    python update_knowledge_bases.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import func, select  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402
from backend.models import KnowledgeBase, KnowledgePoint  # noqa: E402
from backend.seed import SAMPLE_BASES, SAMPLE_POINTS  # noqa: E402
from backend.services.vector_store import delete_knowledge_point  # noqa: E402

# 旧版示例知识点标题（AI 通识）
LEGACY_POINT_TITLES = {
    "什么是 RAG（检索增强生成）",
    "向量检索与相似度计算",
    "ChromaDB 向量数据库",
}


def main() -> None:
    init_db()  # 确保 knowledge_bases 表存在
    session = SessionLocal()
    try:
        # 1. 删除旧示例知识点（SQLite + 向量）
        legacy = list(
            session.scalars(
                select(KnowledgePoint).where(KnowledgePoint.title.in_(LEGACY_POINT_TITLES))
            )
        )
        for p in legacy:
            delete_knowledge_point(p.id)
            session.delete(p)
        session.commit()
        if legacy:
            print(f"[1/4] 已删除旧示例知识点 {len(legacy)} 条（含向量索引）")
        else:
            print("[1/4] 无旧示例知识点需要删除")

        # 2. 创建四门课程知识库（按名称幂等）
        created_bases = 0
        for item in SAMPLE_BASES:
            exists = session.scalar(
                select(KnowledgeBase).where(KnowledgeBase.name == item["name"])
            )
            if exists is None:
                session.add(
                    KnowledgeBase(
                        name=item["name"],
                        description=item["description"],
                        created_by=None,
                    )
                )
                created_bases += 1
        session.commit()
        print(f"[2/4] 知识库就绪：{created_bases} 个新建，其余已存在")

        # 3. 写入四门课程知识点（按标题幂等）并向量化
        added = 0
        for item in SAMPLE_POINTS:
            exists = session.scalar(
                select(KnowledgePoint).where(KnowledgePoint.title == item["title"])
            )
            if exists is not None:
                continue
            p = KnowledgePoint(
                title=item["title"],
                content=item["content"],
                course=item["course"],
                tags=item["tags"],
                created_by=None,
            )
            session.add(p)
            session.commit()
            session.refresh(p)
            try:
                from backend.services.vector_store import add_knowledge_point

                add_knowledge_point(p.id, p.title, p.content, p.course, p.tags)
                added += 1
            except Exception as exc:  # noqa: BLE001
                print(f"    知识点「{p.title}」向量化失败，已回滚：{exc}")
                session.delete(p)
                session.commit()
        print(f"[3/4] 四门课程知识点入库：新增 {added} 条")

        # 4. 兼容历史数据：已有课程未建知识库时自动补建
        base_names = set(session.scalars(select(KnowledgeBase.name)))
        old_courses = session.execute(
            select(KnowledgePoint.course).distinct()
        ).all()
        patched = 0
        for (course,) in old_courses:
            if course and course not in base_names:
                session.add(KnowledgeBase(name=course, description="", created_by=None))
                base_names.add(course)
                patched += 1
        session.commit()
        if patched:
            print(f"[4/4] 已为历史课程自动补建 {patched} 个知识库")

        # 统计
        bases = session.scalar(select(func.count(KnowledgeBase.id))) or 0
        points = session.scalar(select(func.count(KnowledgePoint.id))) or 0
        print(f"---- 迁移完成：知识库 {bases} 个，知识点 {points} 条 ----")
    finally:
        session.close()


if __name__ == "__main__":
    main()
