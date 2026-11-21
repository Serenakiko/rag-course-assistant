"""FastAPI 依赖注入：当前用户、角色校验。"""
from __future__ import annotations

from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import RevokedToken, User
from .security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)

# 会话 Cookie 名。前端不再持久化 token，浏览器刷新后靠它恢复登录态。
ACCESS_TOKEN_COOKIE = "access_token"


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """解析当前用户：优先 Authorization: Bearer，其次 HttpOnly Cookie。

    保留 Bearer 分支是为了兼容旧客户端和脚本；浏览器走 Cookie，JS 读不到 token。
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态无效或已过期，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = credentials.credentials if credentials is not None else request.cookies.get(
        ACCESS_TOKEN_COOKIE
    )
    if not token:
        raise unauthorized
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", "0"))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise unauthorized
    # 已登出的令牌：登出时把 jti 记入黑名单，即使 token 未过期也拒绝
    jti = payload.get("jti")
    if jti and db.get(RevokedToken, jti) is not None:
        raise unauthorized
    user = db.get(User, user_id)
    if user is None:
        raise unauthorized
    # 改密、降权后旧令牌立即失效
    if int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise unauthorized
    allowed_before_password_change = {
        "/api/auth/me",
        "/api/auth/change-password",
    }
    if user.must_change_password and request.url.path not in allowed_before_password_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="首次登录必须修改密码后才能继续使用",
        )
    return user


def require_role(*roles: str) -> Callable:
    """工厂：生成要求指定角色的依赖。"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前角色无权执行此操作",
            )
        return user

    return checker
