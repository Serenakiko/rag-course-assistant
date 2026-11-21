"""RAG 核心服务：检索增强生成（流式）。

流程：用户问题 → 向量检索 Top-K → 组装系统提示（参考资料+对话历史）
      → 大模型流式生成 → 输出 token 与引用来源。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import weakref
from collections.abc import AsyncGenerator, Callable
from enum import Enum

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from ..config import settings
from .llm_service import get_llm, unload_ollama_llm
from .rag_prompts import (
    ANSWERABILITY_PROMPT,
    COURSE_STRONG_KEYWORDS,
    IRRELEVANT_KEYWORDS,
    OVERVIEW_PROMPT,
    QUERY_REWRITE_PROMPT,
    SMALLTALK_REPLY,
    SYSTEM_PROMPT,
    feedback_lines,
    is_course_overview_question,
    is_smalltalk,
    is_unmatched_answer,
)
from .vector_store import _chunk_locator
from .vector_store import search as vector_search

logger = logging.getLogger(__name__)


class Answerability(str, Enum):
    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"
    ERROR = "error"
    RESOURCE_ERROR = "resource_error"


_MODEL_RESOURCE_ERROR_MARKERS = (
    "out-of-memory",
    "failed to allocate",
    "memory allocation failed",
    "not enough memory",
    "insufficient memory",
)


def _is_model_resource_error(exc: BaseException) -> bool:
    """识别 Ollama/llama.cpp 拉不起模型时的内存不足错误。"""
    text = str(exc).lower()
    return any(marker in text for marker in _MODEL_RESOURCE_ERROR_MARKERS)


def _max_similarity(docs: list[tuple[Document, float]]) -> float:
    """reranker 改变顺序后，距离门控仍以候选最高语义分为准。"""
    return max((float(score) for _doc, score in docs), default=0.0)


def _est_tokens(text: str) -> int:
    """粗估 token 数（中西文分开估）。用于上下文预算，不要求精确。

    中文约 1.5~2 token/字（取 1.6），英文约 0.25 token/字符（一个词约 1 token，
    平均词长 4~5 字符）。之前全用 len×1.6，英文资料预算被高估 6 倍而过度裁剪。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_chars = len(text) - cjk
    est = cjk * settings.CHAR_TO_TOKEN + ascii_chars / 4.0
    return max(1, int(est))


async def _astream_with_deadline(chain, inputs: dict, timeout: float) -> AsyncGenerator:
    """带总时长上限地迭代 chain.astream。

    只在循环体里判断 deadline 是假防线：模型卡住时一个 chunk 都不来，
    判断根本执行不到。这里每个 chunk 都用 wait_for 包一层，剩余时间一到
    就取消底层请求，保证「超时」真的会停。
    """
    agen = chain.astream(inputs)
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                chunk = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
            yield chunk
    finally:
        await agen.aclose()


def _doc_block(doc: Document, index: int) -> str:
    """把单条资料格式化为提示词里的一段；index 从 1 开始，与答案里的 [n] 角标对应。"""
    src = doc.metadata
    title = src.get("title") or src.get("filename") or "未知来源"
    course = src.get("course") or "未分类"
    section = src.get("section") or src.get("chapter") or ""
    page = src.get("page") or src.get("page_number") or ""
    locator = "，".join(
        part for part in (
            f"章节：{section}" if section else "",
            f"页码：{page}" if page != "" else "",
        ) if part
    )
    return (
        f"[{index}] 来源《{title}》（课程：{course}"
        f"{'，' + locator if locator else ''}）\n{doc.page_content}"
    )


def _format_context(docs: list[tuple[Document, float]]) -> str:
    """把检索结果格式化为提示词中的参考文本。

    编号必须与「真正送进提示词的资料顺序」一致：调用方要用同一份列表生成
    sources 与引用校验，否则会出现模型没看过 [3]、前端却仍列出来源的问题。
    """
    return "\n\n".join(
        _doc_block(doc, i) for i, (doc, _score) in enumerate(docs, start=1)
    )


def _format_sources(docs: list[tuple[Document, float]]) -> list[dict]:
    """输出给前端的引用来源（含摘要，便于展示）。"""
    out: list[dict] = []
    for _i, (doc, _score) in enumerate(docs, start=1):
        src = doc.metadata
        section = src.get("section") or src.get("chapter") or ""
        if not section and src.get("source_type") == "knowledge_point":
            # 知识点没有页码/章节，用标题兜底，至少能定位到具体知识点
            section = src.get("title") or ""
        page = src.get("page") or src.get("page_number")
        if src.get("source_type") == "document" and (not section or page is None):
            inferred_page, inferred_section = _chunk_locator(doc.page_content)
            section = section or inferred_section
            if page is None:
                page = inferred_page or None
        out.append(
            {
                "source_type": src.get("source_type", "document"),
                "doc_id": src.get("doc_id"),
                "point_id": src.get("point_id"),
                "chunk_id": src.get("chunk_id"),
                "chunk_index": src.get("chunk_index"),
                "locator": src.get("chunk_id")
                or (f"point:{src.get('point_id')}" if src.get("point_id") else None),
                "filename": src.get("filename"),
                "title": src.get("title") or src.get("filename") or "未知来源",
                "course": src.get("course") or "未分类",
                "section": section,
                "chapter": src.get("chapter") or "",
                "page": page,
                "content": doc.page_content[:400],
                "content_length": len(doc.page_content),
            }
        )
    return out


