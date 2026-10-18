"""知识库可见性规则（浏览和问答检索共用）。

  - 管理员：不限制
  - 学生：管理员发布的 + 自己创建的

只在列表接口里过滤、不在检索里过滤，会出现「列表里看不到、提问却能问出来」的越权，
所以两边必须走同一套规则。
"""
from __future__ import annotations

from fastapi import HTTPException
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




def all_course_names(db: Session) -> set[str]:
    """所有出现过的课程名：知识库名 + 只存在于知识点上的历史课程名。"""
    names = all_base_names(db)
    names |= {c for c in db.scalars(select(KnowledgePoint.course).distinct()) if c}
    return names


def visible_course_names(user: User, db: Session) -> set[str] | None:
    """当前用户可见的课程名（含历史课程）；管理员返回 None，表示不限制。

    历史课程（课程名不对应任何知识库）也按创建者判断：管理员留下的对所有学生可见，
    学生自己建的只有他自己能看到。只按知识库过滤的话，学生 A 以前留下的
    "某某的私藏" 这种课程名会出现在学生 B 的课程列表里。
    """
    visible = visible_base_names(user, db)
    if visible is None:
        return None
    known = all_base_names(db)
    legacy = set(
        db.scalars(
            select(KnowledgePoint.course)
            .distinct()
            .where(
                KnowledgePoint.course != "",
                KnowledgePoint.course.notin_(known),
                or_(
                    KnowledgePoint.created_by == user.id,
                    KnowledgePoint.created_by.in_(admin_user_ids()),
                ),
            )
        )
    )
    return visible | legacy


def hidden_course_names(user: User, db: Session) -> set[str]:
    """当前用户看不到的课程名（知识库 + 历史课程）；空集合表示无需过滤。"""
    visible = visible_course_names(user, db)
    if visible is None:
        return set()
    return all_course_names(db) - visible


def point_scope_condition(user: User, db: Session):
    """知识点行级可见性条件；管理员返回 None（不限制）。

    课程名可见还不够：学生把课程留空（"未分类"）同样会把内容暴露给所有人，
    所以未分类的知识点只对创建者本人和管理员留下的那部分可见。
    """
    if user.role == "admin":
        return None
    hidden = hidden_course_names(user, db)
    conditions = [KnowledgePoint.course.notin_(hidden)] if hidden else []
    conditions.append(
        or_(
            KnowledgePoint.course != "",
            KnowledgePoint.created_by == user.id,
            KnowledgePoint.created_by.in_(admin_user_ids()),
        )
    )
    return conditions


def can_view_point(user: User, db: Session, point) -> bool:
    """单条知识点的可见性判断（与 point_scope_condition 同一套规则）。"""
    if user.role == "admin":
        return True
    if point.course:
        return point.course not in hidden_course_names(user, db)
    if point.created_by == user.id:
        return True
    return point.created_by in set(db.scalars(admin_user_ids()))


def allowed_course_names(user: User, db: Session) -> list[str] | None:
    """问答检索允许命中的课程名；None 表示不限制（管理员）。

    返回空列表表示「没有可检索的范围」，调用方要按「知识库为空」处理。
    """
    visible = visible_course_names(user, db)
    return None if visible is None else sorted(visible)


def assert_writable_course(user: User, db: Session, course: str) -> None:
    """校验写权限：必须选一个已存在的知识库，学生只能写自己可见的库。

    新增/编辑知识点、上传文档都要走这里。不校验的话，学生随手填一个课程名就能
    把内容塞进别的学生的私有知识库，或者造出一个「不属于任何知识库」的课程名。
    空课程以前是放行的，但学生的检索范围只含知识库名，course 为空的资料
    永远问不出来，所以现在直接 400，强制先建库/选库。
    """
    name = (course or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请选择所属知识库")
    if name not in all_base_names(db):
        raise HTTPException(status_code=400, detail="所属知识库不存在，请先创建知识库")
    if user.role == "admin":
        return
    # 能看见 ≠ 能往里写：以前按「可见」放行，一个学生就能往管理员发布的公共库
    # 里塞知识点或文档，别人的回答也会引用这条内容。现在只允许创建者本人。
    owner_id = db.scalar(select(KnowledgeBase.created_by).where(KnowledgeBase.name == name))
    if owner_id != user.id:
        raise HTTPException(status_code=403, detail="只有知识库创建者或管理员能往里添加内容")
