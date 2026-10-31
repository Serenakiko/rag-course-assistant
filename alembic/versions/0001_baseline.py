"""基线迁移：以当前 models.py 的完整 schema 作为版本起点。

``upgrade`` 直接按 ORM 元数据建全量表（等价于 database.init_db 的 create_all），
``downgrade`` 全部删除。此迁移不承载「旧库增量补列」——那是 database.init_db
里手写 PRAGMA 的职责（历史遗留库的平滑升级），两者配合使用：

  1. 启动时 init_db 先 create_all + 补列，把任意旧库对齐到当前模型；
  2. 再用 Alembic 把库 stamp 到本基线，之后的新变更都走版本化迁移。

Revision ID: 0001
Revises: None
"""
from __future__ import annotations

from alembic import op

from backend.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
