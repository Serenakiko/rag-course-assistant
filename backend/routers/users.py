"""用户管理路由：管理员全局管理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import User
from ..schemas import AdminUserUpdate, UserOut
from ..security import hash_password

router = APIRouter()


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
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
    if payload.role is not None:
        if target.id == admin.id and payload.role != "admin":
            raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
        target.role = payload.role
    if payload.real_name is not None:
        target.real_name = payload.real_name.strip() or target.real_name
    if payload.new_password:
        target.password_hash = hash_password(payload.new_password)
    db.commit()
    db.refresh(target)
    return target


@router.delete("/admin/users/{user_id}", summary="删除用户（管理员）")
def admin_delete_user(
    user_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(target)
    db.commit()
    return {"ok": True}
