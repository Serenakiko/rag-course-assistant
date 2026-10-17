import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.services.vector_store import get_vector_store

tests = [
    "什么是 B+ 树？",          # 学习相关、知识库没有（只有二叉树）
    "什么是死锁？",            # 知识库有（操作系统/死锁）
    "今天天气如何？",          # 无关
    "我要不要考公？",          # 无关
    "什么是哈希表？",          # 知识库有
    "请解释一下 TCP/IP 协议",  # 知识库有
]
for q in tests:
    res = get_vector_store().similarity_search_with_score(q, k=1)
    d, s = res[0]
    src = d.metadata
    print(f"{s:.3f} | {q}  ->  course={src.get('course')} title={src.get('title') or src.get('filename')}")
