"""站内通知路由：列表 / 已读 / 全部已读 / 管理员手动发送。

只服务本人：所有读写都用当前登录用户过滤，别人的通知一律 404（不泄露存在性）。
分页用 before_id 游标（id < before_id），比 offset 更适合「加载更多」——
新通知不断插进来时 offset 会重复/漏条。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import Notification, User, utcnow
from ..schemas import NotificationCreateRequest

router = APIRouter()

MAX_LIMIT = 100


def _to_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body or "",
        "link": n.link or "",
        "read": n.read_at is not None,
        "created_at": n.created_at,
    }


def _unread_count(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        or 0
    )


@router.get("", summary="我的通知列表")
def list_notifications(
    unread_only: bool = False,
    limit: int = 20,
    before_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """返回本人通知（新→旧），未读数按本人全量统计，不受过滤条件影响。"""
    limit = min(max(limit, 1), MAX_LIMIT)
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    if before_id is not None and before_id > 0:
        query = query.where(Notification.id < before_id)
    rows = list(
        db.scalars(query.order_by(Notification.id.desc()).limit(limit + 1)).all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_to_dict(n) for n in rows],
        "unread": _unread_count(db, user.id),
        "has_more": has_more,
    }


@router.post("/read-all", summary="全部标为已读")
def read_all(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    db.commit()
    return {"ok": True, "unread": 0}


@router.post("/{nid}/read", summary="标记一条通知为已读")
def mark_read(
    nid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """幂等：已读再点一次不改 read_at，仍返回 ok 和当前未读数。"""
    item = db.get(Notification, nid)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="通知不存在")
    if item.read_at is None:
        item.read_at = utcnow()
        db.commit()
    return {"ok": True, "unread": _unread_count(db, user.id)}


@router.post("", summary="管理员发一条系统通知")
def create_notification(
    payload: NotificationCreateRequest,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """手动运营：给指定用户发一条 kind=system 的通知。"""
    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    item = Notification(
        user_id=target.id,
        kind="system",
        title=payload.title,
        body=payload.body,
        link=payload.link,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"ok": True, "id": item.id, "unread": _unread_count(db, target.id)}
