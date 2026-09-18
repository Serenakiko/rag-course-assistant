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

# 模型判定"知识库未收录"时的特征短语（模型输出格式可能不完全受控，用模糊匹配兜底）
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

SYSTEM_PROMPT = """你是一位专业、耐心的「智答系统」课程智能助教，只解答与课程知识库相关的问题（课程知识点、作业习题、学习资料等）。

回答规则：
1. 先判断问题是否在你的回答范围内：只回答与课程知识库相关的问题。对于明显无关的问题（如考公/择业建议、天气、时事新闻、个人生活决策、闲聊等），直接回复："这个问题不在我的回答范围内，我只解答课程知识库中的内容。" 不要展开讨论、不要给一般性建议、不要猜测，一句话拒绝即可。
2. 范围之内的问题，先判断下方「参考资料」是否与问题相关、能否作答：
   - 参考资料与问题相关且能回答：直接基于资料给出答案，能作答时不要在回答结尾追加"资料中没有找到相关内容"之类的说明。
   - 参考资料只覆盖了问题的一部分：回答能确定的部分，并说明"知识库中关于该问题的资料不完整"。
   - 参考资料与问题不相关，或没有覆盖问题所问的具体知识点（例如资料讲二叉树但问题问红黑树）：回答必须以"【知识库未收录】"开头，内容为"知识库中暂无相关内容"，再简要说明知识库现有资料与问题的差距；**绝不使用模型自身知识编造或补充答案，不要自行列举资料标题、不要承诺已反馈教师**（相近资料推荐和反馈提示由系统自动追加）。
3. 回答结构清晰：先直接给出结论/答案，再展开要点；涉及步骤时用编号列表。
4. 语言使用简体中文，专业术语可保留英文原名并给出中文解释。
5. 直接基于参考资料作答；不要讨论"参考资料编号、数量、来源、异同"本身（例如不要输出"资料[1]和[2]一致""资料[4]未提供详细内容"之类的话）。
6. 如果资料为空（知识库还没有内容），告知学生"知识库中暂无相关内容"，并引导其添加课程资料或提出课程相关的问题，不要按常识编造答案。

参考资料：
{context}"""

# 课程概览专用提示：用户问"这门课/知识库有什么、可以干什么"时使用。
# 资料里有什么就介绍什么，禁止输出"未收录/暂无相关内容"等拒答文案。
OVERVIEW_PROMPT = """你是一位专业的「智答系统」课程智能助教。用户想了解知识库/课程里收录了什么内容、可以做什么。

回答要求：
1. 根据下方「参考资料」中列出的知识条目，介绍知识库收录的内容：涉及哪些课程（如数据结构、操作系统等），各有哪些知识点主题，能帮助解答哪类问题（概念解释、算法/原理讲解、对比辨析、习题答疑等）。
2. 用简洁的列表/分条形式输出，先总述再分课程/分主题列举。
3. 资料里有什么就介绍什么；**绝对不要回答"未收录""暂无相关内容""知识库中没有"之类的话**，也不要自行编造资料之外的知识点。
4. 使用简体中文。

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
            f"知识库中现有的相近资料：{titles}，可尝试围绕这些知识点换个问法提问。"
        )
    else:
        lines.append("建议换个知识库中已有的知识点提问，或请教师补充相关课程资料。")
    lines.append("（该问题已自动反馈给教师，补充资料后会重新生成回答。）")
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


async def generate_answer(question: str, courses: list[str] | None = None) -> dict:
    """非流式生成一次完整回答（供管理员补充资料后"重新生成回答"使用）。

    返回：{"answer", "recommended", "matched"}
      - matched=False 表示仍未命中知识库（空库 / 距离门控），answer 为拒答文案
    """
    docs = vector_search(question, settings.TOP_K, courses)
    if not docs:
        return {
            "answer": "知识库中暂无相关内容，请先补充课程资料后重试。",
            "recommended": [],
            "matched": False,
        }
    top_score = docs[0][1]
    if top_score > settings.UNMATCHED_DISTANCE:
        recommended = recommend_titles(docs)
        content = "【知识库未收录】知识库中暂无" + (
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
            answer_parts.append("（回答生成超时，请稍后重试）")
            break
        content = getattr(chunk, "content", "")
        if isinstance(content, str) and content:
            answer_parts.append(content)
    answer = "".join(answer_parts).strip() or "抱歉，没有生成有效回答，请检查模型服务或稍后重试。"

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
                "content": "知识库中暂无相关内容，请先添加课程资料，或提出与课程相关的问题。",
            }
            return
        # 2. 无匹配门控：最相似资料的距离仍超过阈值 → 知识库未覆盖该问题，
        #    先判定是否与课程学习相关：无关问题直接拒绝且不记录；
        #    学习相关但库无才进入"反馈"记录（避免天气/闲聊等污染反馈列表）。
        #    课程概览类问题（"这门课讲了什么""知识库有什么"）不走门控，
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
            content = "【知识库未收录】知识库中暂无" + (
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
                    "message": f"回答生成超时（超过 {settings.LLM_TIMEOUT:.0f} 秒），请稍后重试或换个问法。",
                }
                return
            content = getattr(chunk, "content", "")
            if isinstance(content, str) and content:
                answer_parts.append(content)
                yield {"type": "token", "content": content}

        answer = "".join(answer_parts).strip()
        if not answer:
            answer = "抱歉，我没有生成有效回答。请检查模型服务是否正常，或换个问法再试。"
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
