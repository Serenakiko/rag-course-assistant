"""认证路由：注册 / 登录 / 个人信息。"""
from __future__ import annotations

import ipaddress
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from ..security import (
    _needs_upgrade,
    create_access_token,
    hash_password,
    verify_password,
)

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


def _avatar_file(avatar: str, user_id: int) -> Path | None:
    """把 /static/avatars/u{id}_xxx 映射到磁盘文件；别人的或非上传头像不处理。"""
    prefix = "/static/avatars/"
    if not avatar or not avatar.startswith(prefix):
        return None
    name = avatar[len(prefix):]
    if not name or "/" in name or "\\" in name or not name.startswith(f"u{user_id}_"):
        return None
    return AVATAR_DIR / name


def _delete_avatar_file(avatar: str, user_id: int) -> None:
    """删除被替换下来的头像文件；失败不影响主流程。"""
    path = _avatar_file(avatar, user_id)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
# 再加一层按 IP 的失败计数：只按「IP + 账号」记，换个账号名就绕过去了，
# 而账号只有 8 位数字，逐个号试是可行的。窗口相同，阈值放宽一些。
_IP_MAX_FAILS = 30
_login_ip_fails: dict[str, list[float]] = {}
# 账号不存在时也算一次哈希，否则「立刻返回」本身就是账号不存在的信号
_DUMMY_HASH = hash_password("timing-only-dummy-password")
# 注册限流：同一 IP 10 分钟最多 5 次，防止脚本批量刷账号
_REG_WINDOW = 600.0
_REG_MAX = 5
_reg_log: dict[str, list[float]] = {}
_login_lock = threading.Lock()
_last_prune = 0.0
_PRUNE_INTERVAL = 60.0


def _prune_expired(now: float) -> None:
    """清掉窗口外的限流记录。

    只按 key 懒清理的话，换 IP / 换账号试过一轮，旧 key 就永远留在内存里。
    每 60 秒扫一次，调用方必须已经持有 _login_lock。
    """
    global _last_prune
    if now - _last_prune < _PRUNE_INTERVAL:
        return
    _last_prune = now
    for store, window in (
        (_login_fails, _LOGIN_WINDOW),
        (_login_ip_fails, _LOGIN_WINDOW),
        (_reg_log, _REG_WINDOW),
    ):
        expired = [k for k, stamps in store.items() if not any(now - t < window for t in stamps)]
        for key in expired:
            del store[key]


