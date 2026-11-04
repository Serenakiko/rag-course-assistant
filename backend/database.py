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
    """建表（幂等），并对旧库补列 / 重命名列。"""
    from . import models  # noqa: F401  确保模型已注册

    # 列重命名迁移（先于 create_all）：把历史库里的旧列名对齐到当前模型。
    # 早期 users 表用 username=账号、real_name=用户名；现统一为 account=账号、
    # username=用户名。SQLite 支持 RENAME COLUMN，这里按「旧列存在且新列不存在」逐列处理，
    # 顺序很重要：先把 username(账号) 重命名为 account，再把 real_name 重命名为 username，
    # 避免中间态列名冲突。
    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)"))]
        if "account" not in cols and "username" in cols:
            conn.execute(text("ALTER TABLE users RENAME COLUMN username TO account"))
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)"))]
        if "username" not in cols and "real_name" in cols:
            conn.execute(text("ALTER TABLE users RENAME COLUMN real_name TO username"))

    models.Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)"))]
        if "avatar" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN avatar VARCHAR(255) DEFAULT ''"))
        # username 唯一性：SQLite 无法给已有列直接加 UNIQUE 约束，用唯一索引等价实现。
        # create_all 对已存在的表不会重建列约束，所以这里对旧库手动补唯一索引，
        # 与模型里的 unique=True 语义对齐（应用层预检之外的并发兜底）。
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username "
                "ON users (username)"
            )
        )
        # 索引名对齐：字段重命名（username→account）后，旧库遗留的 ix_users_username
        # 实际指向 account 列，名称有误导。这里删旧建新为 ix_users_account。
        idx_list = [row[1] for row in conn.execute(text("PRAGMA index_list(users)"))]
        if "ix_users_username" in idx_list and "ix_users_account" not in idx_list:
            conn.execute(text("DROP INDEX ix_users_username"))
            conn.execute(
                text("CREATE INDEX ix_users_account ON users (account)")
            )
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
        if "admin_read_at" not in uq_cols:
            conn.execute(text("ALTER TABLE unanswered_questions ADD COLUMN admin_read_at DATETIME"))
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
        # (question, created_by) 唯一约束：先清掉历史竞态可能留下的重复行，再建唯一索引。
        # 保留每组里 id 最小（最早）的一条，删除其余重复；否则唯一索引建不起来。
        conn.execute(
            text(
                "DELETE FROM unanswered_questions WHERE id NOT IN ("
                "  SELECT MIN(id) FROM unanswered_questions "
                "  WHERE created_by IS NOT NULL "
                "  GROUP BY question, created_by"
                ") AND created_by IS NOT NULL"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_unanswered_question_user "
                "ON unanswered_questions (question, created_by)"
            )
        )
        # documents 增量迁移：标记种子展示文档，重建向量时跳过
        doc_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(documents)"))]
        if "is_seed" not in doc_cols:
            conn.execute(text("ALTER TABLE documents ADD COLUMN is_seed INTEGER DEFAULT 0"))
        # 后台解析任务不会跨进程存活：启动时把上次残留的「解析中」标成失败，
        # 否则文档会永远停在 processing，前端也一直轮询。
        # 但热重载 / 多 worker 下，新进程启动时旧进程可能还在正常解析——无条件改 failed
        # 会把正在解析的文档误杀，几秒后旧进程又改回 ready，状态来回跳、前端误弹「失败」。
        # 所以只改「创建时间早于 30 分钟」的 processing 记录：正常解析几秒~几分钟内完成，
        # 残留这么久的一定是进程已死、任务丢失的孤儿。
        conn.execute(
            text(
                "UPDATE documents SET status = 'failed', "
                "error = '服务重启，解析中断，请重新上传' "
                "WHERE status = 'processing' "
                "AND created_at < datetime('now', '-30 minutes')"
            )
        )
        # conversations 增量迁移：生成序号（同会话新提问顶掉旧流）
        conv_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(conversations)"))]
        if "gen_seq" not in conv_cols:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN gen_seq INTEGER DEFAULT 0"))
        # messages 增量迁移：标记生成中断时保存的半截回答
        msg_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(messages)"))]
        if "interrupted" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN interrupted INTEGER DEFAULT 0"))
        if "feedback" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN feedback VARCHAR(8) DEFAULT ''"))
        # 这一轮是否拒答：1=拒答。统计拒答率直接读这个字段，不用中文文案 LIKE。
        if "rejected" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN rejected INTEGER DEFAULT 0"))
        # knowledge_points 增量迁移：文档自动生成的知识点标记
        kp_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(knowledge_points)"))]
        if "is_auto" not in kp_cols:
            conn.execute(text("ALTER TABLE knowledge_points ADD COLUMN is_auto INTEGER DEFAULT 0"))
        if "updated_by" not in kp_cols:
            conn.execute(text("ALTER TABLE knowledge_points ADD COLUMN updated_by INTEGER"))
