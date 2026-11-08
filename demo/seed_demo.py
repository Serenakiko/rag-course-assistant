"""生成答辩演示数据：学生账号、会话记录、消息反馈和未命中问题。

只写 SQLite，不调用大模型；知识点向量由 backend.seed 在应用启动时负责。
可重复运行：已存在的账号 / 会话 / 未命中问题会跳过。

用法：
    python demo/seed_demo.py
    python demo/seed_demo.py --no-seed   # 跳过四门课种子数据，只造演示数据
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402
from backend.models import Conversation, Message, UnansweredQuestion, User  # noqa: E402
from backend.security import hash_password  # noqa: E402
from backend.seed import init_seed  # noqa: E402

DEMO_PASSWORD = "111111"

DEMO_USERS = [
    ("23040108", "张同学"),
    ("23040109", "李同学"),
    ("23040110", "王同学"),
    ("23040111", "赵同学"),
]

# sources 的字段与 rag_service._format_sources 保持一致，前端引用卡可直接渲染
DEMO_CONVERSATIONS = [
    {
        "account": "23040108",
        "title": "TCP 三次握手怎么理解",
        "messages": [
            {"role": "user", "content": "TCP 为什么需要三次握手？"},
            {
                "role": "assistant",
                "content": (
                    "三次握手用于确认双方的收发能力都正常：客户端发送 SYN，服务端回复 "
                    "SYN+ACK，客户端再发送 ACK。第三次握手还能防止已失效的连接请求报文"
                    "突然到达服务端，避免建立多余连接。"
                ),
                "sources": [
                    {
                        "source_type": "knowledge_point",
                        "point_id": None,
                        "title": "TCP 三次握手与四次挥手",
                        "course": "计算机网络",
                        "content": "TCP 建立连接采用三次握手……第三次握手防止旧报文干扰。",
                    }
                ],
                "feedback": "up",
            },
        ],
    },
    {
        "account": "23040108",
        "title": "红黑树插入为什么难",
        "messages": [
            {"role": "user", "content": "红黑树的插入过程是怎样的？"},
            {
                "role": "assistant",
                "content": (
                    "根据现有课程资料，我暂时无法回答这个问题。当前资料只介绍了二叉树、"
                    "二叉排序树和平衡树的概念，没有展开红黑树插入时的旋转与变色步骤。"
                ),
                "sources": [],
                "rejected": 1,
            },
        ],
    },
    {
        "account": "23040109",
        "title": "死锁的四个必要条件",
        "messages": [
            {"role": "user", "content": "产生死锁的四个必要条件是什么？"},
            {
                "role": "assistant",
                "content": (
                    "死锁的四个必要条件是：互斥、占有并等待、不可剥夺、循环等待。"
                    "破坏其中任意一个条件就可以预防死锁；银行家算法则是在分配前判断"
                    "系统是否处于安全状态，属于死锁避免。"
                ),
                "sources": [
                    {
                        "source_type": "knowledge_point",
                        "point_id": None,
                        "title": "死锁及其处理",
                        "course": "操作系统",
                        "content": "死锁指多个进程因竞争资源而互相等待……四个必要条件。",
                    }
                ],
                "feedback": "up",
            },
        ],
    },
    {
        "account": "23040109",
        "title": "HTTP 和 HTTPS 的区别",
        "messages": [
            {"role": "user", "content": "HTTP 和 HTTPS 有什么区别？"},
            {
                "role": "assistant",
                "content": (
                    "HTTPS 在 HTTP 与 TCP 之间加入 TLS/SSL 层，通过证书验证服务器身份、"
                    "协商会话密钥，并用对称加密保护数据，因此提供机密性、完整性和身份认证。"
                    "HTTP 默认 80 端口，HTTPS 默认 443 端口。"
                ),
                "sources": [
                    {
                        "source_type": "knowledge_point",
                        "point_id": None,
                        "title": "HTTP 与 HTTPS",
                        "course": "计算机网络",
                        "content": "HTTPS 在 HTTP 与 TCP 之间加入 TLS/SSL 层……",
                    }
                ],
            },
        ],
    },
    {
        "account": "23040110",
        "title": "二叉树遍历顺序",
        "messages": [
            {"role": "user", "content": "二叉树的先序、中序、后序遍历分别是什么？"},
            {
                "role": "assistant",
                "content": (
                    "先序遍历是根-左-右，中序遍历是左-根-右，后序遍历是左-右-根。"
                    "二叉排序树的中序遍历可以得到有序序列，层序遍历则借助队列实现。"
                ),
                "sources": [
                    {
                        "source_type": "knowledge_point",
                        "point_id": None,
                        "title": "二叉树及其遍历",
                        "course": "数据结构",
                        "content": "二叉树遍历方式分为深度优先和广度优先……",
                    }
                ],
                "feedback": "up",
            },
        ],
    },
    {
        "account": "23040110",
        "title": "TCP 和 UDP 怎么选",
        "messages": [
            {"role": "user", "content": "TCP 和 UDP 各自适合什么场景？"},
            {
                "role": "assistant",
                "content": (
                    "TCP 面向连接、可靠、面向字节流，适合文件传输和网页浏览；"
                    "UDP 无连接、不可靠、面向报文，首部开销小、时延低，适合实时音视频、"
                    "DNS 查询和游戏等容忍少量丢包的场景。"
                ),
                "sources": [
                    {
                        "source_type": "knowledge_point",
                        "point_id": None,
                        "title": "TCP 与 UDP 对比",
                        "course": "计算机网络",
                        "content": "TCP 面向连接、可靠、面向字节流……",
                    }
                ],
            },
        ],
    },
    {
        "account": "23040111",
        "title": "进程和线程的区别",
        "messages": [
            {"role": "user", "content": "进程和线程有什么区别？"},
            {
                "role": "assistant",
                "content": (
                    "进程是资源分配的基本单位，包含程序段、数据段和 PCB；线程是 CPU "
                    "调度的基本单位，同一进程的多个线程共享地址空间，切换开销更小。"
                    "进程的基本状态有就绪、运行和阻塞。"
                ),
                "sources": [
                    {
                        "source_type": "knowledge_point",
                        "point_id": None,
                        "title": "进程与线程",
                        "course": "操作系统",
                        "content": "进程是资源分配的基本单位……线程是 CPU 调度的基本单位。",
                    }
                ],
                "feedback": "down",
            },
        ],
    },
    {
        "account": "23040111",
        "title": "Cache 的映射方式",
        "messages": [
            {"role": "user", "content": "Cache 有哪几种映射方式？"},
            {
                "role": "assistant",
                "content": (
                    "Cache 与主存的映射方式有直接映射、全相联映射和组相联映射。"
                    "替换策略常见的有 LRU、FIFO，写策略分为写直达和写回。"
                ),
                "sources": [
                    {
                        "source_type": "knowledge_point",
                        "point_id": None,
                        "title": "存储层次结构与 Cache",
                        "course": "计算机组成原理",
                        "content": "Cache 与主存的映射方式：直接映射、全相联映射、组相联映射……",
                    }
                ],
                "feedback": "up",
            },
        ],
    },
]

DEMO_UNANSWERED = [
    {
        "account": "23040108",
        "question": "红黑树的插入过程是怎样的？",
        "course": "数据结构",
        "status": "pending",
        "related_points": ["二叉树及其遍历"],
    },
    {
        "account": "23040109",
        "question": "MySQL 的索引底层为什么用 B+ 树？",
        "course": "数据结构",
        "status": "pending",
        "related_points": ["图与最短路径"],
    },
    {
        "account": "23040110",
        "question": "TCP 拥塞控制的慢启动具体怎么计算 cwnd？",
        "course": "计算机网络",
        "status": "resolved",
        "related_points": ["TCP 与 UDP 对比"],
        "answer": (
            "慢启动阶段 cwnd 从 1 个 MSS 开始，每收到一个 ACK 就增加 1 个 MSS，"
            "因此每经过一个 RTT，cwnd 大约翻倍，直到达到慢启动阈值 ssthresh 后"
            "进入拥塞避免阶段。"
        ),
    },
    {
        "account": "23040111",
        "question": "操作系统如何进行用户登录认证？",
        "course": "操作系统",
        "status": "pending",
        "related_points": ["文件系统与磁盘管理"],
    },
]


def _get_or_create_user(db, account: str, username: str) -> User:
    user = db.scalar(select(User).where(User.account == account))
    if user is not None:
        return user
    user = User(
        account=account,
        password_hash=hash_password(DEMO_PASSWORD),
        username=username,
        role="student",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def seed_conversations(db) -> tuple[int, int]:
    created_convs = created_msgs = 0
    for spec in DEMO_CONVERSATIONS:
        user = db.scalar(select(User).where(User.account == spec["account"]))
        if user is None:
            continue
        exists = db.scalar(
            select(Conversation.id).where(
                Conversation.user_id == user.id,
                Conversation.title == spec["title"],
            )
        )
        if exists is not None:
            continue
        conv = Conversation(user_id=user.id, title=spec["title"])
        db.add(conv)
        db.flush()
        for m in spec["messages"]:
            db.add(
                Message(
                    conversation_id=conv.id,
                    role=m["role"],
                    content=m["content"],
                    sources=json.dumps(m.get("sources") or [], ensure_ascii=False),
                    rejected=int(m.get("rejected") or 0),
                    feedback=m.get("feedback") or "",
                )
            )
            created_msgs += 1
        created_convs += 1
    db.commit()
    return created_convs, created_msgs


def seed_unanswered(db) -> int:
    created = 0
    display_no = db.scalar(select(func.max(UnansweredQuestion.display_no))) or 0
    for spec in DEMO_UNANSWERED:
        user = db.scalar(select(User).where(User.account == spec["account"]))
        if user is None:
            continue
        exists = db.scalar(
            select(UnansweredQuestion.id).where(
                UnansweredQuestion.question == spec["question"],
                UnansweredQuestion.created_by == user.id,
            )
        )
        if exists is not None:
            continue
        display_no += 1
        resolved = spec["status"] == "resolved"
        db.add(
            UnansweredQuestion(
                display_no=display_no,
                question=spec["question"],
                course=spec["course"],
                status=spec["status"],
                related_points=json.dumps(spec.get("related_points") or [], ensure_ascii=False),
                answer=spec.get("answer") or "",
                answered_at=datetime.now(timezone.utc) if resolved else None,
                created_by=user.id,
            )
        )
        created += 1
    db.commit()
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="生成答辩演示数据")
    parser.add_argument("--no-seed", action="store_true", help="跳过四门课种子数据")
    args = parser.parse_args()

    init_db()
    if not args.no_seed:
        init_seed()

    db = SessionLocal()
    try:
        users = [_get_or_create_user(db, account, name) for account, name in DEMO_USERS]
        # session 关闭后 ORM 对象会过期，先把要打印的字段取成普通值
        user_lines = [(u.account, u.username) for u in users]
        convs, msgs = seed_conversations(db)
        unanswered = seed_unanswered(db)
    finally:
        db.close()

    print("演示数据已就绪：")
    print(f"  学生账号：{len(user_lines)} 个（密码统一 {DEMO_PASSWORD}）")
    print(f"  新增会话：{convs} 个，消息 {msgs} 条")
    print(f"  新增未命中问题：{unanswered} 条")
    print("\n账号：" + "、".join(f"{account}({name})" for account, name in user_lines))
    print("启动应用后即可在管理端看到用户、会话、反馈和问题列表。")


if __name__ == "__main__":
    main()
