"""RAG 核心服务：检索增强生成（流式）。

流程：用户问题 → 向量检索 Top-K → 组装系统提示（参考资料+对话历史）
      → 大模型流式生成 → 输出 token 与引用来源。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncGenerator

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from ..config import settings
from .llm_service import get_llm
from .vector_store import search as vector_search

logger = logging.getLogger(__name__)

# 模型判定“知识库未收录”时的特征短语（模型输出格式可能不完全受控，用模糊匹配兜底）
UNMATCHED_MARKERS = (
    "【知识库未收录】",
    "知识库未收录",
    "知识库中暂无相关内容",
    "知识库暂无相关内容",
    "知识库中未收录",
    "知识库未覆盖",
)


def is_unmatched_answer(answer: str) -> bool:
    """判断模型回答是否属于"知识库未收录"拒答（前 40 字内出现特征短语即判定）。"""
    head = (answer or "").strip()[:40]
    return any(m in head for m in UNMATCHED_MARKERS)

SYSTEM_PROMPT = """你是这门课的助教，正在回答学生的提问。用平常说话的方式讲清楚，别端着。

回答规则：
1. 只聊课程知识库里的内容（知识点、作业习题、学习资料）。碰到明显无关的问题（考公择业、天气、时事、娱乐、个人生活决策、闲聊之类），只回一句：“这个问题不在我的回答范围内，我只解答课程知识库中的内容。” 不展开、不建议、不猜测。
2. 在范围内的问题，先看下面「参考资料」能不能答：
   - 能答：直接讲，用完整的句子把道理说清楚；需要分步骤时再用编号，不要为了显得“结构清晰”把本该连成一段的话拆成一堆要点。
   - 只覆盖了一部分：把能确定的讲完，再说明“知识库中关于这部分的资料不完整”。
   - 答不上来，或资料讲的根本不是这个知识点（比如资料讲二叉树、学生问红黑树）：必须以“【知识库未收录】”开头，正文写“知识库中暂无相关内容”，再一两句说明现有资料和问题的差距。绝不许用自己的知识补充或编造，也不要列举资料标题、不要承诺已经反馈老师（这些由系统追加）。
3. 不要用这些腔调：“总的来说”“综上所述”“需要注意的是”“希望对你有帮助”；不要复述学生的问题，不要开场白和收尾客套。问题短就答短，不硬凑篇幅。
4. 简体中文作答，专业术语保留英文原名，顺带给出中文说法。
5. 只依据参考资料讲，不要评论资料本身（比如“资料[1]和[2]说法一致”“资料[4]没有详细内容”）。
6. 资料为空时，直接说明知识库里还没有相关内容，引导学生补充课程资料或换个课程相关的问题问，不要凭常识编。

最容易出错的一种情况是"学生问的比资料多"，照着例子来：

  资料里是「二叉查找树」，学生问「红黑树的性质」。
  × 错的：把资料里二叉树的内容讲一遍，再凭印象把红黑树的性质补上。学生分不清哪些来自资料、哪些是你自己加的。
  ○ 对的：把资料里讲到的二叉查找树说清楚，然后明确说明"知识库中没有红黑树的内容"。红黑树的性质一个字都不要写。
  同理，问「B+ 树」而资料只有二叉树、问「协程」而资料只有进程线程，都按这个方式处理。

判断标准只有一条：这句话在「参考资料」里找得到出处吗？找不到就不要写。

参考资料：
{context}"""

# 课程概览专用提示：用户问“这门课/知识库有什么、可以干什么”时使用。
# 资料里有什么就介绍什么，禁止输出“未收录/暂无相关内容”等拒答文案。
OVERVIEW_PROMPT = """你是这门课的助教。学生想了解知识库里收了什么、能帮他做什么。

怎么答：
1. 照着下面「参考资料」里列出的知识条目说：涉及哪几门课，各有哪些知识点主题，能帮他解答哪几类问题（概念解释、算法/原理讲解、对比辨析、习题答疑等）。
2. 内容多的时候可以分条列，但每条说人话，别堆名词。先总说一句，再按课程或主题展开。
3. 资料里有什么就说什么；绝对不要说“未收录”“暂无相关内容”“知识库里没有”这类话，也不要编资料之外的知识点。
4. 简体中文。

