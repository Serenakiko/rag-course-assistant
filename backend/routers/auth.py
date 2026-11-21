"""认证路由：注册 / 登录 / 个人信息。"""
from __future__ import annotations

import ipaddress
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..constants import SYSTEM_ADMIN_ACCOUNT
from ..database import get_db
from ..deps import ACCESS_TOKEN_COOKIE, get_current_user
from ..models import RevokedToken, User, utcnow
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
    decode_token,
    hash_password,
    verify_password,
)
from ..services.admin_security import remove_initial_password

router = APIRouter()


def _request_is_https(request: Request) -> bool:
    """判断本次请求是否来自 https，决定 Cookie 要不要带 Secure。

    反代（Nginx/Caddy）终止 TLS 后，后端看到的是 http，request.url.scheme
    永远是 http —— 直接用它判断会让线上 Cookie 丢掉 Secure。所以优先信任
    反代写入的 X-Forwarded-Proto，其次才是原始 scheme。
    COOKIE_SECURE 显式配置（"true"/"false"）优先于自动判断，方便自建反代
    没传这个头时手动兜底。
    """
    override = (settings.COOKIE_SECURE or "").strip().lower()
    if override in ("1", "true", "yes"):
        return True
    if override in ("0", "false", "no"):
        return False
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto:
        return proto == "https"
    return request.url.scheme == "https"


def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    """把 JWT 写入 HttpOnly 会话 Cookie。

    CSRF 防护说明：这里不额外引入 CSRF token，而是依赖
    1) SameSite=Lax（跨站 POST 不会带上 Cookie）；
    2) 所有写接口都要求 Content-Type: application/json（简单表单跨站提交无法伪造）；
    3) 前后端同源部署，静态页面与 API 使用同一站点。
    本地 http 部署不设置 Secure；https 部署按 X-Forwarded-Proto / scheme 自动加上，
    也可以用 COOKIE_SECURE 显式覆盖（反代没传头的情况）。
    """
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        max_age=settings.JWT_EXPIRE_HOURS * 3600,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_request_is_https(request),
    )


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


_AVATAR_PATH_PREFIX = "/static/avatars/"
# 只放行单个 emoji（含 ZWJ 组合、肤色、键帽、旗帜），不放行任意文本/HTML
_EMOJI_RE = re.compile(
    "^(?:"
    "[0-9#*]\ufe0f?\u20e3"
    "|[\U0001F1E6-\U0001F1FF]{2}"
    "|[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF"
    "\u2300-\u23FF\u00A9\u00AE\u2122\u203C\u2049\u2139\u3030\u303D\u3297\u3299]"
    "(?:\ufe0f|[\U0001F3FB-\U0001F3FF])*"
    ")(?:\u200d"
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF"
    "\u2300-\u23FF\u00A9\u00AE\u2122\u203C\u2049\u2139\u3030\u303D\u3297\u3299]"
    "(?:\ufe0f|[\U0001F3FB-\U0001F3FF])*)*$"
)


