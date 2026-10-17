"""数据库引擎与会话管理。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

settings.ensure_dirs()

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """SQLite 默认不校验外键，删用户/删会话会留下孤儿消息，这里显式打开。

    WAL 让读写不互相阻塞，busy_timeout 给并发写留重试时间，避免直接
    抛 database is locked。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


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
        if "token_version" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 0"))
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
        if "display_no" not in uq_cols:
            conn.execute(
                text("ALTER TABLE unanswered_questions ADD COLUMN display_no INTEGER DEFAULT 0")
            )
        # 旧数据回填：先按主键排一遍，之后删除时只重排这一列
        conn.execute(
            text(
                "UPDATE unanswered_questions SET display_no = id "
                "WHERE display_no IS NULL OR display_no = 0"
            )
        )
        # documents 增量迁移：标记种子展示文档，重建向量时跳过
        doc_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(documents)"))]
        if "is_seed" not in doc_cols:
            conn.execute(text("ALTER TABLE documents ADD COLUMN is_seed INTEGER DEFAULT 0"))
        # 后台解析任务不会跨进程存活：启动时把上次残留的“解析中”标成失败，
        # 否则文档会永远停在 processing，前端也一直轮询。
        conn.execute(
            text(
                "UPDATE documents SET status = 'failed', "
                "error = '服务重启，解析中断，请重新上传' "
                "WHERE status = 'processing'"
            )
        )
        # conversations 增量迁移：记录会话使用的知识库（历史会话按库筛选）
        conv_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(conversations)"))]
        if "knowledge_bases" not in conv_cols:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN knowledge_bases TEXT DEFAULT '[]'"))
