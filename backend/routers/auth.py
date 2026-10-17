"""认证路由：注册 / 登录 / 个人信息。"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    UpdateProfileRequest,
    UserOut,
)
from ..security import create_access_token, hash_password, verify_password

router = APIRouter()

AVATAR_DIR = Path(__file__).resolve().parent.parent / "static" / "avatars"
AVATAR_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
AVATAR_MAX_SIZE = 2 * 1024 * 1024
AVATAR_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}


def _looks_like_image(data: bytes, ext: str) -> bool:
    """按文件头判断，光看扩展名挡不住把任意文件改名成 .png 上传。"""
    if ext == "webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return any(data.startswith(sig) for sig in AVATAR_SIGNATURES.get(ext, ()))


# ---------- 登录失败限流 ----------
# 账号是 8 位数字、密码下限 6 位，不设限的话可以慢慢试出来。
# 按「IP + 账号」记失败时间戳，窗口内失败 10 次就锁到窗口结束；成功登录即清零。
_LOGIN_WINDOW = 600.0
_LOGIN_MAX_FAILS = 10
_login_fails: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _login_key(request: Request, username: str) -> str:
    client = request.client.host if request.client else "-"
    return f"{client}|{username}"


def _recent_fails(key: str) -> int:
    now = time.monotonic()
    with _login_lock:
        stamps = [t for t in _login_fails.get(key, []) if now - t < _LOGIN_WINDOW]
        if stamps:
            _login_fails[key] = stamps
        else:
            _login_fails.pop(key, None)
        return len(stamps)


def _note_fail(key: str) -> None:
    with _login_lock:
        _login_fails.setdefault(key, []).append(time.monotonic())


def _clear_fails(key: str) -> None:
    with _login_lock:
        _login_fails.pop(key, None)


@router.post("/register", response_model=UserOut, summary="注册（学生）")
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    exists = db.scalar(select(User).where(User.username == payload.username))
    if exists:
        raise HTTPException(status_code=400, detail="账号已存在")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        real_name=payload.real_name.strip() or payload.username,
        role="student",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse, summary="登录")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    key = _login_key(request, payload.username)
    if _recent_fails(key) >= _LOGIN_MAX_FAILS:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请 10 分钟后再试")
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        _note_fail(key)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _clear_fails(key)
    token = create_access_token(user.id, user.username, user.role)
    return LoginResponse(token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut, summary="当前用户信息")
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/change-password", summary="修改密码")
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}


@router.put("/profile", response_model=UserOut, summary="更新个人资料")
def update_profile(
    payload: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if payload.username is not None:
        new_username = payload.username.strip()
        if new_username and new_username != user.username:
            exists = db.scalar(select(User.id).where(User.username == new_username))
            if exists:
                raise HTTPException(status_code=400, detail="该账号已被占用")
            user.username = new_username
    if payload.real_name is not None:
        user.real_name = payload.real_name.strip() or user.real_name
    if payload.avatar is not None:
        user.avatar = payload.avatar.strip()
    db.commit()
    db.refresh(user)
    return user


@router.post("/avatar", response_model=UserOut, summary="上传头像")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in AVATAR_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 png/jpg/jpeg/gif/webp 图片")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > AVATAR_MAX_SIZE:
        raise HTTPException(status_code=400, detail="图片不能超过 2MB")
    if not _looks_like_image(data, ext):
        raise HTTPException(status_code=400, detail="文件内容不是有效的图片")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    name = f"u{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    (AVATAR_DIR / name).write_bytes(data)
    user.avatar = f"/static/avatars/{name}"
    db.commit()
    db.refresh(user)
    return user