_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


def _citation_audit(answer: str, source_count: int) -> tuple[list[int], list[int]]:
    """校验答案里的 [n] 角标：返回 (有效编号, 越界编号)，编号从 1 开始。"""
    cited = [int(n) for n in _CITATION_RE.findall(answer)]
    valid = sorted({n for n in cited if 1 <= n <= source_count})
    invalid = sorted({n for n in cited if n < 1 or n > source_count})
    return valid, invalid


def _cjk_bigrams(text: str) -> set[str]:
    """把文本切成二元组，用作便宜的字面重合度（不引 tokenizer / jieba）。"""
    clean = "".join(ch for ch in (text or "") if not ch.isspace())
    if len(clean) < 2:
        return set()
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")


def _citation_grounding(
    answer: str, sources: list[dict], min_overlap: float = 0.15
) -> dict:
    """粗查「角标挂的内容和来源有没有字面关系」。

    编号范围合法不代表引用成立：模型完全可以给真实存在的 [2] 编造一段来源里没有的话。
    这里按句检查含角标的句子与对应来源的字面二元组重合率，低于阈值记为 weak ——
    只作提示与日志信号，不拦截回答（字面重合低不等于一定错，改写表述也会掉重合）。
    """
    weak: list[int] = []
    checked = 0
    for sentence in _SENTENCE_RE.findall(answer or ""):
        nums = [int(n) for n in _CITATION_RE.findall(sentence)]
        if not nums:
            continue
        sent_grams = _cjk_bigrams(_CITATION_RE.sub("", sentence))
        if not sent_grams:
            continue
        for n in nums:
            if not 1 <= n <= len(sources):
                continue
            checked += 1
            src_grams = _cjk_bigrams(sources[n - 1].get("content") or "")
            if not src_grams:
                continue
            if len(sent_grams & src_grams) / len(sent_grams) < min_overlap:
                weak.append(n)
    return {"checked": checked, "weak": sorted(set(weak))}


def _citation_stats(
    answer: str, source_count: int, sources: list[dict] | None = None
) -> dict:
    """记录引用越界与弱引用，并随 sources 事件返回。"""
    valid, invalid = _citation_audit(answer, source_count)
    grounding = _citation_grounding(answer, sources) if sources else {"checked": 0, "weak": []}
    if invalid:
        logger.warning(
            "回答引用了不存在的来源编号 %s（本次来源 %d 条）", invalid, source_count
        )
    elif valid:
        logger.info("回答引用编号 %s（来源 %d 条）", valid, source_count)
    if grounding["weak"]:
        logger.warning(
            "回答的部分角标与来源文字重合度过低，疑似空挂引用：%s（共检查 %d 处）",
            grounding["weak"],
            grounding["checked"],
        )
    return {"valid": valid, "invalid": invalid, **grounding}


async def _rewrite_query(question: str, history: list[tuple[str, str]]) -> str:
    """多轮追问的检索 query 改写：把指代补全，避免「它」「这个」召回失败。

    有历史时优先用模型把指代补全成独立可检索的问句；模型不可用或超时就退化为
    纯文本拼接（最近若干轮 user 问题 + 本轮问题），至少比只拿「它」去检索强。
    """
    if not history:
        return question
    recent = history[-settings.QUERY_REWRITE_HISTORY_TURNS * 2 :]
    if not recent:
        return question
    hist_text = "\n".join(f"{'学生' if r == 'user' else '助教'}：{c}" for r, c in recent)
    try:
        llm = get_llm()
        # QUERY_REWRITE_PROMPT 里带 {history}/{question} 占位符，必须 format 后再当 system 传，
        # 否则小模型收到的是字面 "{history}" 残缺指令，真实历史反而被当 human 消息重复喂了一遍。
        system = QUERY_REWRITE_PROMPT.format(history=hist_text, question=question)
        resp = await asyncio.wait_for(
            llm.ainvoke(
                [
                    ("system", system),
                    ("human", question),
                ]
            ),
            timeout=8.0,
        )
        text = (getattr(resp, "content", "") or "").strip()
        if text and text != question:
            return text[:500]
    except Exception:  # noqa: BLE001
        logger.warning("查询改写失败，退化为文本拼接：%s", question[:30])
    # 退化：拼接最近的 user 问题作为上下文
    parts = [c for r, c in recent if r == "user"]
    parts.append(question)
    return " ".join(parts)[:500]


def _context_budget() -> int:
    """可用于「系统提示 + 历史 + 检索资料」的 token 预算。

    num_ctx 是 prompt + 输出总窗口，减去 num_predict 再留 20% 安全边际。
    """
    usable = settings.LLM_CTX - settings.LLM_MAX_TOKENS
    return int(usable * 0.8)


