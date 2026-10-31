"""RAG 核心服务：检索增强生成（流式）。

流程：用户问题 → 向量检索 Top-K → 组装系统提示（参考资料+对话历史）
      → 大模型流式生成 → 输出 token 与引用来源。
"""
from __future__ import annotations

import asyncio
import logging
import time
import weakref
from collections.abc import AsyncGenerator, Callable

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from ..config import settings
from .llm_service import get_llm
from .rag_prompts import (
    ANSWERABILITY_PROMPT,
    COURSE_STRONG_KEYWORDS,
    IRRELEVANT_KEYWORDS,
    OVERVIEW_PROMPT,
    SMALLTALK_REPLY,
    SYSTEM_PROMPT,
    feedback_lines,
    is_course_overview_question,
    is_smalltalk,
    is_unmatched_answer,
)
from .vector_store import search as vector_search

logger = logging.getLogger(__name__)


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


def _format_context(docs: list[tuple[Document, float]]) -> str:
    """把检索结果格式化为提示词中的参考文本。"""
    lines: list[str] = []
    for i, (doc, _score) in enumerate(docs, start=1):
        src = doc.metadata
        title = src.get("title") or src.get("filename") or "未知来源"
        course = src.get("course") or "未分类"
        lines.append(f"[{i}] 来源《{title}》（课程：{course}）\n{doc.page_content}")
    return "\n\n".join(lines)


def _format_sources(docs: list[tuple[Document, float]]) -> list[dict]:
    """输出给前端的引用来源（含摘要，便于展示）。"""
    out: list[dict] = []
    for _i, (doc, _score) in enumerate(docs, start=1):
        src = doc.metadata
        out.append(
            {
                "source_type": src.get("source_type", "document"),
                "doc_id": src.get("doc_id"),
                "point_id": src.get("point_id"),
                "title": src.get("title") or src.get("filename") or "未知来源",
                "course": src.get("course") or "未分类",
                "content": doc.page_content[:200],
            }
        )
    return out


def recommend_titles(docs: list[tuple[Document, float]], limit: int = 3, spread: float = 0.15) -> list[str]:
    """从检索结果提取相近资料标题，用于拒答时引导换问法。

    只取与首选结果距离接近的；首选结果离题太远（超过阈值 + spread）说明整批是噪声。
    """
    if not docs:
        return []
    top = docs[0][1]
    if top > settings.UNMATCHED_DISTANCE + 0.15:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for doc, score in docs:
        if score > top + spread:
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


async def can_answer(question: str, docs: list[tuple[Document, float]], timeout: float = 30.0) -> bool:
    """独立判一次"检索到的资料能不能直接回答这个问题"。

    距离门控只能拦"库里明显没有"；拦不住"库里看着像有、其实没讲"（资料讲二叉树、
    学生问红黑树）。让生成模型自己克制没用 —— 实测 3B 模型会照着相近资料把答案编下去，
    所以拆成一个只输出"有/没有"的窄任务，判定失败或超时一律按不能答处理（宁可拒答，不要编造）。
    """
    try:
        llm = get_llm()
        resp = await asyncio.wait_for(
            llm.ainvoke(
                [
                    ("system", ANSWERABILITY_PROMPT),
                    ("human", f"学生的问题：{question}\n\n检索到的资料：\n{_format_context(docs)}"),
                ]
            ),
            timeout=timeout,
        )
        text = (getattr(resp, "content", "") or "").strip()
        return text.startswith("有")
    except Exception:  # noqa: BLE001
        logger.warning("资料可答性判定失败（%s），按不可答处理", question[:30])
        return False


def _trim_docs(docs: list[tuple[Document, float]], spread: float = 0.25) -> list[tuple[Document, float]]:
    """只保留与首选结果距离接近的资料。

    门控只看 top1，后面几条可能是离题内容（问死锁却引用"无穷级数"），
    拿去拼提示词和引用来源只会干扰回答。至少保留一条。
    """
    if not docs:
        return []
    top = docs[0][1]
    kept = [d for d in docs if d[1] <= top + spread]
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

