"""用户管理路由：管理员全局管理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import KnowledgeBase, User
from ..schemas import AdminUserUpdate, UserOut
from ..security import hash_password

router = APIRouter()


class AdminUserCreate(BaseModel):
    # 和注册同规则：账号固定 8 位数字
    username: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")
    password: str = Field(min_length=6, max_length=64)
    real_name: str = Field(default="", max_length=32)
    role: str = Field(default="student", pattern=r"^(student|admin)$")


@router.get("/admin/users", response_model=list[UserOut], summary="全部用户（管理员）")
def admin_list_users(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("/admin/users", response_model=UserOut, summary="新增用户（管理员）")
def admin_create_user(
    payload: AdminUserCreate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> User:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    exists = db.scalar(select(User).where(User.username == username))
    if exists is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        real_name=payload.real_name.strip(),
        role=payload.role,
    )
    db.add(user)
    db.commit()
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
    changed_auth = False
    if payload.role is not None:
        if target.id == admin.id and payload.role != "admin":
            raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
        if target.role != payload.role:
            changed_auth = True
        target.role = payload.role
    if payload.real_name is not None:
        target.real_name = payload.real_name.strip() or target.real_name
    if payload.new_password:
        target.password_hash = hash_password(payload.new_password)
        changed_auth = True
    if changed_auth:
        target.token_version = int(target.token_version or 0) + 1
    db.commit()
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

    owned = list(
        db.scalars(select(KnowledgeBase).where(KnowledgeBase.created_by == target.id))
    )
    if owned and transfer_to is None:
        names = "、".join(b.name for b in owned[:5]) + (" 等" if len(owned) > 5 else "")
        raise HTTPException(
            status_code=400,
            detail=(
                f"该用户名下有 {len(owned)} 个知识库（{names}），"
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
        for base in owned:
            base.created_by = heir.id

    db.delete(target)
    db.commit()
    return {"ok": True, "transferred_bases": len(owned)}
