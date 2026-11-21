"""F7 导出与分享：会话/知识库导出、限时免登录分享。

路由按完整路径注册（`/api/export/...`、`/api/share...`、`/share/{token}`），
所以 main.py 里只需要一行 `app.include_router(export.router, ...)`，不额外加 prefix。

安全约定：
- 导出会话：本人或管理员；他人会话统一 404，避免暴露存在性。
- 导出知识库、分享知识库：仅管理员。
- 分享令牌用 secrets.token_urlsafe(32)，过期 / 撤销 / 不存在统一 404 文案。
- 免登录 JSON 不返回 user_id、feedback、feedback_reason 等内部字段。
- GET /api/share/{token} 只读：不写库、不要求登录。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import Conversation, KnowledgeBase, KnowledgePoint, Message, ShareLink, User, utcnow
from ..schemas import ShareCreateRequest

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SHARE_HOURS_DEFAULT = 72
SHARE_HOURS_MIN = 1
SHARE_HOURS_MAX = 720
SHARE_NOT_FOUND = "分享链接不存在或已失效"

VALID_KINDS = ("conversation", "knowledge_base")


# ---------------------------------------------------------------- 通用工具


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读出来是 naive，按 UTC 解释；统一带上时区再比较/序列化。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str:
    dt = _as_utc(value)
    if dt is None:
        return ""
    return dt.isoformat().replace("+00:00", "Z")


def _display_time(value: datetime | None) -> str:
    """导出文件里给人看的时间：统一换算到 UTC+8，避免服务器时区歧义。"""
    dt = _as_utc(value)
    if dt is None:
        return "-"
    local = dt.astimezone(timezone(timedelta(hours=8)))
    return local.strftime("%Y-%m-%d %H:%M:%S (UTC+8)")


def _parse_sources(raw: str | None) -> list[dict]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _safe_filename(name: str, fallback: str) -> str:
    """文件名里去掉 Windows/HTTP 头容易出问题的字符，空则用兜底名。"""
    cleaned = "".join(ch for ch in (name or "") if ch not in '\\/:*?"<>|\r\n\t').strip()
    cleaned = cleaned[:80] or fallback
    return cleaned


def _attachment_header(filename: str) -> str:
    """RFC 5987：ASCII 兜底名 + UTF-8 百分号编码的 filename*。"""
    ascii_name = _safe_filename(filename.encode("ascii", "ignore").decode("ascii"), "export")
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _get_conversation(db: Session, conv_id: int, user: User) -> Conversation:
    conv = db.get(Conversation, conv_id)
    # 不存在和无权访问用同一个 404，避免通过状态码探测他人会话
    if conv is None or (user.role != "admin" and conv.user_id != user.id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


def _get_knowledge_base(db: Session, base_id: int) -> KnowledgeBase:
    base = db.get(KnowledgeBase, base_id)
    if base is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return base


def _conversation_messages(db: Session, conv_id: int) -> list[Message]:
    return list(
        db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).all()
    )


def _knowledge_points(db: Session, base: KnowledgeBase) -> list[KnowledgePoint]:
    return list(
        db.scalars(
            select(KnowledgePoint)
            .where(KnowledgePoint.course == base.name)
            .order_by(KnowledgePoint.id)
        ).all()
    )


# ---------------------------------------------------------------- Markdown 导出


def _feedback_line(msg: Message) -> str:
    if msg.role != "assistant" or not msg.feedback:
        return ""
    label = "👍 有帮助" if msg.feedback == "up" else "👎 没帮助" if msg.feedback == "down" else ""
    if not label:
        return ""
    reason = (msg.feedback_reason or "").strip()
    return f"**反馈**：{label}" + (f"（原因：{reason}）" if reason else "")


def _sources_block(msg: Message) -> list[str]:
    sources = _parse_sources(msg.sources)
    if not sources:
        return []
    lines = ["", "**引用来源**", ""]
    for index, src in enumerate(sources, start=1):
        title = (src.get("title") or src.get("filename") or "未知来源").strip()
        course = (src.get("course") or "未分类").strip()
        location = (src.get("section") or src.get("chapter") or "").strip()
        page = src.get("page")
        locator = location
        if page not in (None, "", 0):
            locator = f"{locator}，第 {page} 页" if locator else f"第 {page} 页"
        suffix = f"（课程：{course}" + (f"；位置：{locator}" if locator else "") + "）"
        lines.append(f"{index}. **{title}**{suffix}")
        excerpt = (src.get("content") or "").strip()
        if excerpt:
            excerpt = excerpt.replace("\r\n", "\n").replace("\n", "\n   > ")
            lines.append(f"   > {excerpt[:300]}")
    return lines


def _conversation_markdown(conv: Conversation, messages: list[Message]) -> str:
    lines = [
        f"# 会话：{conv.title or '未命名会话'}",
        "",
        f"- 会话 ID：{conv.id}",
        f"- 创建时间：{_display_time(conv.created_at)}",
        f"- 消息数：{len(messages)}",
        f"- 导出时间：{_display_time(utcnow())}",
        "",
        "---",
        "",
    ]
    for msg in messages:
        role = "学生" if msg.role == "user" else "助教"
        lines.append(f"## {role}")
        lines.append("")
        lines.append(f"*{_display_time(msg.created_at)}*")
        lines.append("")
        content = (msg.content or "").strip()
        lines.append(content if content else "（空消息）")
        lines.append("")
        lines.extend(_sources_block(msg))
        feedback = _feedback_line(msg)
        if feedback:
            lines.append("")
            lines.append(feedback)
        if msg.interrupted:
            lines.append("")
            lines.append("> 该回答生成过程中被中断。")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _conversation_payload(conv: Conversation, messages: list[Message]) -> dict:
    return {
        "id": conv.id,
        "title": conv.title,
        "created_at": _iso(conv.created_at),
        "message_count": len(messages),
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "sources": _parse_sources(msg.sources),
                "interrupted": bool(msg.interrupted),
                "feedback": msg.feedback,
                "feedback_reason": msg.feedback_reason,
                "created_at": _iso(msg.created_at),
            }
            for msg in messages
        ],
    }


def _knowledge_markdown(base: KnowledgeBase, points: list[KnowledgePoint]) -> str:
    lines = [
        f"# 知识库：{base.name}",
        "",
        f"- 知识库 ID：{base.id}",
        f"- 描述：{base.description or '（无）'}",
        f"- 创建时间：{_display_time(base.created_at)}",
        f"- 更新时间：{_display_time(base.updated_at)}",
        f"- 知识点数：{len(points)}",
        f"- 导出时间：{_display_time(utcnow())}",
        "",
        "---",
        "",
    ]
    if not points:
        lines.append("（该知识库暂无知识点）")
        return "\n".join(lines).rstrip() + "\n"
    for point in points:
        lines.append(f"## {point.title or '未命名知识点'}")
        lines.append("")
        meta = []
        if point.course:
            meta.append(f"课程：{point.course}")
        if point.tags:
            meta.append(f"标签：{point.tags}")
        meta.append(f"更新时间：{_display_time(point.updated_at)}")
        lines.append("*" + "；".join(meta) + "*")
        lines.append("")
        lines.append((point.content or "").strip() or "（无正文）")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- 导出接口


@router.get("/api/export/conversations/{conv_id}.md", summary="导出会话 Markdown")
def export_conversation_markdown(
    conv_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    conv = _get_conversation(db, conv_id, user)
    messages = _conversation_messages(db, conv.id)
    body = _conversation_markdown(conv, messages)
    filename = _safe_filename(f"会话-{conv.title or conv.id}.md", f"conversation-{conv.id}.md")
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _attachment_header(filename)},
    )


@router.get("/api/export/conversations/{conv_id}.json", summary="导出会话 JSON")
def export_conversation_json(
    conv_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = _get_conversation(db, conv_id, user)
    messages = _conversation_messages(db, conv.id)
    payload = {
        "kind": "conversation",
        "exported_at": _iso(utcnow()),
        "conversation": _conversation_payload(conv, messages),
    }
    filename = _safe_filename(f"会话-{conv.title or conv.id}.json", f"conversation-{conv.id}.json")
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _attachment_header(filename)},
    )


@router.get("/api/export/knowledge/{base_id}.md", summary="导出知识库 Markdown（管理员）")
def export_knowledge_markdown(
    base_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> Response:
    base = _get_knowledge_base(db, base_id)
    points = _knowledge_points(db, base)
    body = _knowledge_markdown(base, points)
    filename = _safe_filename(f"知识库-{base.name}.md", f"knowledge-{base.id}.md")
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _attachment_header(filename)},
    )


# ---------------------------------------------------------------- 分享管理（需登录）


def _create_token() -> str:
    # token_urlsafe(32) 约 43 字符，远小于列宽 64
    return secrets.token_urlsafe(32)


def _target_title(db: Session, kind: str, target_id: int) -> str:
    if kind == "conversation":
        conv = db.get(Conversation, target_id)
        return (conv.title or "") if conv is not None else ""
    base = db.get(KnowledgeBase, target_id)
    return base.name if base is not None else ""


def _share_item(db: Session, link: ShareLink) -> dict:
    return {
        "id": link.id,
        "token": link.token,
        "kind": link.kind,
        "target_id": link.target_id,
        "target_title": _target_title(db, link.kind, link.target_id),
        "url": f"/share/{link.token}",
        "revoked": bool(link.revoked),
        "expired": bool(link.revoked) or _as_utc(link.expires_at) <= utcnow(),
        "created_at": _iso(link.created_at),
        "expires_at": _iso(link.expires_at),
    }


@router.post("/api/share", summary="创建分享链接")
def create_share(
    payload: ShareCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # kind / id / expires_hours 的类型由 ShareCreateRequest 校验（StrictInt 拒绝布尔）
    kind = payload.kind
    target_id = payload.id
    hours = payload.expires_hours
    if not SHARE_HOURS_MIN <= hours <= SHARE_HOURS_MAX:
        raise HTTPException(status_code=422, detail="expires_hours 超出 1..720 范围")

    if kind == "conversation":
        conv = db.get(Conversation, target_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if user.role != "admin" and conv.user_id != user.id:
            raise HTTPException(status_code=403, detail="只能分享自己的会话")
    else:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可分享知识库")
        if db.get(KnowledgeBase, target_id) is None:
            raise HTTPException(status_code=404, detail="知识库不存在")

    expires_at = utcnow() + timedelta(hours=hours)
    link = ShareLink(
        token=_create_token(),
        kind=kind,
        target_id=target_id,
        created_by=user.id,
        expires_at=expires_at,
        revoked=False,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return {
        "token": link.token,
        "url": f"/share/{link.token}",
        "expires_at": _iso(link.expires_at),
    }


@router.get("/api/share", summary="我创建的分享（分页）")
def list_shares(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    total = db.scalar(
        select(func.count(ShareLink.id)).where(ShareLink.created_by == user.id)
    ) or 0
    rows = db.scalars(
        select(ShareLink)
        .where(ShareLink.created_by == user.id)
        .order_by(ShareLink.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [_share_item(db, link) for link in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.delete("/api/share/{token}", summary="撤销分享链接")
def revoke_share(
    token: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    link = db.scalar(select(ShareLink).where(ShareLink.token == token))
    if link is None or (user.role != "admin" and link.created_by != user.id):
        raise HTTPException(status_code=404, detail="分享链接不存在")
    if not link.revoked:
        link.revoked = True
        db.commit()
    return {"ok": True, "token": link.token, "revoked": True}


# ---------------------------------------------------------------- 免登录读取


def _share_conversation_payload(db: Session, conv: Conversation) -> dict:
    messages = _conversation_messages(db, conv.id)
    return {
        "title": conv.title,
        "created_at": _iso(conv.created_at),
        "message_count": len(messages),
        # 只暴露展示所需字段：不含 user_id / conversation_id / feedback 系列内部字段
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "sources": [
                    {
                        "title": src.get("title") or src.get("filename") or "未知来源",
                        "course": src.get("course") or "",
                        "section": src.get("section") or src.get("chapter") or "",
                        "page": src.get("page"),
                        "content": (src.get("content") or "")[:400],
                    }
                    for src in _parse_sources(msg.sources)
                ],
                "interrupted": bool(msg.interrupted),
                "created_at": _iso(msg.created_at),
            }
            for msg in messages
        ],
    }


def _share_knowledge_payload(db: Session, base: KnowledgeBase) -> dict:
    points = _knowledge_points(db, base)
    return {
        "name": base.name,
        "description": base.description,
        "created_at": _iso(base.created_at),
        "updated_at": _iso(base.updated_at),
        "point_count": len(points),
        # 不含 created_by / updated_by
        "points": [
            {
                "title": point.title,
                "content": point.content,
                "course": point.course,
                "tags": point.tags,
                "created_at": _iso(point.created_at),
                "updated_at": _iso(point.updated_at),
            }
            for point in points
        ],
    }


@router.get("/api/share/{token}", summary="读取分享内容（免登录）")
def read_share(token: str, db: Session = Depends(get_db)) -> dict:
    # 只读：这里绝对不 create / commit / update 任何表
    link = db.scalar(select(ShareLink).where(ShareLink.token == token))
    if link is None or link.revoked:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    expires_at = _as_utc(link.expires_at)
    if expires_at is None or expires_at <= utcnow():
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)

    if link.kind == "conversation":
        conv = db.get(Conversation, link.target_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
        content = {"conversation": _share_conversation_payload(db, conv), "knowledge_base": None}
    elif link.kind == "knowledge_base":
        base = db.get(KnowledgeBase, link.target_id)
        if base is None:
            raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
        content = {"conversation": None, "knowledge_base": _share_knowledge_payload(db, base)}
    else:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)

    return {
        "kind": link.kind,
        "target_id": link.target_id,
        "created_at": _iso(link.created_at),
        "expires_at": _iso(link.expires_at),
        **content,
    }


@router.get("/share/{token}", include_in_schema=False)
def share_page(token: str, request: Request) -> FileResponse:
    """免登录只读页；真实内容由页面里的 JS 调 /api/share/{token} 拉取。"""
    return FileResponse(STATIC_DIR / "share.html", media_type="text/html; charset=utf-8")
