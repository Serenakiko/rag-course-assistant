"""检索调试路由：管理员查看一次真实检索的候选、重排与阈值门控决策。

只读端点，复用 vector_store.search_trace()（与正式检索共用 _search_pipeline），
不复制检索逻辑、不写库、不影响问答行为。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import settings
from ..deps import require_role
from ..models import User
from ..services import vector_store

logger = logging.getLogger(__name__)

router = APIRouter()

PREVIEW_CHARS = 120


def _as_int(value) -> int | None:
    """metadata 里的 doc_id 可能是 str/int/None，统一成 int 或 None。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunk_id(meta: dict) -> str | None:
    chunk_id = meta.get("chunk_id")
    if chunk_id:
        return str(chunk_id)
    point_id = meta.get("point_id")
    if point_id not in (None, ""):
        return f"point:{point_id}"
    return None


def _candidate(doc, score: float, rank: int, threshold: float) -> dict:
    src = doc.metadata or {}
    rerank_score = src.get("rerank_score")
    return {
        "chunk_id": _chunk_id(src),
        "doc_id": _as_int(src.get("doc_id")),
        "filename": src.get("filename") or src.get("title") or "",
        "course": src.get("course") or "未分类",
        "title": src.get("title") or src.get("filename") or "",
        "score": round(float(score), 6),
        "rank": rank,
        "rerank_score": (
            round(float(rerank_score), 6) if rerank_score is not None else None
        ),
        # 名字必须如实：这条字段只表示「该候选的相似度是否达到阈值」，
        # 不是「是否进入最终 top-k」——返回的 candidates 本来就全是 top-k。
        # 早期叫 selected，读日志的人容易误以为这些是被选中的资料。
        "passed_threshold": float(score) >= threshold,
        "preview": (doc.page_content or "")[:PREVIEW_CHARS],
    }


@router.get("/debug", summary="检索调试（仅管理员）")
def retrieval_debug(
    question: str = Query(..., min_length=1, max_length=500, description="检索问题"),
    courses: list[str] | None = Query(default=None, description="课程（知识库）过滤，可重复"),
    _user: User = Depends(require_role("admin")),
) -> dict:
    """返回真实检索链路的候选、重排前后名次与阈值门控决策。"""
    scopes = [c.strip() for c in (courses or []) if c and c.strip()]
    threshold = float(settings.UNMATCHED_SIMILARITY)
    try:
        trace = vector_store.search_trace(
            question,
            settings.TOP_K,
            scopes if scopes else None,
        )
    except Exception:  # noqa: BLE001 - 向量库/embedding 不可用时给前端明确错误
        logger.exception("检索调试失败：向量检索链路不可用")
        raise HTTPException(
            status_code=503,
            detail="检索服务暂时不可用，请检查向量库与 embedding 模型是否就绪",
        ) from None

    selected = trace["selected"]
    before = trace["before_rerank"]
    before_rank = {id(doc): index + 1 for index, (doc, _score) in enumerate(before)}

    candidates: list[dict] = []
    reranked: list[dict] = []
    for rank, (doc, score) in enumerate(selected, start=1):
        rank_before = before_rank.get(id(doc), rank)
        candidates.append(_candidate(doc, score, rank, threshold))
        if rank_before != rank:
            reranked.append(
                {
                    "chunk_id": _chunk_id(doc.metadata or {}),
                    "rank_before": rank_before,
                    "rank_after": rank,
                }
            )

    top_score = max((item["score"] for item in candidates), default=0.0)
    if not candidates:
        answerable, reason = False, "no_candidates"
    elif top_score >= threshold:
        answerable, reason = True, "above_threshold"
    else:
        answerable, reason = False, "below_threshold"

    timings = trace["timings"]
    embed_ms = int(round(timings["embed"]))
    query_ms = int(round(timings["query"]))
    rerank_ms = int(round(timings["rerank"]))

    return {
        "question": question,
        "courses": scopes,
        "embedding_ms": embed_ms,
        "retrieval_ms": query_ms + rerank_ms,
        "threshold": threshold,
        "candidates": candidates,
        "reranked": reranked,
        "decision": {
            "answerable": answerable,
            "top_score": round(top_score, 6),
            "reason": reason,
        },
        "timings_ms": {"embed": embed_ms, "query": query_ms, "rerank": rerank_ms},
    }
