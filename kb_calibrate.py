import sys
sys.path.insert(0, r"D:\rag-course-assistant")
from backend.services.vector_store import get_vector_store

queries = {
    "红黑树（语义近但库无）": "解释一下红黑树的插入过程",
    "TCP（强匹配）": "什么是TCP三次握手",
    "天气（完全无关）": "今天天气如何",
    "总结框架（宽泛弱匹配）": "帮我总结一下这门课的核心知识点框架",
    "栈和队列（强匹配）": "栈和队列有什么区别？各自适合什么场景？",
}
store = get_vector_store()
for label, q in queries.items():
    res = store.similarity_search_with_score(q, k=3)
    print(f"### {label} | {q}")
    for d, s in res:
        src = d.metadata
        print(f"  {s:.4f} | {src.get('course')} | {src.get('title') or src.get('filename')}")
