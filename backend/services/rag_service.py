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

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from ..config import settings
from .llm_service import get_llm
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
   - 资料只有一两句话时，别把原句换个说法抄一遍就交差：先说清它在回答什么，再把资料里的每个要点分别说一句
     （它是什么、用来解决什么），出现的术语要展开成普通话说清楚。仍然不许补充资料之外的知识点；
     资料真的只有一句话能讲，就答一句话，不要注水凑字数。
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
   总量控制在 400 字以内：每门课一行，一行里只点 2~3 个代表性主题词，不要逐条抄知识点标题。
   课程多的时候挑 6~8 门最典型的说，剩下的概括成一句“还有……”带过。
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


def recommend_titles(docs: list[tuple[Document, float]], limit: int = 3, spread: float = 0.15) -> list[str]:
    """从检索结果中提取相近资料标题，用于拒答时引导换问法。

    两道过滤：只取与首选结果距离接近的（spread），并且首选结果本身不能离题太远
    （超过阈值 + 0.15 就说明整批都是噪声，"api 是什么意思"以前会被推《条件概率》）。
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
    "范式", "主键", "外键", "继承",
    "多态", "指针", "数组", "字符串", "软件工程", "需求分析",
    "软件测试", "版本控制", "Git", "前端", "后端", "编译",
    "知识点", "课件", "章节", "考点", "习题", "实验报告", "作业", "复习", "考试",
    "课程", "助教", "知识库", "课本", "笔记",
    "API", "接口", "协议", "带宽", "并发", "缓存", "编码", "加密", "容器", "虚拟机",
)
# 明显无关词：命中即视为与课程学习无关（不进入反馈）
IRRELEVANT_KEYWORDS = (
    "天气", "温度", "下雨", "考公", "公务员", "考研", "择业", "求职", "薪资",
    "简历", "面试", "新闻", "时政", "明星", "八卦", "娱乐", "电影", "电视剧",
    "综艺", "音乐", "美食", "菜谱", "旅游", "攻略", "景点", "恋爱", "情感",
    "星座", "运势", "游戏攻略", "抽卡", "健身", "减肥", "养生", "就医", "购物",
    "折扣", "优惠", "外卖", "打车",
)


# 寒暄/客套/自我介绍类整句：这类话去检索必然撞距离门控，再走"是否课程相关"
# 的模型判定又白等几秒，最后还回一句冷冰冰的拒答（学生说"你好"被甩脸）。
# 直接在本地认出来，回一句引导语，不检索、不调模型、不记未命中。
_SMALLTALK_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|哈啰|hi|hello|hey|在吗|在不在|有人吗|早上好|中午好|下午好|晚上好|晚安"
    r"|谢谢|谢谢你|多谢|感谢|3q|thanks|thankyou|辛苦了|好的|好的呀|嗯嗯|ok|收到"
    r"|你是谁|你叫什么|你叫什么名字|介绍一下你自己|你能帮我什么|测试|test)"
    r"(呀|啊|呢|哈|鸭|啦|哦|喔|嘛)*"
    r"[\s!！。？?~～,.，、]*$",
    re.I,
)

SMALLTALK_REPLY = (
    "你好，我是这门课的智能助教，只依据知识库里的课程资料回答。\n\n"
    "能帮上忙的大致三类：\n"
    "1. 讲知识点：概念、原理、推导过程；\n"
    "2. 对比辨析：把容易混的两个概念放一起说区别；\n"
    "3. 习题答疑：课后题、例题一步步拆开讲。\n\n"
    "直接问具体的就行，比如「TCP 三次握手为什么不是两次」「进程和线程有什么区别」。"
)


def is_smalltalk(question: str) -> bool:
    """是不是寒暄/客套/自我介绍类整句（"你好""谢谢""你是谁"）。"""
    return bool(_SMALLTALK_RE.match((question or "").strip()))


async def is_course_related(question: str, timeout: float = 10.0) -> bool:
    """轻量判定：该问题是否与课程学习相关。

    规则优先：先判明显无关（天气/考公/娱乐等），命中直接不相关；
    再看课程术语（红黑树/进程/TCP 等）判相关；
    其余交给 LLM 二分类。LLM 失败时保守返回 False。

    顺序不能反："考研面试里的多线程" 同时命中课程词和无关词，
    先判课程词就会把它当课程问题，反而刷进反馈列表。
    """
    q = (question or "").lower()
    blocked = any(k.lower() in q for k in IRRELEVANT_KEYWORDS)
    strong = any(k.lower() in q for k in COURSE_STRONG_KEYWORDS)
    # 只命中无关词（天气/娱乐）→ 直接不相关；只命中课程词（红黑树/进程）→ 直接相关。
    # 两组都命中（"考研面试里的多线程"、"考研 408 里的红黑树"）不能一刀切：
    # 一刀切判无关，学生问得越贴近考试越会被拒答；判相关又会让闲话刷进反馈列表。
    # 这种含糊的句子交给下面那个只答「是/否」的模型判定。
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




def is_course_overview_question(question: str) -> bool:
    """是否"这门课讲了什么 / 知识库有什么 / 你能帮我做什么"类概览问题。

    概览问题跟资料没有字面相似度，走检索只会撞距离门控，再顺手推荐几条
    不相干的标题，所以单独识别、直接汇总知识库，不参与未收录判定。
    """
    q = re.sub(r"\s+", "", question or "")
    markers = (
        "这门课讲了什么", "这门课学什么", "这门课讲什么", "这门课主要讲什么",
        "这门课都学什么", "这门课有哪些内容", "这门课包括哪些", "这门课有什么内容",
        "这门课学哪些", "这门课的知识点", "课程讲了什么", "课程学什么",
        "课程主要内容", "课程有哪些内容", "课程包括哪些", "知识库讲了什么",
        "知识库里有什么", "知识库有哪些内容", "知识库有什么内容", "知识库包括哪些",
        "知识库有什么知识点", "知识库收录了什么", "知识库里有什么知识点",
        "知识库里收集了什么", "知识库收集了什么", "知识库里都收集了什么",
        "这个知识库讲了什么", "这个知识库有什么", "这个知识库有哪些内容",
        "知识库学什么", "检索知识库", "搜索知识库", "查询知识库",
        "知识库可以干什么", "知识库能干什么", "知识库能做什么", "知识库可以做什么",
        "知识库里有什么可以", "知识库有什么可以", "告诉我可以干什么", "可以干什么事情",
        "知识库里有什么内容", "知识库的内容", "知识库收录了", "知识库有哪些",
        "你能做什么", "你能干什么", "你可以做什么", "能帮我做什么",
        "怎么提问", "如何提问", "能问什么", "可以问什么",
    )
    if any(m in q for m in markers):
        return True
    # 短问句 + 概览/能力口气才走概览；具体问题（更长、带"关于"）仍然正常检索
    if len(q) > 24 or "关于" in q:
        return False
    patterns = (
        r"(知识库|课程|资料).{0,8}(收集|收录|包含|包括|有哪些|有什么|内容|知识点)",
        r"(你|助教|系统|这里).{0,8}(能|可以|会).{0,6}(做什么|干什么|解答|回答|帮我|提供)",
        r"(怎么|如何).{0,2}(提问|问你|用你|使用你)",
        r"(能|可以)问(什么|哪些)",
    )
    return any(re.search(pat, q) for pat in patterns)


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

    返回：{"answer", "recommended", "matched", "error"}
      - matched=False 表示仍未命中知识库（空库 / 距离门控），answer 为拒答文案
      - error="timeout"/"empty" 表示生成失败，answer 为空，调用方不能当已解决写入
    """
    # 向量化 + Chroma 查询是同步 IO，放到线程里跑，避免阻塞事件循环
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
        # 超时提示不是答案，不能混进 answer 里被当成正常回答
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