参考资料：
{context}"""


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


def recommend_titles(docs: list[tuple[Document, float]], limit: int = 3) -> list[str]:
    """从检索结果中提取最相关的资料标题（去重），用于拒答时引导换问法。"""
    seen: set[str] = set()
    out: list[str] = []
    for doc, _score in docs:
        src = doc.metadata
        title = (src.get("title") or src.get("filename") or "").strip()
        if title and title not in seen:
            seen.add(title)
            out.append(title)
        if len(out) >= limit:
            break
    return out


def feedback_lines(question: str, recommended: list[str]) -> list[str]:
    """拒答文案的相近资料推荐与反馈提示（通道1/通道2 共用，由系统追加）。"""
    lines: list[str] = []
    if recommended:
        titles = "、".join(f"《{t}》" for t in recommended)
        lines.append(
            f"知识库里相近的还有：{titles}，可以换个问法再试试。"
        )
    else:
        lines.append("换个知识库里已有的知识点问问，或者找老师补一下相关资料。")
    lines.append("（这个问题已经记下来反馈给老师了，补上资料后会重新给你生成解答。）")
    return lines


# 明显课程术语：命中即视为课程学习相关（红黑树等，即使 LLM 误判也进入反馈）
COURSE_STRONG_KEYWORDS = (
    "红黑树", "二叉树", "二叉", "B树", "B+树", "AVL", "数据结构", "栈与队列",
    "栈", "队列", "链表", "哈希", "散列", "排序", "图论", "遍历", "算法", "递归",
    "动态规划", "贪心", "回溯", "时间复杂度", "空间复杂度", "复杂度", "操作系统",
    "进程", "线程", "协程", "死锁", "内存", "分页", "分段", "文件系统", "磁盘",
    "调度", "信号量", "计算机网络", "TCP", "UDP", "HTTP", "HTTPS", "三次握手",
    "四次挥手", "路由", "DNS", "子网", "端口", "交换机", "路由器", "计算机组成",
    "CPU", "存储器", "指令", "流水线", "寄存器", "总线", "数据库", "SQL", "索引",
    "事务", "范式", "主键", "外键", "编程", "代码", "函数", "类", "对象", "继承",
    "多态", "指针", "数组", "字符串", "变量", "循环", "软件工程", "需求分析",
    "软件测试", "版本控制", "Git", "前端", "后端", "接口", "框架", "编译", "解释",
    "知识点", "课件", "章节", "考点", "习题", "实验报告", "作业", "复习", "考试",
    "课程", "助教", "知识库", "课本", "笔记",
)
# 明显无关词：命中即视为与课程学习无关（不进入反馈）
IRRELEVANT_KEYWORDS = (
    "天气", "温度", "下雨", "考公", "公务员", "考研", "择业", "求职", "薪资",
    "简历", "面试", "新闻", "时政", "明星", "八卦", "娱乐", "电影", "电视剧",
    "综艺", "音乐", "美食", "菜谱", "旅游", "攻略", "景点", "恋爱", "情感",
    "星座", "运势", "游戏攻略", "抽卡", "健身", "减肥", "养生", "就医", "购物",
    "折扣", "优惠", "外卖", "打车",
)


async def is_course_related(question: str, timeout: float = 10.0) -> bool:
    """轻量判定：该问题是否与课程学习相关。

    规则优先：明显课程术语（红黑树/进程/TCP 等）直接判相关；
    明显无关（天气/考公/娱乐等）直接判不相关；
    其余交给 LLM 二分类。LLM 失败时保守返回 False。
    """
    if any(k in question for k in COURSE_STRONG_KEYWORDS):
        return True
    if any(k in question for k in IRRELEVANT_KEYWORDS):
        return False
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


def search_docs(
    query: str,
    top_k: int | None = None,
    courses: list[str] | None = None,
) -> list[tuple[Document, float]]:
    """对外暴露的检索接口（供诊断 / 测试使用）。"""
    return vector_search(query, top_k, courses)


def is_course_overview_question(question: str) -> bool:
    """是否"这门课讲了什么 / 知识库有什么"类课程概览问题。

    概览问题应基于检索到的已有资料直接总结，不适用"未收录"判定
    （避免"这门课讲了什么""知识库里有什么"被误判拒答或混入未收录文案）。
    """
    q = re.sub(r"\s+", "", question or "")
    markers = (
        "这门课讲了什么", "这门课学什么", "这门课讲什么", "这门课主要讲什么",
        "这门课都学什么", "这门课有哪些内容", "这门课包括哪些", "这门课有什么内容",
        "这门课学哪些", "这门课的知识点", "课程讲了什么", "课程学什么",
        "课程主要内容", "课程有哪些内容", "课程包括哪些", "知识库讲了什么",
        "知识库里有什么", "知识库有哪些内容", "知识库有什么内容", "知识库包括哪些",
        "知识库有什么知识点", "知识库收录了什么", "知识库里有什么知识点",
        "这个知识库讲了什么", "这个知识库有什么", "这个知识库有哪些内容",
        "知识库学什么", "检索知识库", "搜索知识库", "查询知识库",
        "知识库可以干什么", "知识库能干什么", "知识库能做什么", "知识库可以做什么",
        "知识库里有什么可以", "知识库有什么可以", "告诉我可以干什么", "可以干什么事情",
        "知识库里有什么内容", "知识库的内容", "知识库收录了", "知识库有哪些",
    )
    return any(m in q for m in markers)


ANSWERABILITY_PROMPT = """你是课程助教的知识库校对员。下面给你学生的问题，以及从知识库里检索到的资料。