def _trim_history_to_budget(
    history: list[tuple[str, str]], budget: int
) -> tuple[list[tuple[str, str]], int]:
    """按 token 预算从旧到新裁剪历史，返回 (保留的历史, 实际占用 token)。

    返回实际占用，是为了把没用掉的额度回流给检索资料：首轮没有历史时，
    资料应当拿到全部剩余额度，而不是被固定切走 40%。
    """
    if not history:
        return [], 0
    kept: list[tuple[str, str]] = []
    used = 0
    for role, content in reversed(history):  # 从新到旧挑
        size = _est_tokens(content)
        if kept and used + size > budget:
            break
        kept.append((role, content))
        used += size
    return list(reversed(kept)), used


def _select_docs_within_budget(
    docs: list[tuple[Document, float]], budget: int
) -> list[tuple[Document, float]]:
    """按相关性顺序挑出能放进预算的资料，至少保留首条。

    返回的列表就是真正进入提示词的资料，调用方必须用它生成 sources 与引用校验，
    否则角标 [n] 会和来源列表错位（模型没看过第 3 条，前端却列出第 3 条）。
    """
    if not docs or budget <= 0:
        return []
    chosen: list[tuple[Document, float]] = []
    used = 0
    for doc, score in docs:
        block = _doc_block(doc, len(chosen) + 1)
        cost = _est_tokens(block)
        if not chosen and cost > budget:
            # 单条就超预算：截断正文而不是整条丢掉，保证至少有一条依据
            header = _doc_block(Document(page_content="", metadata=doc.metadata), 1)
            room = max(budget - _est_tokens(header), budget // 2)
            body_tokens = max(_est_tokens(doc.page_content), 1)
            limit = max(120, int(len(doc.page_content) * room / body_tokens))
            truncated = Document(
                page_content=doc.page_content[:limit] + "\n…（单条资料过长，已截断）",
                metadata=doc.metadata,
            )
            chosen.append((truncated, score))
            break
        if chosen and used + cost > budget:
            break
        chosen.append((doc, score))
        used += cost
    return chosen


def _build_messages(
    system_prompt: str,
    docs: list[tuple[Document, float]],
    question: str,
    history: list[tuple[str, str]],
) -> tuple[str, list[tuple[str, str]], list[tuple[Document, float]]]:
    """按 token 预算一次性分配 system + history + 资料，返回 (context, history, 实际资料)。

    顺序是「先扣 system，再给历史最多 40% 额度，剩下的全部给资料」。
    历史没用满时，未用额度回流给资料 —— 首轮问答因此能拿到接近完整的检索结果
    （旧实现固定切走 40%，默认配置下 5 条资料只有约 2 条进得去）。
    第三项是真正进入提示词的资料，sources 与引用编号必须基于它生成。
    """
    budget = _context_budget()
    sys_tokens = _est_tokens(system_prompt)
    remaining = max(budget - sys_tokens, int(budget * 0.3))
    history, hist_used = _trim_history_to_budget(history, int(remaining * 0.4))
    ctx_budget = max(remaining - hist_used, 1)
    kept = _select_docs_within_budget(docs, ctx_budget)
    return _format_context(kept), history, kept


def recommend_titles(docs: list[tuple[Document, float]], limit: int = 3, spread: float = 0.15) -> list[str]:
    """从检索结果提取相近资料标题，用于拒答时引导换问法。

    只取与首选结果相似度接近的（相似度差异不超过 spread）；首选相似度过低说明整批是噪声。
    """
    if not docs:
        return []
    top = _max_similarity(docs)
    # 只拦「整批是噪声」：top1 相似度连阈值下限都够不到，说明库里没有相近内容。
    # 之前用 top >= UNMATCHED_SIMILARITY + 0.15 当门槛，而门控拒答是 top < UNMATCHED_SIMILARITY，
    # 两个区间不相交 → 拒答时 recommended 恒空，「拒答推荐相近资料」功能形同虚设。
    # 这里改成阈值下侧：只要 top1 不低于「阈值 - 0.1」，就说明有沾边的资料，值得推荐。
    if top < settings.UNMATCHED_SIMILARITY - 0.1:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for doc, score in docs:
        if score < top - spread:
            continue
        src = doc.metadata
        title = (src.get("title") or src.get("filename") or "").strip()
        if title and title not in seen:
            seen.add(title)
            out.append(title)
        if len(out) >= limit:
            break
    return out


async def is_course_related(question: str, timeout: float = 10.0) -> bool:
    """轻量判定：该问题是否与课程学习相关。

    规则优先：先判明显无关，再看课程术语，其余交给 LLM 二分类；LLM 失败保守返回 False。
    """
    q = (question or "").lower()
    blocked = any(k.lower() in q for k in IRRELEVANT_KEYWORDS)
    strong = any(k.lower() in q for k in COURSE_STRONG_KEYWORDS)
    # 只命中无关词 → 不相关；只命中课程词 → 相关；两组都命中交给模型判
    if blocked and not strong:
        return False
    if strong and not blocked:
        return True
    try:
        llm = get_llm()
        resp = await asyncio.wait_for(
            llm.ainvoke(
                [
                    (
                        "system",
                        "你是课程智能助教的问题过滤器。判断下面的问题是否与课程学习相关"
                        "（计算机、数据结构、操作系统、计算机网络、计算机组成原理等课程知识）。"
                        "只回答一个字：是 或 否。",
                    ),
                    ("human", question),
                ]
            ),
            timeout=timeout,
        )
        text = (getattr(resp, "content", "") or "").strip()
        return text.startswith("是")
    except Exception:  # noqa: BLE001
        logger.warning("课程相关性判定失败（%s），按不相关处理", question[:30])
        return False


async def can_answer(
    question: str,
    docs: list[tuple[Document, float]],
    timeout: float | None = None,
) -> Answerability:
    """独立判一次“检索到的资料能不能直接回答这个问题”。

    距离门控只能拦"库里明显没有"；拦不住"库里看着像有、其实没讲"（资料讲二叉树、
    学生问红黑树）。让生成模型自己克制没用 —— 实测 3B 模型会照着相近资料把答案编下去，
    所以拆成一个只输出"有/没有"的窄任务。

    模型明确回答“没有”才是 UNANSWERABLE；超时、空响应或无法解析都返回 ERROR。
    ERROR 是“增强判定不可用”，由上层提示重试，不能降级为无校验生成。
    """
    timeout = settings.ANSWERABILITY_TIMEOUT if timeout is None else timeout
    try:
        llm = get_llm(num_predict=8, streaming=False)
        # 判定器看的证据必须覆盖真正可能进 prompt 的资料：只喂前 3 条、
        # 再截到 1000 字符（约 1 条 chunk）会把「答案在第 2/3 条」误判成未收录。
        context = _format_context(_trim_docs(docs))
        max_chars = settings.ANSWERABILITY_MAX_CONTEXT_CHARS
        if len(context) > max_chars:
            context = context[:max_chars] + "\n…（判定上下文已截断）"
        resp = await asyncio.wait_for(
            llm.ainvoke(
                [
                    ("system", ANSWERABILITY_PROMPT),
                    ("human", f"学生的问题：{question}\n\n检索到的资料：\n{context}"),
                ]
            ),
            timeout=timeout,
        )
        text = (getattr(resp, "content", "") or "").strip()
        if text.startswith(("无法", "不能确定", "不确定")):
            return Answerability.ERROR
        if text.startswith("有"):
            return Answerability.ANSWERABLE
        if text.startswith(("没有", "无", "否")):
            return Answerability.UNANSWERABLE
        logger.warning("资料可答性判定返回无法解析的结果（%s）：%r", question[:30], text[:40])
        return Answerability.ERROR
    except asyncio.TimeoutError:
        logger.warning("资料可答性判定超时（%s）", question[:30])
        return Answerability.ERROR
    except Exception as exc:  # noqa: BLE001
        if _is_model_resource_error(exc):
            logger.warning(
                "资料可答性判定失败：本地模型内存不足（%s）",
                question[:30],
                exc_info=True,
            )
            return Answerability.RESOURCE_ERROR
        logger.warning("资料可答性判定失败（%s）", question[:30], exc_info=True)
        return Answerability.ERROR


def _trim_docs(docs: list[tuple[Document, float]], ratio: float | None = None) -> list[tuple[Document, float]]:
    """只保留与首选结果相似度接近的资料。

    用相对判据（与 top1 相似度之比）而非绝对加法：绝对 spread 在余弦相似度空间里
    物理含义随 embedding 模型漂移，换模型必然失效；比值/分位数才跨模型稳定。
    至少保留一条。
    """
    if not docs:
        return []
    ratio = ratio if ratio is not None else settings.TRIM_RATIO
    top = _max_similarity(docs)
    if top <= 0:
        return docs[:1]
    kept = [d for d in docs if d[1] >= top * ratio]
    return kept or docs[:1]


def overview_context(courses: list[str] | None, per_base: int = 6) -> str:
    """概览问题不走向量检索：按可见范围直接汇总知识库与其知识点标题。"""
    from sqlalchemy import select

    from ..database import SessionLocal
    from ..models import KnowledgeBase, KnowledgePoint

    allowed = set(courses) if courses is not None else None
    lines: list[str] = []
    with SessionLocal() as db:
        names = [
            n for (n,) in db.execute(select(KnowledgeBase.name).order_by(KnowledgeBase.id))
        ]
        if allowed is not None:
            names = [n for n in names if n in allowed]
        for name in names[:12]:
            titles = [
                t
                for (t,) in db.execute(
                    select(KnowledgePoint.title)
                    .where(KnowledgePoint.course == name)
                    .order_by(KnowledgePoint.id)
                    .limit(per_base)
                )
            ]
            lines.append(
                f"课程《{name}》：{'、'.join(titles) if titles else '（暂无知识点）'}"
            )
    return "\n".join(lines)


def build_prompt_messages(
    system_prompt: str, history: list[tuple[str, str]], question: str
) -> list:
    """装配提示词：系统提示仍是带 {context} 的模板，历史和问题用消息对象。

    历史如果也用 ("user", "字符串") 元组拼，ChatPromptTemplate 会把字符串当模板解析：
    上一轮回答里只要出现花括号（代码块、JSON、集合记号），格式化时就 KeyError，
    整轮问答直接失败，报错还只是一句“回答生成失败：' '”。
    """
    messages: list = [("system", system_prompt)]
    for role, content in (history or [])[-settings.HISTORY_LIMIT * 2 :]:
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=question))
    return messages