def _validate_avatar(avatar: str, user_id: int) -> str:
    """头像白名单：空 / 单个 emoji / 本站上传目录下**自己的**头像文件名。

    以前可直接写入任意文本（最多 255 字符），前端若漏转义就是一个存储型
    XSS 入口；顺手挡住 `../` 之类跑出头像目录的路径。
    上传文件名带 `u{id}_` 前缀，这里必须对得上当前用户，否则填别人的头像
    路径就能把别人的图挂到自己资料上（读取他人上传内容）。
    """
    value = (avatar or "").strip()
    if not value:
        return ""
    if len(value) > 255:
        raise HTTPException(status_code=400, detail="头像最多 255 个字符")
    if value.startswith(_AVATAR_PATH_PREFIX):
        name = value[len(_AVATAR_PATH_PREFIX):]
        if (
            not name
            or len(name) > 200
            or "/" in name
            or "\\" in name
            or name.startswith(".")
            or "\x00" in name
        ):
            raise HTTPException(status_code=400, detail="头像路径不合法")
        if not name.startswith(f"u{user_id}_"):
            raise HTTPException(status_code=400, detail="不能使用其他用户的头像")
        return value
    if len(value) <= 32 and _EMOJI_RE.fullmatch(value):
        return value
    raise HTTPException(status_code=400, detail="头像只支持单个 emoji 或站内上传的头像")


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
# 账号是 8 位数字、密码下限 8 位，不设限的话可以慢慢试出来。
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
    """直连方是否为可信反向代理。

    默认只信任本机代理。将任意内网地址都视为可信，会让局域网客户端通过
    伪造 X-Forwarded-For 绕过登录限流。
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("localhost",)
    # IPv4-mapped IPv6（::ffff:127.0.0.1）按 IPv4 判断
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


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
    response: Response = None,
) -> LoginResponse:
    client = _client_ip(request)
    key = _login_key(request, payload.account)
    # 达到上限后必须在验密前拒绝，否则仍然允许无限次密码猜测。
    if _recent_fails(key) >= _LOGIN_MAX_FAILS or _recent_ip_fails(client) >= _IP_MAX_FAILS:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请 10 分钟后再试")
    user = db.scalar(select(User).where(User.account == payload.account))
    if user is None:
        # 账号不存在也走一遍哈希，抹平「不存在的号返回更快」这个信号
        verify_password(payload.password, _DUMMY_HASH)
        _note_fail(key)
        _note_ip_fail(client)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not verify_password(payload.password, user.password_hash):
        _note_fail(key)
        _note_ip_fail(client)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    # 只清「IP + 账号」这一格：成功登一个自己的号就把 IP 计数清掉，
    # 等于给了攻击者「试 10 次 → 登自己的合法号重置 → 再试 10 次」的绕道。
    # IP 维度只在窗口过期时自然衰减。
    _clear_fails(key)
    # 旧哈希（低迭代次数）登录成功后自动升级到当前强度，静默重哈希
    if _needs_upgrade(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()
    token = create_access_token(
        user.id, user.account, user.role, user.token_version or 0
    )
    # 浏览器会话走 HttpOnly Cookie；响应体同时保留 token/access_token，
    # 兼容尚未升级、仍从响应里取 token 的旧客户端。
    if response is not None:
        _set_auth_cookie(response, token, request)
    return LoginResponse(
        token=token,
        access_token=token,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut, summary="当前用户信息")
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout", summary="退出登录")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """删除会话 Cookie，并把当前令牌的 jti 记入吊销黑名单。

    幂等：未登录、Cookie 已过期、令牌损坏时调用同样返回 200，前端可以无脑调用。
    只吊销这一枚令牌（jti），同一用户的其它设备不会被动下线；改密仍走 token_version。
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if token:
        try:
            payload = decode_token(token)
        except jwt.PyJWTError:
            payload = {}
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            try:
                expires_at = datetime.fromtimestamp(int(exp), timezone.utc)
                db.merge(RevokedToken(jti=str(jti), expires_at=expires_at))
                db.query(RevokedToken).filter(RevokedToken.expires_at < utcnow()).delete()
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    return {"ok": True}


@router.post("/change-password", summary="修改密码")
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    request: Request = None,
    response: Response = None,
) -> dict:
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = 0
    # 让所有已签发的令牌失效，避免旧 token 在有效期内继续可用
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    remove_initial_password(user.account)
    # 旧 Cookie 里的 token 已因 token_version 变化失效；签发新 token 写回 Cookie，
    # 让改密后的当前会话保持可用（前端仍可选择跳转登录页重新登录）。
    new_token = create_access_token(
        user.id, user.account, user.role, user.token_version or 0
    )
    if response is not None and request is not None:
        _set_auth_cookie(response, new_token, request)
    return {"ok": True}


@router.put("/profile", response_model=UserOut, summary="更新个人资料")
def update_profile(
    payload: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if payload.account is not None:
        new_account = payload.account.strip()
        if (
            user.account == SYSTEM_ADMIN_ACCOUNT
            and new_account
            and new_account != SYSTEM_ADMIN_ACCOUNT
        ):
            raise HTTPException(status_code=400, detail="系统管理员账号不能修改")
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
        user.avatar = _validate_avatar(payload.avatar, user.id)
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