你的任务只有一个：判断这些资料里有没有**直接讲解**学生问的这个知识点。

- 资料里有直接讲这个知识点的内容 → 回答「有」
- 资料讲的是相近但不同的知识点（例如资料讲二叉树、学生问红黑树）→ 回答「没有」
- 拿不准 → 回答「没有」

只回答两个字：有 或 没有。不要解释，不要补充知识。"""


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


async def generate_answer(question: str, courses: list[str] | None = None) -> dict:
    """非流式生成一次完整回答（供管理员补充资料后"重新生成回答"使用）。

    返回：{"answer", "recommended", "matched"}
      - matched=False 表示仍未命中知识库（空库 / 距离门控），answer 为拒答文案
    """
    docs = vector_search(question, settings.TOP_K, courses)
    if not docs:
        return {
            "answer": "知识库里还没有相关内容，先补充课程资料再试。",
            "recommended": [],
            "matched": False,
        }
    top_score = docs[0][1]
    if top_score > settings.UNMATCHED_DISTANCE:
        recommended = recommend_titles(docs)
        content = "【知识库未收录】知识库里暂时没有" + (
            f"「{question}」" if len(question) <= 40 else "该问题"
        ) + "的相关内容。\n" + "\n".join(feedback_lines(question, recommended))
        return {"answer": content, "recommended": recommended, "matched": False}

    if settings.ANSWERABILITY_CHECK and not await can_answer(question, docs):
        recommended = recommend_titles(docs)
        content = "【知识库未收录】知识库里暂时没有" + (
            f"「{question}」" if len(question) <= 40 else "该问题"
        ) + "的相关内容。\n" + "\n".join(feedback_lines(question, recommended))
        return {"answer": content, "recommended": recommended, "matched": False}

    context = _format_context(docs)
    messages: list = [("system", SYSTEM_PROMPT), ("human", "{question}")]
    prompt = ChatPromptTemplate.from_messages(messages)
    chain = prompt | get_llm()

    deadline = time.monotonic() + settings.LLM_TIMEOUT
    answer_parts: list[str] = []
    async for chunk in chain.astream({"context": context, "question": question}):
        if time.monotonic() > deadline:
            answer_parts.append("（回答生成超时了，稍后再试）")
            break
        content = getattr(chunk, "content", "")
        if isinstance(content, str) and content:
            answer_parts.append(content)
    answer = "".join(answer_parts).strip() or "这次没生成出有效回答，检查一下模型服务，或者稍后再试。"

    # 模型判定未收录时同样追加推荐与反馈提示
    if is_unmatched_answer(answer):
        recommended = recommend_titles(docs)
        answer += "\n\n" + "\n".join(feedback_lines(question, recommended))
        return {"answer": answer, "recommended": recommended, "matched": False}

    return {"answer": answer, "recommended": [], "matched": True}


async def stream_answer(
    question: str,
    history: list[tuple[str, str]] | None = None,
    courses: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """流式回答生成器。

    产出事件：
      {"type": "token", "content": str}        逐字 token
      {"type": "sources", "sources": [...],    结束：来源列表
       "answer": str}
      {"type": "error", "message": str}        失败

    courses 非空时，只基于这些知识库（课程）检索回答。
    """
    history = history or []
    try:
        # 1. 检索（可按知识库/课程限定范围）
        docs = vector_search(question, settings.TOP_K, courses)
        # 兜底：知识库没有任何内容时直接拒绝，不调用模型
        if not docs:
            yield {
                "type": "token",
                "content": "知识库里还没有相关内容，先添加课程资料，或者问点跟课程有关的问题。",
            }
            return
        # 2. 无匹配门控：最相似资料的距离仍超过阈值 → 知识库未覆盖该问题，
        #    先判定是否与课程学习相关：无关问题直接拒绝且不记录；
        #    学习相关但库无才进入“反馈”记录（避免天气/闲聊等污染反馈列表）。
        #    课程概览类问题（“这门课讲了什么”“知识库有什么”）不走门控，
        #    直接基于检索到的资料总结。
        overview_mode = is_course_overview_question(question)
        top_score = docs[0][1]
        if not overview_mode and top_score > settings.UNMATCHED_DISTANCE:
            top_course = docs[0][0].metadata.get("course") or ""
            if not await is_course_related(question):
                yield {
                    "type": "token",
                    "content": "这个问题不在我的回答范围内，我只解答课程知识库中的内容。",
                }
                return
            recommended = recommend_titles(docs)
            content = "【知识库未收录】知识库里暂时没有" + (
                f"「{question}」" if len(question) <= 40 else "该问题"
            ) + "的相关内容。\n" + "\n".join(feedback_lines(question, recommended))
            yield {"type": "token", "content": content}
            yield {
                "type": "unmatched",
                "question": question,
                "course": top_course,
                "recommended": recommended,
            }
            return

        # 距离门控拦不住、但资料其实没讲这个知识点的情况（资料讲二叉树、学生问红黑树），
        # 再独立判一次，免得生成模型照着相近资料编出一套不存在的性质。
        if settings.ANSWERABILITY_CHECK and not await can_answer(question, docs):
            recommended = recommend_titles(docs)
            content = "【知识库未收录】知识库里暂时没有" + (
                f"「{question}」" if len(question) <= 40 else "该问题"
            ) + "的相关内容。\n" + "\n".join(feedback_lines(question, recommended))
            yield {"type": "token", "content": content}
            yield {
                "type": "unmatched",
                "question": question,
                "course": docs[0][0].metadata.get("course") or "",
                "recommended": recommended,
            }
            return

        context = _format_context(docs)

        # 2. 组装消息（系统 + 历史 + 当前问题）；概览问题用专用提示引导
        messages: list = [("system", OVERVIEW_PROMPT if overview_mode else SYSTEM_PROMPT)]
        for role, content in history[-settings.HISTORY_LIMIT * 2 :]:
            if role in ("user", "assistant"):
                messages.append((role, content))
        messages.append(("human", "{question}"))

        prompt = ChatPromptTemplate.from_messages(messages)
        chain = prompt | get_llm()

        # 3. 流式生成（带超时兜底：超过 LLM_TIMEOUT 秒停止，避免模型卡死一直挂着）
        deadline = time.monotonic() + settings.LLM_TIMEOUT
        answer_parts: list[str] = []
        async for chunk in chain.astream({"context": context, "question": question}):
            if time.monotonic() > deadline:
                logger.warning("RAG 回答生成超过 %.0f 秒，已强制停止", settings.LLM_TIMEOUT)
                yield {
                    "type": "error",
                    "message": f"回答生成超时（超过 {settings.LLM_TIMEOUT:.0f} 秒），稍后再试或者换个问法。",
                }
                return
            content = getattr(chunk, "content", "")
            if isinstance(content, str) and content:
                answer_parts.append(content)
                yield {"type": "token", "content": content}

        answer = "".join(answer_parts).strip()
        if not answer:
            answer = "这次没生成出有效回答。检查一下模型服务，或者换个问法再问。"
            yield {"type": "token", "content": answer}

        # 模型判定知识库未收录（未收录特征短语）：系统自动追加
        # 相近资料推荐与反馈提示，并发出 unmatched 事件供落库记录
        # （概览模式基于所选库资料回答，不判未收录）
        if not overview_mode and is_unmatched_answer(answer):
            recommended = recommend_titles(docs)
            suffix = "\n\n" + "\n".join(feedback_lines(question, recommended))
            answer += suffix
            yield {"type": "token", "content": suffix}
            yield {
                "type": "unmatched",
                "question": question,
                "course": docs[0][0].metadata.get("course") or "",
                "recommended": recommended,
            }

        yield {"type": "sources", "sources": _format_sources(docs), "answer": answer}
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG 流式回答失败")
        err = str(exc)
        # 模型缺失（Ollama 404）：给出可操作的提示，而不是裸报错
        if "404" in err or "model not found" in err.lower():
            yield {
                "type": "error",
                "message": (
                    f"模型 {settings.OLLAMA_LLM_MODEL} 未安装。"
                    f"请先运行：ollama pull {settings.OLLAMA_LLM_MODEL}，然后重启服务再试。"
                ),
            }
            return
        yield {"type": "error", "message": f"回答生成失败：{exc}"}