def _is_trusted_proxy(host: str) -> bool:
    """直连方是否为本机/内网代理（cpolar、nginx 等）。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("localhost",)
    # IPv4-mapped IPv6（::ffff:127.0.0.1）按 IPv4 判断
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private


def _client_ip(request: Request) -> str:
    """取客户端真实 IP。

    反向代理 / cpolar 隧道下，request.client.host 是 127.0.0.1，所有公网用户会被
    当成同一个 IP，限流会误伤全班。只有直连方确实是本机/内网代理时才信任
    X-Forwarded-For；公网客户端直连时伪造该头会被忽略，无法绕过 IP 限流。

    取最后一个合法 IP：代理通常把真实客户端追加到末尾，这样客户端自带的伪造
    前缀不会生效。
    """
    peer = request.client.host if request.client else "-"
    if not _is_trusted_proxy(peer):
        return peer
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        candidates = [p.strip() for p in xff.split(",") if p.strip()]
        for candidate in reversed(candidates):
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                continue
    return peer


def _login_key(request: Request, account: str) -> str:
    client = _client_ip(request)
    return f"{client}|{account}"


def _recent_fails(key: str) -> int:
    now = time.monotonic()
    with _login_lock:
        _prune_expired(now)
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


def _recent_ip_fails(client: str) -> int:
    """同一 IP 在窗口内的登录失败次数。"""
    now = time.monotonic()
    with _login_lock:
        _prune_expired(now)
        stamps = [t for t in _login_ip_fails.get(client, []) if now - t < _LOGIN_WINDOW]
        if stamps:
            _login_ip_fails[client] = stamps
        else:
            _login_ip_fails.pop(client, None)
        return len(stamps)


def _note_ip_fail(client: str) -> None:
    with _login_lock:
        _login_ip_fails.setdefault(client, []).append(time.monotonic())


def _clear_ip_fails(client: str) -> None:
    with _login_lock:
        _login_ip_fails.pop(client, None)


@router.post("/register", response_model=UserOut, summary="注册（学生）")
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    client = _client_ip(request)
    now = time.monotonic()
    with _login_lock:
        _prune_expired(now)
        stamps = [t for t in _reg_log.get(client, []) if now - t < _REG_WINDOW]
        if len(stamps) >= _REG_MAX:
            raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")
        stamps.append(now)
        _reg_log[client] = stamps
    exists = db.scalar(select(User).where(User.account == payload.account))
    if exists:
        raise HTTPException(status_code=400, detail="账号已存在")
    username = payload.username.strip() or payload.account
    name_exists = db.scalar(select(User.id).where(User.username == username))
    if name_exists:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        account=payload.account,
        password_hash=hash_password(payload.password),
        username=username,
        role="student",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 并发下应用层预检被绕过时，唯一索引兜底拦截（account 或 username 撞库）
        db.rollback()
        raise HTTPException(status_code=400, detail="账号或用户名已存在")
    db.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse, summary="登录")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    client = _client_ip(request)
    key = _login_key(request, payload.account)
    # 先验密，再按失败次数限流：限流必须在「确认这是一次失败尝试」之后，
    # 否则隧道下共用一个 IP 时，恶意方连错 10 次就能把别人正确的密码也挡在门外。
    user = db.scalar(select(User).where(User.account == payload.account))
    if user is None:
        # 账号不存在也走一遍哈希，抹平「不存在的号返回更快」这个信号
        verify_password(payload.password, _DUMMY_HASH)
        if _recent_fails(key) >= _LOGIN_MAX_FAILS or _recent_ip_fails(client) >= _IP_MAX_FAILS:
            raise HTTPException(status_code=429, detail="登录失败次数过多，请 10 分钟后再试")
        _note_fail(key)
        _note_ip_fail(client)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not verify_password(payload.password, user.password_hash):
        if _recent_fails(key) >= _LOGIN_MAX_FAILS or _recent_ip_fails(client) >= _IP_MAX_FAILS:
            raise HTTPException(status_code=429, detail="登录失败次数过多，请 10 分钟后再试")
        _note_fail(key)
        _note_ip_fail(client)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    _clear_fails(key)
    _clear_ip_fails(client)
    # 旧哈希（低迭代次数）登录成功后自动升级到当前强度，静默重哈希
    if _needs_upgrade(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()
    token = create_access_token(
        user.id, user.account, user.role, user.token_version or 0
    )
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
    # 让所有已签发的令牌失效，避免旧 token 在有效期内继续可用
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    return {"ok": True}


@router.put("/profile", response_model=UserOut, summary="更新个人资料")
def update_profile(
    payload: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if payload.account is not None:
        new_account = payload.account.strip()
        if new_account and new_account != user.account:
            exists = db.scalar(select(User.id).where(User.account == new_account))
            if exists:
                raise HTTPException(status_code=400, detail="账号已存在")
            user.account = new_account
    if payload.username is not None:
        new_username = payload.username.strip() or user.username
        if new_username and new_username != user.username:
            name_exists = db.scalar(
                select(User.id).where(User.username == new_username)
            )
            if name_exists:
                raise HTTPException(status_code=400, detail="用户名已存在")
        user.username = new_username
    old_avatar = user.avatar
    if payload.avatar is not None:
        user.avatar = payload.avatar.strip()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="账号或用户名已存在")
    db.refresh(user)
    # 数据库落定后再删旧文件：提交失败时头像字段还指向它，删了就成了破图
    if old_avatar and old_avatar != user.avatar:
        _delete_avatar_file(old_avatar, user.id)
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
    if (file.size or 0) > AVATAR_MAX_SIZE:
        raise HTTPException(status_code=400, detail="图片不能超过 2MB")
    # 分块读，超过上限立刻中止：一次性 read() 会先把超大文件整个读进内存
    data = bytearray()
    while chunk := await file.read(64 * 1024):
        data.extend(chunk)
        if len(data) > AVATAR_MAX_SIZE:
            raise HTTPException(status_code=400, detail="图片不能超过 2MB")
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    data = bytes(data)
    if not _looks_like_image(data, ext):
        raise HTTPException(status_code=400, detail="文件内容不是有效的图片")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    name = f"u{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    (AVATAR_DIR / name).write_bytes(data)
    old_avatar = user.avatar
    user.avatar = f"/static/avatars/{name}"
    db.commit()
    db.refresh(user)
    if old_avatar and old_avatar != user.avatar:
        _delete_avatar_file(old_avatar, user.id)
    return user