_STREAM_ATTEMPTS = 2


def _is_connection_error(exc: BaseException) -> bool:
    """是否为连接层错误（可安全重试）。超时算在内，但调用方已用 deadline 包住。"""
    import httpx

    return isinstance(
        exc,
        (ConnectionError, httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError),
    )


async def _generate_stream(
    system_prompt: str,
    context: str,
    question: str,
    history: list[tuple[str, str]],
    finish_info: dict | None = None,
) -> AsyncGenerator[str, None]:
    """按给定提示词流式产出文本（超时抛 asyncio.TimeoutError，由调用方处理）。

    finish_info：可选的可变容器，生成结束后写入 {"finish_reason": ...}。
    finish_reason == "length" 表示被 num_predict 硬切，回答可能不完整。
    """
    chain = ChatPromptTemplate.from_messages(
        build_prompt_messages(system_prompt, history, question)
    ) | get_llm()
    last_meta: dict = {}
    # 连接层抖动（Ollama 正在换模型、端口刚起、连接被重置）不该变成「回答失败」：
    # 只要还没吐出第一个字，就重建一次链再试。一旦开始输出就不重试，否则会重复生成。
    for attempt in range(1, _STREAM_ATTEMPTS + 1):
        emitted = False
        try:
            async for chunk in _astream_with_deadline(
                chain,
                {"context": context, "question": question},
                settings.LLM_TIMEOUT,
            ):
                text = getattr(chunk, "content", "")
                meta = getattr(chunk, "response_metadata", None) or {}
                if meta:
                    last_meta = meta
                if isinstance(text, str) and text:
                    emitted = True
                    yield text
        except Exception as exc:  # noqa: BLE001 - 只在连接层错误时重试
            if emitted or attempt >= _STREAM_ATTEMPTS or not _is_connection_error(exc):
                raise
            logger.warning("连接模型失败，重建后重试（第 %s 次）：%s", attempt, exc)
            await asyncio.sleep(0.5)
            chain = ChatPromptTemplate.from_messages(
                build_prompt_messages(system_prompt, history, question)
            ) | get_llm()
            continue
        break
    if finish_info is not None:
        # langchain_ollama 的 response_metadata 里完成原因是 done_reason（取值 stop/length/load），
        # 不是 finish_reason。之前拼错字段名，truncated 恒为 False，「回答可能不完整」永远不提示。
        finish_info["finish_reason"] = (
            last_meta.get("done_reason") or last_meta.get("finish_reason")
        )