async def _generate_stream(
    system_prompt: str,
    context: str,
    question: str,
    history: list[tuple[str, str]],
) -> AsyncGenerator[str, None]:
    """按给定提示词流式产出文本（超时抛 asyncio.TimeoutError，由调用方处理）。"""
    chain = ChatPromptTemplate.from_messages(
        build_prompt_messages(system_prompt, history, question)
    ) | get_llm()
    async for chunk in _astream_with_deadline(
        chain,
        {"context": context, "question": question},
        settings.LLM_TIMEOUT,
    ):
        text = getattr(chunk, "content", "")
        if isinstance(text, str) and text:
            yield text


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
    docs = await asyncio.to_thread(vector_search, question, settings.TOP_K, courses)
    if not docs:
        return {
            "answer": "知识库里还没有相关内容，先补充课程资料再试。",
            "recommended": [],
            "matched": False,
        }
    if docs[0][1] > settings.UNMATCHED_DISTANCE:
        return _unmatched_result(question, docs)
    if settings.ANSWERABILITY_CHECK and not await can_answer(question, docs):
        return _unmatched_result(question, docs)

    kept = _trim_docs(docs)
    context = _format_context(kept)
    answer_parts: list[str] = []
    try:
        async for chunk in _generate_stream(SYSTEM_PROMPT, context, question, []):
            answer_parts.append(chunk)
    except asyncio.TimeoutError:
        # 超时提示不是答案，不混进 answer
        return {"answer": "", "recommended": [], "matched": False, "error": "timeout"}
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
    """
    sem = _llm_slots()
    deadline = time.monotonic() + _QUEUE_MAX_WAIT
    while True:
        if still_wanted is not None and not still_wanted():
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
    if still_wanted is not None and not still_wanted():
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
) -> AsyncGenerator[dict, None]:
    """基于检索资料流式生成回答，并在模型判「未收录」时追加推荐与反馈。"""
    context = _format_context(kept)
    answer_parts: list[str] = []
    try:
        async for chunk in _generate_stream(SYSTEM_PROMPT, context, question, history):
            answer_parts.append(chunk)
            yield {"type": "token", "content": chunk}
    except asyncio.TimeoutError:
        logger.warning("RAG 回答生成超过 %.0f 秒，已强制停止", settings.LLM_TIMEOUT)
        yield {"type": "error", "message": _timeout_message()}
        return

    answer = "".join(answer_parts).strip()
    if not answer:
        answer = "这次没生成出有效回答。检查一下模型服务，或者换个问法再问。"
        yield {"type": "token", "content": answer}

    if is_unmatched_answer(answer):
        recommended = recommend_titles(kept)
        suffix = "\n\n" + "\n".join(feedback_lines(question, recommended))
        answer += suffix
        yield {"type": "token", "content": suffix}
        yield _unmatched_event(question, kept[0][0].metadata.get("course") or "", recommended)

    yield {"type": "sources", "sources": _format_sources(kept), "answer": answer}


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

        # 1. 检索
        docs = await asyncio.to_thread(vector_search, question, settings.TOP_K, courses)
        # 知识库没有任何内容时直接拒绝，不调模型
        if not docs:
            yield {
                "type": "token",
                "content": "知识库里还没有相关内容，先添加课程资料，或者问点跟课程有关的问题。",
            }
            return

        # 2. 无匹配门控：最相似资料的距离仍超过阈值 → 知识库未覆盖该问题
        if docs[0][1] > settings.UNMATCHED_DISTANCE:
            async for event in _handle_unmatched(question, docs):
                yield event
            return

        # 3. 可答性二次判定：距离门控拦不住的相近未覆盖，再独立判一次
        if settings.ANSWERABILITY_CHECK and not await can_answer(question, docs):
            async for event in _handle_answerability_gate(question, docs):
                yield event
            return

        # 4. 引用资料收紧（离题的不参与生成），再流式生成
        kept = _trim_docs(docs)
        async for event in _handle_generate(question, history, kept):
            yield event
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG 流式回答失败")
        err = str(exc)
        # 模型缺失（Ollama 404）：给一句人话，细节写日志
        if "404" in err or "model not found" in err.lower():
            logger.error("模型未安装：%s", settings.OLLAMA_LLM_MODEL)
            yield {
                "type": "error",
                "message": "模型服务还没准备好，这次答不了。过一会儿再试，一直这样的话找老师看一下。",
            }
            return
        yield {"type": "error", "message": f"回答生成失败：{exc}"}
