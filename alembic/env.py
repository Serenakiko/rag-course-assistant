"""Alembic 迁移环境：复用项目的 SQLAlchemy 模型与配置。

数据库 URL 来自 backend.config.settings.DB_PATH（即 data/app.db），
目标元数据来自 backend.models.Base，autogenerate 据此对比模型与库的差异。
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# 让 alembic 能 import backend 包（项目根目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.models import Base  # noqa: E402

config = context.config

# 从项目配置注入 SQLite 路径，而不是读 alembic.ini 里的占位 URL
config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.DB_PATH}")

if config.config_file_name is not None:
    # 注意：fileConfig 会重置整个 logging 系统（关闭 uvicorn 的 handler），
    # 在 uvicorn 的 async lifespan 里执行会干扰启动。这里仅用于命令行 alembic
    # 迁移时的日志，启动时 stamp 不需要它，改为 disable_existing_loggers=False
    # 也不够干净，干脆跳过（stamp 本身不依赖 logging 配置）。
    try:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    except Exception:
        pass

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite 的 ALTER 能力有限，用 batch 模式（重建表）执行迁移
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