def _unmatched_result(question: str, docs: list[tuple[Document, float]]) -> dict:
    """未收录的统一结果（非流式路径用）。"""
    recommended = recommend_titles(docs)
    content = "【知识库未收录】知识库里暂时没有" + (
        f"「{question}」" if len(question) <= 40 else "该问题"
    ) + "的相关内容。\n" + "\n".join(feedback_lines(question, recommended))
    return {"answer": content, "recommended": recommended, "matched": False}


async def _generate_answer_locked(
    question: str, courses: list[str] | None = None
) -> dict:
    """非流式生成一次完整回答（供管理员补充资料后"重新生成回答"使用）。

    返回 {"answer", "recommended", "matched", "error"}：matched=False 表示仍未命中；
    error="timeout"/"empty" 表示生成失败，调用方不能当已解决写入。
    """
    # 向量化 + Chroma 查询是同步 IO，放线程里跑，避免阻塞事件循环
    await asyncio.to_thread(unload_ollama_llm)
    docs = await asyncio.to_thread(vector_search, question, settings.TOP_K, courses)
    if not docs:
        return {
            "answer": "知识库里还没有相关内容，先补充课程资料再试。",
            "recommended": [],
            "matched": False,
        }
    if _max_similarity(docs) < settings.UNMATCHED_SIMILARITY:
        logger.info(
            "非流式检索未命中 top_similarity=%.4f threshold=%.2f docs=%d question=%s",
            _max_similarity(docs),
            settings.UNMATCHED_SIMILARITY,
            len(docs),
            question[:40],
        )
        return _unmatched_result(question, docs)
    if settings.ANSWERABILITY_CHECK:
        answerability = await can_answer(question, docs)
        if answerability is Answerability.RESOURCE_ERROR:
            return {
                "answer": "",
                "recommended": [],
                "matched": False,
                "error": "resource",
            }
        if answerability is Answerability.ERROR:
            # 与流式路径保持一致：判定器不可用只降级，不当成「资料不可答」
            logger.warning("资料可答性判定不可用（ERROR），降级为直接生成（非流式）")
        elif answerability is Answerability.UNANSWERABLE:
            return _unmatched_result(question, docs)

    kept = _trim_docs(docs)
    # token 预算：一次性分配 system + history + 资料；
    # 返回的 kept 才是真正进入提示词的资料，角标与来源列表都以它为准
    context, history, kept = _build_messages(SYSTEM_PROMPT, kept, question, [])
    answer_parts: list[str] = []
    try:
        async for chunk in _generate_stream(SYSTEM_PROMPT, context, question, history):
            answer_parts.append(chunk)
    except asyncio.TimeoutError:
        # 超时提示不是答案，不混进 answer
        return {"answer": "", "recommended": [], "matched": False, "error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("非流式回答生成失败")
        if _is_model_resource_error(exc):
            return {"answer": "", "recommended": [], "matched": False, "error": "resource"}
        return {"answer": "", "recommended": [], "matched": False, "error": "generation"}
    answer = "".join(answer_parts).strip()
    if not answer:
        return {"answer": "", "recommended": [], "matched": False, "error": "empty"}

    # 模型判定未收录时同样追加推荐与反馈提示
    if is_unmatched_answer(answer):
        recommended = recommend_titles(docs)
        answer += "\n\n" + "\n".join(feedback_lines(question, recommended))
        return {"answer": answer, "recommended": recommended, "matched": False}

    return {"answer": answer, "recommended": [], "matched": True}


# 同时最多 _SLOT_COUNT 路生成：本机模型一次只吐一路 token
_SLOT_COUNT = 3
# 排队期间每隔这么久发一个 ping，让前端重置空闲计时
_QUEUE_PING_SECONDS = 10.0
# 排队上限：超时后明确回「人多，稍后再试」
_QUEUE_MAX_WAIT = 120.0

# 闸门按事件循环缓存：asyncio 信号量第一次等过之后就跟 loop 绑死，
# pytest 每个 asyncio.run() 都是新循环，不能用模块级单个 Semaphore
_slot_cache: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _llm_slots() -> asyncio.Semaphore:
    """当前事件循环的生成闸门（同时最多 _SLOT_COUNT 路）。"""
    loop = asyncio.get_running_loop()
    sem = _slot_cache.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_SLOT_COUNT)
        _slot_cache[loop] = sem
    return sem


