"""用户管理路由：管理员全局管理。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..constants import SYSTEM_ADMIN_ACCOUNT
from ..database import get_db
from ..deps import require_role
from ..models import (
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgePoint,
    Message,
    User,
)
from ..schemas import AdminUserUpdate, UserOut
from ..security import hash_password
from ..services.like import contains

router = APIRouter()

logger = logging.getLogger(__name__)

# 系统管理员账号：其他管理员不能修改或删除，避免误操作把超管降级


class AdminUserCreate(BaseModel):
    # 和注册同规则：账号固定 8 位数字，密码下限 8 位（与注册/改密一致）
    account: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")
    password: str = Field(min_length=8, max_length=64)
    username: str = Field(default="", max_length=32)
    role: str = Field(default="student", pattern=r"^(student|admin)$")


@router.get("/admin/users", summary="用户列表（管理员，分页）")
def admin_list_users(
    q: str = "",
    field: str = "account",
    limit: int = 20,
    offset: int = 0,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """服务端搜索 + 分页，列表页不再把全部用户拉回前端过滤。"""
    if field not in ("account", "name", "role"):
        raise HTTPException(status_code=400, detail="field 仅支持 account / name / role")
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    conditions = []
    keyword = q.strip()
    if keyword:
        if field == "name":
            conditions.append(contains(User.username, keyword))
        elif field == "role":
            # 支持“学生 / student / 管理员 / admin”，也支持只输入一个字
            key = keyword.lower()
            role_conds = []
            if "学" in keyword or "student" in key:
                role_conds.append(User.role == "student")
            if "管" in keyword or "admin" in key:
                role_conds.append(User.role == "admin")
            conditions.append(or_(*role_conds) if role_conds else User.id < 0)
        else:
            conditions.append(contains(User.account, keyword))

    total = db.scalar(select(func.count(User.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(User).where(*conditions).order_by(User.id).limit(limit).offset(offset)
    ).all()

    # 提问数：这一页用户各自的 user 消息条数（一次 group by，不逐行 count）
    ids = [u.id for u in rows]
    counts: dict[int, int] = {}
    if ids:
        counts = dict(
            db.execute(
                select(Conversation.user_id, func.count(Message.id))
                .join(Message, Message.conversation_id == Conversation.id)
                .where(Conversation.user_id.in_(ids), Message.role == "user")
                .group_by(Conversation.user_id)
            ).all()
        )
    items = []
    for u in rows:
        data = UserOut.model_validate(u).model_dump()
        data["question_count"] = int(counts.get(u.id, 0))
        items.append(data)
    return {"total": total, "items": items}


@router.get("/admin/users/options", summary="用户下拉选项（管理员）")
def admin_user_options(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    """转移知识库等场景只需要 id / 名字 / 角色，不必拉整张用户表。"""
    rows = db.scalars(select(User).order_by(User.id)).all()
    return [
        {"id": u.id, "account": u.account, "username": u.username, "role": u.role}
        for u in rows
    ]


@router.post("/admin/users", response_model=UserOut, summary="新增用户（管理员）")
def admin_create_user(
    payload: AdminUserCreate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> User:
    account = payload.account.strip()
    if not account:
        raise HTTPException(status_code=400, detail="账号不能为空")
    exists = db.scalar(select(User).where(User.account == account))
    if exists is not None:
        raise HTTPException(status_code=409, detail="账号已存在")
    # 与注册保持一致：空用户名回落到账号，避免第二个空用户名撞唯一索引
    username = payload.username.strip() or account
    name_exists = db.scalar(select(User.id).where(User.username == username))
    if name_exists is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        account=account,
        password_hash=hash_password(payload.password),
        username=username,
        role=payload.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="账号或用户名已存在")
    db.refresh(user)
    return user


@router.patch("/admin/users/{user_id}", response_model=UserOut, summary="修改用户（管理员）")
def admin_update_user(
    user_id: int,
    payload: AdminUserUpdate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.account == SYSTEM_ADMIN_ACCOUNT and target.id != admin.id:
        raise HTTPException(status_code=403, detail="系统管理员账号仅限本人修改")
    changed_auth = False
    if payload.role is not None:
        if target.account == SYSTEM_ADMIN_ACCOUNT and payload.role != "admin":
            raise HTTPException(status_code=400, detail="系统管理员的身份不可取消")
        if target.id == admin.id and payload.role != "admin":
            raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
        if target.role != payload.role:
            changed_auth = True
        target.role = payload.role
    if payload.username is not None:
        new_username = payload.username.strip() or target.username
        if new_username and new_username != target.username:
            name_exists = db.scalar(
                select(User.id).where(User.username == new_username)
            )
            if name_exists is not None:
                raise HTTPException(status_code=409, detail="用户名已存在")
        target.username = new_username
    if payload.new_password:
        target.password_hash = hash_password(payload.new_password)
        changed_auth = True
        # 管理员代设的密码是「临时密码」：本人下次登录必须先改成自己的密码。
        # 改自己的密码走 /api/auth/change-password，不走这里。
        if target.id != admin.id:
            target.must_change_password = 1
    if changed_auth:
        target.token_version = int(target.token_version or 0) + 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在")
    db.refresh(target)
    return target


@router.delete("/admin/users/{user_id}", summary="删除用户（管理员）")
def admin_delete_user(
    user_id: int,
    transfer_to: int | None = None,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """删除用户；名下还有知识库时必须先指定接收人。

    知识库的 created_by 是 ON DELETE SET NULL，直接删用户会留下无主知识库：
    学生可见性只认「管理员发布的 + 自己创建的」，无主库谁都看不到，
    知识点和文档却还在库里，等于资料凭空消失。
    """
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.account == SYSTEM_ADMIN_ACCOUNT:
        raise HTTPException(status_code=400, detail="系统管理员账号不能删除")

    owned = list(
        db.scalars(select(KnowledgeBase).where(KnowledgeBase.created_by == target.id))
    )
    if owned and transfer_to is None:
        names = "、".join(b.name for b in owned[:5]) + (" 等" if len(owned) > 5 else "")
        raise HTTPException(
            status_code=400,
            detail=(
                f"该用户下有 {len(owned)} 个知识库（{names}），"
                "删除后会变成无主知识库、学生端不可见。"
                "请先指定接收人转移，或先删除这些知识库。"
            ),
        )
    if owned:
        if transfer_to == target.id:
            raise HTTPException(status_code=400, detail="接收人不能是待删除的用户")
        heir = db.get(User, transfer_to)
        if heir is None:
            raise HTTPException(status_code=404, detail="知识库接收人不存在")
    elif transfer_to is not None:
        if transfer_to == target.id:
            raise HTTPException(status_code=400, detail="接收人不能是待删除的用户")
        heir = db.get(User, transfer_to)
        if heir is None:
            raise HTTPException(status_code=404, detail="接收人不存在")
    else:
        # 没指定接收人且名下没有知识库：把上传的文档 / 知识点交给兜底账号，
        # 优先系统管理员，其次当前操作的管理员。否则 uploaded_by 会被置 NULL，
        # 文档变成无主但仍可检索，等于留下孤儿数据。
        heir = db.scalar(select(User).where(User.account == SYSTEM_ADMIN_ACCOUNT))
        heir = heir or admin

    moved_docs = 0
    moved_points = 0
    try:
        for base in owned:
            base.created_by = heir.id
        # 批量改归属：用 SQL 侧 update，避免把这名学生的历史文档/知识点全量
        # 加载进 Session（毕业设计里可能有上千条）
        moved_docs = (
            db.execute(
                update(Document)
                .where(Document.uploaded_by == target.id)
                .values(uploaded_by=heir.id)
                .execution_options(synchronize_session=False)
            ).rowcount
            or 0
        )
        moved_points = (
            db.execute(
                update(KnowledgePoint)
                .where(KnowledgePoint.created_by == target.id)
                .values(created_by=heir.id)
                .execution_options(synchronize_session=False)
            ).rowcount
            or 0
        ) + (
            db.execute(
                update(KnowledgePoint)
                .where(KnowledgePoint.updated_by == target.id)
                .values(updated_by=heir.id)
                .execution_options(synchronize_session=False)
            ).rowcount
            or 0
        )
        db.delete(target)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        # 仍有外键/唯一约束挡住（例如删除过程中有人同时上传文档）时不抛 500，
        # 回滚后让管理员重试；转交已回滚，不会留下半更新的数据。
        db.rollback()
        logger.warning("删除用户失败：%s", exc)
        raise HTTPException(
            status_code=409,
            detail="删除失败，数据仍在被引用或并发修改，请重试",
        )
    return {
        "ok": True,
        "transferred_bases": len(owned),
        "transferred_docs": int(moved_docs),
        "transferred_points": int(moved_points),
    }
