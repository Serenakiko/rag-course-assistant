"""管理员初始凭据加固：默认密码轮换与一次性引导文件。"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import User
from ..security import hash_password, verify_password
from ..seed_data import DEFAULT_ADMIN

logger = logging.getLogger(__name__)

CREDENTIALS_FILENAME = "admin_initial_credentials.txt"


def default_credentials_path(path: Path | None = None) -> Path:
    return path or settings.DATA_DIR / CREDENTIALS_FILENAME


def _known_default_passwords() -> set[str]:
    return {DEFAULT_ADMIN[1]}


def _generate_password() -> str:
    return secrets.token_urlsafe(18)


def _read_credentials(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        account, sep, password = line.partition("\t")
        if sep and account and password:
            result[account] = password
    return result


def _write_credentials(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = [
        "# 管理员初始密码，仅用于本机首次登录。登录后请立即修改密码。",
        f"# 生成时间：{stamp}",
        *(f"{account}\t{password}" for account, password in sorted(entries.items())),
    ]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def remember_initial_password(
    account: str, password: str, path: Path | None = None
) -> None:
    """保存本机可读的初始密码，供部署者在服务器文件系统中获取。"""
    target = default_credentials_path(path)
    entries = _read_credentials(target)
    entries[account] = password
    _write_credentials(target, entries)


def remove_initial_password(account: str, path: Path | None = None) -> None:
    """改密成功后删除该账号的初始密码记录。"""
    target = default_credentials_path(path)
    entries = _read_credentials(target)
    if account not in entries:
        return
    entries.pop(account, None)
    try:
        if entries:
            _write_credentials(target, entries)
        else:
            target.unlink(missing_ok=True)
    except OSError:
        # 密码已经成功落库，文件清理失败不能反过来让前端误以为改密失败。
        logger.warning("删除初始密码记录失败 account=%s path=%s", account, target)


def rotate_insecure_admin_passwords(
    db: Session, *, credentials_path: Path | None = None
) -> list[tuple[str, str]]:
    """轮换所有仍在使用已知默认密码的管理员账号。

    新密码只在本地凭据文件中出现，不写日志。轮换同时递增 token_version，
    让已经签发的旧令牌立即失效，并要求首次登录后改密。
    """
    known = _known_default_passwords()
    admins = db.scalars(select(User).where(User.role == "admin")).all()
    rotated: list[tuple[str, str]] = []
    for admin in admins:
        if not any(verify_password(password, admin.password_hash) for password in known):
            continue
        password = _generate_password()
        admin.password_hash = hash_password(password)
        admin.must_change_password = 1
        admin.token_version = int(admin.token_version or 0) + 1
        rotated.append((admin.account, password))

    if not rotated:
        return []

    target = default_credentials_path(credentials_path)
    entries = _read_credentials(target)
    entries.update(rotated)
    _write_credentials(target, entries)
    db.commit()
    logger.warning(
        "已轮换 %s 个使用默认密码的管理员账号；初始密码见本机文件 %s",
        len(rotated),
        target,
    )
    return rotated


def create_default_admin(db: Session, *, path: Path | None = None) -> User:
    """创建默认管理员；未配置强密码时生成随机初始密码。"""
    admin = db.scalar(select(User).where(User.account == DEFAULT_ADMIN[0]))
    if admin is not None:
        if admin.role != "admin":
            raise RuntimeError(
                f"账号 {DEFAULT_ADMIN[0]} 已存在，但角色为 {admin.role}，不是管理员。"
            )
        return admin

    configured = (settings.DEFAULT_ADMIN_PASSWORD or "").strip()
    generated = (
        not configured
        or len(configured) < 8
        or configured in _known_default_passwords()
    )
    password = _generate_password() if generated else configured
    admin = User(
        account=DEFAULT_ADMIN[0],
        password_hash=hash_password(password),
        username=DEFAULT_ADMIN[2],
        role="admin",
        must_change_password=1 if generated else 0,
    )
    db.add(admin)
    try:
        db.flush()
        if generated:
            remember_initial_password(admin.account, password, path)
        db.commit()
    except Exception:
        db.rollback()
        if generated:
            remove_initial_password(admin.account, path)
        raise
    db.refresh(admin)
    if generated:
        logger.warning(
            "已创建管理员 %s，初始密码见本机文件 %s",
            admin.account,
            default_credentials_path(path),
        )
    return admin
