"""认证工具：密码哈希（PBKDF2，标准库实现，免 bcrypt 编译依赖）与 JWT。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings

_ITERATIONS = 600_000  # OWASP 2023+ 推荐值；旧 hash 用低迭代次数时登录后自动升级
_LEGACY_ITERATIONS = 100_000  # 早期版本用的迭代次数，两段式旧 hash 按此校验


def _parse_stored(stored: str) -> tuple[bytes, bytes, int]:
    """解析 salt$hash 或 salt$iter$hash 格式，返回 (salt, expected, iterations)。

    两段式（salt$hash）是早期格式，当时用 100000 次迭代，这里按 _LEGACY_ITERATIONS 校验。
    """
    parts = stored.split("$")
    if len(parts) == 3:
        salt_b64, iter_s, dk_b64 = parts
        iterations = int(iter_s)
    elif len(parts) == 2:
        salt_b64, dk_b64 = parts
        iterations = _LEGACY_ITERATIONS
    else:
        raise ValueError("非法哈希格式")
    return base64.b64decode(salt_b64), base64.b64decode(dk_b64), iterations


def _needs_upgrade(stored: str) -> bool:
    """旧哈希（迭代次数低于当前值）是否需要登录时升级。"""
    try:
        _, _, iterations = _parse_stored(stored)
        return iterations < _ITERATIONS
    except (ValueError, TypeError):
        return True


def hash_password(password: str, iterations: int | None = None) -> str:
    """生成 salt$iter$hash 格式的密码哈希（迭代次数写入，便于登录时判断是否升级）。"""
    it = iterations or _ITERATIONS
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, it)
    return (
        f"{base64.b64encode(salt).decode()}$"
        f"{it}$"
        f"{base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码，恒定时间比较防时序攻击。兼容旧 salt$hash 两段式格式。"""
    try:
        salt, expected, iterations = _parse_stored(stored)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: int, account: str, role: str, token_version: int = 0
) -> str:
    """签发 JWT；ver 与用户当前 token_version 不一致时令牌失效。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "account": account,
        "role": role,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解析 JWT，失败抛 jwt.PyJWTError。"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