async def generate_answer(question: str, courses: list[str] | None = None) -> dict:
    """非流式生成（管理员「生成回答」用）：和流式共用同一个并发闸门，最多等 _QUEUE_MAX_WAIT 秒。"""
    sem = _llm_slots()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=_QUEUE_MAX_WAIT)
    except asyncio.TimeoutError:
        return {"answer": "", "recommended": [], "matched": False, "error": "busy"}
    try:
        return await _generate_answer_locked(question, courses)
    finally:
        sem.release()


async def stream_answer(
    question: str,
    history: list[tuple[str, str]] | None = None,
    courses: list[str] | None = None,
    still_wanted: Callable[[], bool] | None = None,
) -> AsyncGenerator[dict, None]:
    """带并发闸门的流式回答生成器：拿不到槽位时先发心跳，再排队。

    产出事件同 _stream_answer_locked，额外的 {"type": "ping"} 只是保活，不参与答案。
    排队超过 _QUEUE_MAX_WAIT 还没轮到，回一条 error 让前端别再转圈。

    still_wanted：可选回调，返回 False 表示这一轮已经被同会话的新提问顶掉了。
    排队期间和拿到槽位后各查一次——否则同一会话的两个请求里，旧的那个还要占着
    一路模型把答案生成完，最后才发现序号不符、整个丢掉。

    回调内部要查 SQLite（生成序号），必须丢到线程里执行：在事件循环线程里直接调
    会在数据库被锁时把整个进程的 async 端点一起冻住（P0-3）。
    """
    sem = _llm_slots()
    deadline = time.monotonic() + _QUEUE_MAX_WAIT
    while True:
        if still_wanted is not None and not await asyncio.to_thread(still_wanted):
            return
        try:
            await asyncio.wait_for(sem.acquire(), timeout=_QUEUE_PING_SECONDS)
            break
        except asyncio.TimeoutError:
            if time.monotonic() >= deadline:
                yield {
                    "type": "error",
                    "message": (
                        f"当前提问人数较多，排队超过 {_QUEUE_MAX_WAIT:.0f} 秒还没轮到，"
                        "请稍后再发一次。"
                    ),
                }
                return
            yield {"type": "ping"}
    if still_wanted is not None and not await asyncio.to_thread(still_wanted):
        # 排队期间被顶掉了：把刚拿到的槽位还回去，不生成
        sem.release()
        return
    agen = _stream_answer_locked(question, history, courses)
    try:
        async for event in agen:
            yield event
    finally:
        # 断开时关掉里层生成器；槽位在嵌套 finally 里释放，避免 aclose 抛异常时漏掉
        try:
            await agen.aclose()
        finally:
            sem.release()


def _timeout_message() -> str:
    return f"回答生成超时（超过 {settings.LLM_TIMEOUT:.0f} 秒），稍后再试或者换个问法。"


def model_resource_error_message() -> str:
    return "本地模型内存不足，无法加载并完成这次回答。请关闭占用内存较大的程序，或换用更小的模型后再试。"


