import json
import os
import sys

import httpx

base = "http://127.0.0.1:8000"
TEST_USER = os.getenv("RAG_TEST_USER", "")
TEST_PASSWORD = os.getenv("RAG_TEST_PASSWORD", "")

def sse_collect(resp):
    """收集 SSE data 行文本。"""
    parts = []
    for line in resp.iter_lines():
        if line and line.startswith("data: "):
            parts.append(line[6:])
    return parts

def main():
    # 1. 登录管理员
    if not TEST_USER or not TEST_PASSWORD:
        sys.exit("请先设置 RAG_TEST_USER / RAG_TEST_PASSWORD 环境变量")
    r = httpx.post(
        f"{base}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    print("登录 OK")

    # 2. 创建会话
    r = httpx.post(f"{base}/api/chat/sessions", json={"title": "闭环验证"}, headers=h, timeout=10)
    conv = r.json()["id"]
    print("会话 ID:", conv)

    # 3. 三类问题
    cases = [
        ("今天天气如何", "完全无关（门控）"),
        ("解释一下红黑树的插入过程", "语义近但库未覆盖（模型判定）"),
        ("什么是TCP三次握手", "强匹配（正常回答）"),
    ]
    for msg, label in cases:
        print(f"\n=== {label} | {msg} ===")
        with httpx.stream("POST", f"{base}/api/chat/ask",
                          json={"message": msg, "conversation_id": conv},
                          headers=h, timeout=120) as resp:
            evs = sse_collect(resp)
        tokens = [json.loads(e)["content"] for e in evs if json.loads(e).get("type") == "token"]
        types = {json.loads(e).get("type") for e in evs}
        print("事件类型:", sorted(types))
        print("回答:", "".join(tokens)[:150].replace("\n", " "))

    # 4. 检查未命中问题记录
    r = httpx.get(f"{base}/api/admin/unanswered", headers=h, timeout=10)
    r.raise_for_status()
    items = r.json()["items"]
    print("\n=== 未命中问题记录 ===")
    for it in items:
        print(f"#{it['id']} [{it['status']}] course={it['course']!r} | {it['question']!r} | by={it['creator_name']!r}")

    # 5. 标记已补充 + 删除（第一条）
    if items:
        first = items[0]
        r = httpx.patch(f"{base}/api/admin/unanswered/{first['id']}?status=resolved", headers=h, timeout=10)
        print("PATCH resolved:", r.status_code, r.json())
        r = httpx.get(f"{base}/api/admin/unanswered?status=resolved", headers=h, timeout=10)
        print("resolved 列表条数:", len(r.json()["items"]))

if __name__ == "__main__":
    main()
