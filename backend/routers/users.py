"""用户管理路由：管理员全局管理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import Conversation, KnowledgeBase, Message, User
from ..schemas import AdminUserUpdate, UserOut
from ..security import hash_password

router = APIRouter()


class AdminUserCreate(BaseModel):
    # 和注册同规则：账号固定 8 位数字
    username: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")
    password: str = Field(min_length=6, max_length=64)
    real_name: str = Field(default="", max_length=32)
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
        like = f"%{keyword}%"
        if field == "name":
            conditions.append(User.real_name.like(like))
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
            conditions.append(User.username.like(like))

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
        {"id": u.id, "username": u.username, "real_name": u.real_name, "role": u.role}
        for u in rows
    ]


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