def _phase_event(phase: str, status: str, started: float) -> dict:
    return {
        "type": "phase",
        "phase": phase,
        "status": status,
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _unmatched_content(question: str, recommended: list[str]) -> str:
    """未收录拒答正文：知识库暂缺 + 相近资料推荐 + 反馈提示。"""
    head = f"「{question}」" if len(question) <= 40 else "该问题"
    return (
        f"【知识库未收录】知识库里暂时没有{head}的相关内容。\n"
        + "\n".join(feedback_lines(question, recommended))
    )


def _unmatched_event(question: str, course: str, recommended: list[str]) -> dict:
    """发出 unmatched 事件（供调用方落库记录未命中问题）。"""
    return {"type": "unmatched", "question": question, "course": course, "recommended": recommended}


async def _handle_smalltalk(question: str) -> AsyncGenerator[dict, None]:
    """寒暄/客套/自我介绍：本地识别，直接回引导语，不检索、不调模型、不记未命中。"""
    yield {"type": "token", "content": SMALLTALK_REPLY}
    yield {"type": "sources", "sources": [], "answer": SMALLTALK_REPLY}


async def _handle_overview(
    question: str, history: list[tuple[str, str]], courses: list[str] | None
) -> AsyncGenerator[dict, None]:
    """概览/能力类问题：汇总知识库与知识点标题，不判未收录、不给推荐。"""
    context = await asyncio.to_thread(overview_context, courses)
    answer_parts: list[str] = []
    if context:
        try:
            async for chunk in _generate_stream(OVERVIEW_PROMPT, context, question, history):
                answer_parts.append(chunk)
                yield {"type": "token", "content": chunk}
        except asyncio.TimeoutError:
            logger.warning("概览回答生成超时：%s", question[:30])
            yield {"type": "error", "message": _timeout_message()}
            return
    answer = "".join(answer_parts).strip()
    if not answer:
        answer = "知识库里还没有可以介绍的内容，先让老师补充课程资料。"
        yield {"type": "token", "content": answer}
    yield {"type": "sources", "sources": [], "answer": answer}


async def _handle_unmatched(
    question: str, docs: list[tuple[Document, float]]
) -> AsyncGenerator[dict, None]:
    """知识库未覆盖该问题：先判课程相关性，学习相关才记录反馈并拒答。"""
    if not await is_course_related(question):
        # 明确跑题的问题只拒答，不进未命中反馈池（避免天气/闲聊污染统计）；
        # 仍打 outcome 标记，便于区分「跑题」与「知识库缺资料」。
        yield {"type": "outcome", "outcome": "out_of_scope"}
        yield {"type": "token", "content": "这个问题不在我的回答范围内，我只解答课程知识库中的内容。"}
        return
    course = docs[0][0].metadata.get("course") or ""
    recommended = recommend_titles(docs)
    yield {"type": "token", "content": _unmatched_content(question, recommended)}
    yield _unmatched_event(question, course, recommended)


async def _handle_answerability_gate(
    question: str, docs: list[tuple[Document, float]]
) -> AsyncGenerator[dict, None]:
    """距离门控拦不住、但资料其实没讲这个知识点时，再独立判一次。"""
    recommended = recommend_titles(docs)
    yield {"type": "token", "content": _unmatched_content(question, recommended)}
    yield _unmatched_event(question, docs[0][0].metadata.get("course") or "", recommended)


async def _handle_generate(
    question: str,
    history: list[tuple[str, str]],
    kept: list[tuple[Document, float]],
    phase_started: float | None = None,
) -> AsyncGenerator[dict, None]:
    """基于检索资料流式生成回答，并在模型判「未收录」时追加推荐与反馈。"""
    gen_started = time.monotonic()
    # token 预算：一次性分配 system + history + 资料，三者之和不再超窗。
    # 返回的 kept 是实际进入提示词的资料，后面的 sources / 引用校验必须用它，
    # 否则会出现「模型没读过 [4][5]，前端却把它们列为来源」。
    context, history, kept = _build_messages(SYSTEM_PROMPT, kept, question, history)
    answer_parts: list[str] = []
    finish_info: dict = {}
    try:
        async for chunk in _generate_stream(SYSTEM_PROMPT, context, question, history, finish_info):
            answer_parts.append(chunk)
            yield {"type": "token", "content": chunk}
    except asyncio.TimeoutError:
        logger.warning("RAG 回答生成超过 %.0f 秒，已强制停止", settings.LLM_TIMEOUT)
        yield {"type": "error", "message": _timeout_message()}
        return

    answer = "".join(answer_parts).strip()
    # 被 num_predict 硬切：回答可能不完整，前端应提示而非当完整答案
    truncated = finish_info.get("finish_reason") == "length"
    if not answer:
        answer = "这次没生成出有效回答。检查一下模型服务，或者换个问法再问。"
        yield {"type": "token", "content": answer}

    if is_unmatched_answer(answer):
        recommended = recommend_titles(kept)
        suffix = "\n\n" + "\n".join(feedback_lines(question, recommended))
        answer += suffix
        yield {"type": "token", "content": suffix}
        yield _unmatched_event(question, kept[0][0].metadata.get("course") or "", recommended)

    logger.info(
        "阶段耗时 generation=%.2fs chars=%d truncated=%s",
        time.monotonic() - gen_started,
        len(answer),
        truncated,
    )
    _sources_payload = _format_sources(kept)
    yield {
        "type": "sources",
        "sources": _sources_payload,
        "answer": answer,
        "truncated": truncated,
        "citation_stats": _citation_stats(answer, len(kept), _sources_payload),
    }
    if phase_started is not None:
        yield _phase_event("generation", "done", phase_started)


async def _stream_answer_locked(
    question: str,
    history: list[tuple[str, str]] | None = None,
    courses: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """流式回答生成器。

    产出事件：
      {"type": "token", "content": str}        逐字 token
      {"type": "sources", "sources": [...],    结束：来源列表
       "answer": str}
      {"type": "unmatched", ...}               知识库未覆盖，需落库记录
      {"type": "error", "message": str}        失败

    courses 非空时，只基于这些知识库（课程）检索回答。
    """
    turn_started = time.monotonic()
    history = history or []
    try:
        # -1. 寒暄 / 概览：不走向量检索
        if is_smalltalk(question):
            async for event in _handle_smalltalk(question):
                yield event
            return
        if is_course_overview_question(question):
            async for event in _handle_overview(question, history, courses):
                yield event
            return

        # 1. 检索（多轮追问先做 query 改写，把指代补全再检索）
        retrieval_started = time.monotonic()
        yield _phase_event("retrieval", "running", retrieval_started)
        search_query = await _rewrite_query(question, history)
        if search_query != question:
            logger.info("查询改写：%s → %s", question[:30], search_query[:30])
        await asyncio.to_thread(unload_ollama_llm)
        docs = await asyncio.to_thread(vector_search, search_query, settings.TOP_K, courses)
        yield _phase_event("retrieval", "done", retrieval_started)
        logger.info(
            "阶段耗时 retrieval=%.2fs docs=%d courses=%s",
            time.monotonic() - retrieval_started,
            len(docs),
            courses,
        )
        # 知识库没有任何内容时直接拒绝，不调模型
        if not docs:
            yield {"type": "outcome", "outcome": "empty_kb"}
            yield {
                "type": "token",
                "content": "知识库里还没有相关内容，先添加课程资料，或者问点跟课程有关的问题。",
            }
            return

        # 2. 无匹配门控：最相似资料的相似度仍低于阈值 → 知识库未覆盖该问题
        top_similarity = _max_similarity(docs)
        logger.info(
            "检索结果 top_similarity=%.4f threshold=%.2f docs=%d question=%s",
            top_similarity,
            settings.UNMATCHED_SIMILARITY,
            len(docs),
            question[:40],
        )
        if top_similarity < settings.UNMATCHED_SIMILARITY:
            async for event in _handle_unmatched(question, docs):
                yield event
            return

        # 3. 可答性二次判定：距离门控拦不住的相近未覆盖，再独立判一次
        if settings.ANSWERABILITY_CHECK:
            answerability_started = time.monotonic()
            yield _phase_event("answerability", "running", answerability_started)
            answerability = await can_answer(question, docs)
            # 资源不足是系统错误，仍须明确报错；但「判定器超时/返回不可解析」只是
            # 增强判定不可用，不等于资料不可答。以前这里一律拒绝生成，一次分类器
            # 抖动就把可答问题变成失败页，与 .env.example 里承诺的降级行为相反。
            if answerability is Answerability.RESOURCE_ERROR:
                logger.warning(
                    "资料可答性判定不可用（%s），拒绝生成：%s",
                    answerability.value,
                    question[:30],
                )
                yield _phase_event("answerability", "error", answerability_started)
                yield {"type": "error", "message": model_resource_error_message()}
                return
            if answerability is Answerability.ERROR:
                logger.warning(
                    "资料可答性判定不可用（ERROR），降级为直接生成：%s", question[:30]
                )
                yield _phase_event("answerability", "degraded", answerability_started)
            else:
                yield _phase_event("answerability", "done", answerability_started)
                logger.info(
                    "阶段耗时 answerability=%.2fs result=%s",
                    time.monotonic() - answerability_started,
                    answerability.value,
                )
            if answerability is Answerability.UNANSWERABLE:
                async for event in _handle_answerability_gate(question, docs):
                    yield event
                return

        # 4. 引用资料收紧（离题的不参与生成），再流式生成
        kept = _trim_docs(docs)
        generation_started = time.monotonic()
        yield _phase_event("generation", "running", generation_started)
        async for event in _handle_generate(
            question, history, kept, phase_started=generation_started
        ):
            yield event
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG 流式回答失败")
        if _is_model_resource_error(exc):
            yield {"type": "error", "message": model_resource_error_message()}
            return
        err = str(exc)
        # 模型缺失（Ollama 404）：给一句人话，细节写日志
        if "404" in err or "model not found" in err.lower():
            logger.error("模型未安装：%s", settings.OLLAMA_LLM_MODEL)
            yield {
                "type": "error",
                "message": "模型服务还没准备好，这次答不了。过一会儿再试，一直这样的话找老师看一下。",
            }
            return
        yield {
            "type": "error",
            "message": "回答生成失败，请稍后重试；如果一直失败，请联系老师查看服务日志。",
        }
    finally:
        # 单轮总耗时只做观测，用来定位「慢在检索 / 可答性判定 / 生成」哪一段（P1-9）
        logger.info(
            "阶段耗时 total=%.2fs question=%s",
            time.monotonic() - turn_started,
            question[:30],
        )