# 同时最多 _SLOT_COUNT 路生成：本机模型一次只吐一路 token，不限并发时排队的请求
# 等不到第一个 token，180 秒后整批超时（前端只会看到「回答生成超时」）。
_SLOT_COUNT = 3
# 排队期间每隔这么久发一个 ping：前端按「收到数据」重置空闲计时，
# 否则排在后面的请求会被前端的空闲超时（见 chat.js 的 idleTimeout）先掐掉。
_QUEUE_PING_SECONDS = 10.0
# 排队上限：等这么久还没拿到槽位就明确回「人多，稍后再试」。没有上限的话，
# 排队时间不计入 LLM_TIMEOUT，学生会在前端转到总超时，最后看到一句指错方向的
# 「回答生成超时」——他会以为是模型坏了，其实是没轮到他。
_QUEUE_MAX_WAIT = 120.0

# 闸门按事件循环缓存。不能用模块级的单个 Semaphore：asyncio 的信号量第一次
# 真的**等过**之后就跟那个 loop 绑死（_LoopBoundMixin），换个 loop 再用会抛
# 「bound to a different event loop」。uvicorn 只有一个循环所以没事，但 pytest
# 每个 asyncio.run() 都是新循环，第二个"真的排队"的用例就会莫名其妙炸。
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
    """非流式生成（管理员「生成回答」用）：和流式那条路共用同一个并发闸门。

    以前这里不排队，补完资料点「生成回答」会绕过闸门直接打本机模型，后台在跑
    几路问答时这一下就是第 4、5 路。现在排队，但**最多等 _QUEUE_MAX_WAIT 秒**：
    前端的 fetch 没有超时，无限期挂着等于按钮一直转、管理员不知道发生了什么。
    """
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
        # 客户端断开时也要把里层生成器关掉，否则模型流会吊在那儿。
        # 槽位放在嵌套的 finally 里释放：aclose() 自己抛异常时也不能把槽位漏掉。
        try:
            await agen.aclose()
        finally:
            sem.release()


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
      {"type": "error", "message": str}        失败

    courses 非空时，只基于这些知识库（课程）检索回答。
    """
    history = history or []
    try:
        # -1. 寒暄/客套/自我介绍：本地识别，直接给引导语，不检索、不调模型、不记未命中。
        #     以前「你好」要过检索→距离门控→模型判相关性，十几秒后换来一句
        #     “这个问题不在我的回答范围内”，学生体验上就是被冷拒。
        if is_smalltalk(question):
            yield {"type": "token", "content": SMALLTALK_REPLY}
            yield {"type": "sources", "sources": [], "answer": SMALLTALK_REPLY}
            return

        # 0. 概览 / 能力类问题：问句跟资料没有字面相似度，走向量检索只会撞门控。
        #    这里直接汇总知识库与知识点标题，不判未收录、不给推荐。
        if is_course_overview_question(question):
            context = await asyncio.to_thread(overview_context, courses)
            answer_parts: list[str] = []
            if context:
                try:
                    async for chunk in _generate_stream(
                        OVERVIEW_PROMPT, context, question, history
                    ):
                        answer_parts.append(chunk)
                        yield {"type": "token", "content": chunk}
                except asyncio.TimeoutError:
                    logger.warning("概览回答生成超时：%s", question[:30])
                    yield {
                        "type": "error",
                        "message": f"回答生成超时（超过 {settings.LLM_TIMEOUT:.0f} 秒），稍后再试或者换个问法。",
                    }
                    return
            answer = "".join(answer_parts).strip()
            if not answer:
                answer = "知识库里还没有可以介绍的内容，先让老师补充课程资料。"
                yield {"type": "token", "content": answer}
            yield {"type": "sources", "sources": [], "answer": answer}
            return

        # 1. 检索（可按知识库/课程限定范围）
        docs = await asyncio.to_thread(vector_search, question, settings.TOP_K, courses)
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
        if docs[0][1] > settings.UNMATCHED_DISTANCE:
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

        # 2.5 引用资料收紧：top5 里离题的那些不参与生成，也不展示为来源
        kept = _trim_docs(docs)
        context = _format_context(kept)

        # 3. 流式生成：每个 chunk 都用 wait_for 兜底，模型卡死（一个 token 都不来）也能停
        answer_parts = []
        try:
            async for chunk in _generate_stream(
                SYSTEM_PROMPT, context, question, history
            ):
                answer_parts.append(chunk)
                yield {"type": "token", "content": chunk}
        except asyncio.TimeoutError:
            logger.warning("RAG 回答生成超过 %.0f 秒，已强制停止", settings.LLM_TIMEOUT)
            yield {
                "type": "error",
                "message": f"回答生成超时（超过 {settings.LLM_TIMEOUT:.0f} 秒），稍后再试或者换个问法。",
            }
            return

        answer = "".join(answer_parts).strip()
        if not answer:
            answer = "这次没生成出有效回答。检查一下模型服务，或者换个问法再问。"
            yield {"type": "token", "content": answer}

        # 模型判定知识库未收录（未收录特征短语）：系统自动追加
        # 相近资料推荐与反馈提示，并发出 unmatched 事件供落库记录
        if is_unmatched_answer(answer):
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

        yield {"type": "sources", "sources": _format_sources(kept), "answer": answer}
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG 流式回答失败")
        err = str(exc)
        # 模型缺失（Ollama 404）：界面上只给一句人话，细节写日志
        if "404" in err or "model not found" in err.lower():
            logger.error("模型未安装：%s", settings.OLLAMA_LLM_MODEL)
            yield {
                "type": "error",
                "message": "模型服务还没准备好，这次答不了。过一会儿再试，一直这样的话找老师看一下。",
            }
            return
        yield {"type": "error", "message": f"回答生成失败：{exc}"}
