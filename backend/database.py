"""数据库引擎与会话管理。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

settings.ensure_dirs()

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等），并对旧库补列。"""
    from . import models  # noqa: F401  确保模型已注册

    models.Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)"))]
        if "avatar" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN avatar VARCHAR(255) DEFAULT ''"))
        # unanswered_questions 增量迁移：为旧库补充闭环增强所需字段
        uq_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(unanswered_questions)"))]
        if "related_points" not in uq_cols:
            conn.execute(text("ALTER TABLE unanswered_questions ADD COLUMN related_points TEXT DEFAULT '[]'"))
        if "answer" not in uq_cols:
            conn.execute(text("ALTER TABLE unanswered_questions ADD COLUMN answer TEXT DEFAULT ''"))
        if "answered_at" not in uq_cols:
            conn.execute(text("ALTER TABLE unanswered_questions ADD COLUMN answered_at DATETIME"))
        if "read_at" not in uq_cols:
            conn.execute(text("ALTER TABLE unanswered_questions ADD COLUMN read_at DATETIME"))
        if "updated_at" not in uq_cols:
            conn.execute(text("ALTER TABLE unanswered_questions ADD COLUMN updated_at DATETIME"))
        # conversations 增量迁移：记录会话使用的知识库（历史会话按库筛选）
        conv_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(conversations)"))]
        if "knowledge_bases" not in conv_cols:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN knowledge_bases TEXT DEFAULT '[]'"))
