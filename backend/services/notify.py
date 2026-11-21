"""站内通知小工具：业务钩子调一行，失败只记日志，绝不影响主流程。"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification

logger = logging.getLogger(__name__)


def notify(
    db: Session,
    user_id: int | None,
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
) -> Notification | None:
    """给某个用户投递一条站内通知。

    刻意独立 commit：调用方的主业务此时已经提交，通知写失败（锁冲突、字段
    超长、库里没有这个用户）只回滚自己这一次事务并记日志，不能把「补答成功」
    变成 500。user_id 为空（历史记录没有提问人）时直接跳过。
    """
    if not user_id:
        return None
    try:
        item = Notification(
            user_id=int(user_id),
            kind=(kind or "system")[:32],
            title=(title or "")[:200],
            body=body or "",
            link=(link or "")[:300],
        )
        db.add(item)
        db.commit()
        return item
    except Exception:  # noqa: BLE001  通知永远不是主流程的必要条件
        logger.warning("站内通知写入失败：user_id=%s kind=%s", user_id, kind, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def notify_admins(
    db: Session,
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
) -> int:
    """给所有管理员投递同一条通知，返回成功写入条数。

    管理端需要的是「出了什么事」而不是「谁在等我答」，所以只用于运营事件
    （文档处理失败、学生差评等），待解答问题仍走未命中队列，两边不重复。
    """
    from ..models import User

    try:
        admin_ids = [
            uid for uid in db.scalars(select(User.id).where(User.role == "admin")).all() if uid
        ]
    except Exception:  # noqa: BLE001
        logger.warning("站内通知查询管理员失败 kind=%s", kind, exc_info=True)
        return 0
    if not admin_ids:
        return 0
    try:
        db.add_all(
            [
                Notification(
                    user_id=int(uid),
                    kind=(kind or "system")[:32],
                    title=(title or "")[:200],
                    body=body or "",
                    link=(link or "")[:300],
                )
                for uid in admin_ids
            ]
        )
        db.commit()
        return len(admin_ids)
    except Exception:  # noqa: BLE001
        logger.warning("站内通知写入管理员失败 kind=%s", kind, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
