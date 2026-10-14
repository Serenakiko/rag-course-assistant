"""知识库可见性规则（浏览和问答检索共用）。

  - 管理员：不限制
  - 学生：管理员发布的 + 自己创建的

只在列表接口里过滤、不在检索里过滤，会出现「列表里看不到、提问却能问出来」的越权，
所以两边必须走同一套规则。
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import KnowledgeBase, KnowledgePoint, User


def admin_user_ids():
    """所有管理员的 id（子查询），用来判断知识库是不是管理员发布的。"""
    return select(User.id).where(User.role == "admin")


def all_base_names(db: Session) -> set[str]:
    return set(db.scalars(select(KnowledgeBase.name)))


def visible_base_names(user: User, db: Session) -> set[str] | None:
    """当前用户可见的知识库名称；管理员返回 None，表示不限制。

    管理员：全部（收集所有人的知识库）
    学生：管理员发布的 + 自己创建的
    """
    if user.role == "admin":
        return None
    return set(
        db.scalars(
            select(KnowledgeBase.name).where(
                or_(
                    KnowledgeBase.created_by == user.id,
                    KnowledgeBase.created_by.in_(admin_user_ids()),
                )
            )
        )
    )


def hidden_base_names(user: User, db: Session) -> set[str]:
    """当前用户看不到的知识库名称；空集合表示无需过滤。"""
    visible = visible_base_names(user, db)
    if visible is None:
        return set()
    return all_base_names(db) - visible


def allowed_course_names(user: User, db: Session) -> list[str] | None:
    """问答检索允许命中的课程名；None 表示不限制（管理员）。

    学生的范围 = 可见知识库 + 不属于任何知识库的历史课程（兼容旧数据）。
    返回空列表表示「没有可检索的范围」，调用方要按「知识库为空」处理。
    """
    visible = visible_base_names(user, db)
    if visible is None:
        return None
    known = all_base_names(db)
    legacy = {
        c
        for c in db.scalars(select(KnowledgePoint.course).distinct())
        if c and c not in known
    }
    return sorted(visible | legacy)
