"""向量库封装：基于 ChromaDB（持久化）+ LangChain Chroma。

两类数据写入同一 collection：
  - 课程文档分块   source_type=document，doc_id=文档ID
  - 手工知识点     source_type=knowledge_point，point_id=知识点ID
"""
from __future__ import annotations

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document

from ..config import settings
from .embedding_service import get_embeddings

_client: chromadb.ClientAPI | None = None
_vector_store: Chroma | None = None


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(settings.CHROMA_DIR))
    return _client


def get_vector_store() -> Chroma:
    """懒加载向量库（首次调用时创建 collection）。"""
    global _vector_store
    if _vector_store is None:
        _vector_store = Chroma(
            client=_get_client(),
            collection_name=settings.COLLECTION_NAME,
            embedding_function=get_embeddings(),
        )
    return _vector_store


def _doc(text: str, metadata: dict) -> Document:
    return Document(page_content=text, metadata=metadata)


# ---------- 文档 ----------
def add_document_chunks(doc_id: int, filename: str, course: str, chunks: list[str]) -> int:
    """文档分块入库，返回写入块数。"""
    if not chunks:
        return 0
    docs = [
        _doc(
            text,
            {
                "source_type": "document",
                "doc_id": str(doc_id),
                "filename": filename,
                "course": course,
                "chunk_index": i,
            },
        )
        for i, text in enumerate(chunks)
    ]
    ids = [f"doc-{doc_id}-{i}" for i in range(len(chunks))]
    get_vector_store().add_documents(docs, ids=ids)
    return len(chunks)


def delete_document(doc_id: int) -> None:
    """删除某文档的全部向量（不存在时静默跳过）。"""
    try:
        get_vector_store().delete(
            where={
                "$and": [{"source_type": "document"}, {"doc_id": str(doc_id)}]
            }
        )
    except Exception:  # noqa: BLE001  集合为空/键不存在时忽略
        pass


# ---------- 知识点 ----------
def add_knowledge_point(point_id: int, title: str, content: str, course: str, tags: str) -> None:
    doc = _doc(
        f"{title}\n{content}",
        {
            "source_type": "knowledge_point",
            "point_id": str(point_id),
            "title": title,
            "course": course,
            "tags": tags,
        },
    )
    get_vector_store().add_documents([doc], ids=[f"point-{point_id}"])


def update_knowledge_point(point_id: int, title: str, content: str, course: str, tags: str) -> None:
    delete_knowledge_point(point_id)
    add_knowledge_point(point_id, title, content, course, tags)


def delete_knowledge_point(point_id: int) -> None:
    try:
        get_vector_store().delete(
            where={
                "$and": [{"source_type": "knowledge_point"}, {"point_id": str(point_id)}]
            }
        )
    except Exception:  # noqa: BLE001
        pass


# ---------- 检索 ----------
def search(
    query: str,
    top_k: int | None = None,
    courses: list[str] | None = None,
) -> list[tuple[Document, float]]:
    """相似度检索，返回 (文档, 距离分数) 列表，按相关度升序。

    courses 非空时只检索指定课程（知识库）的资料，空/None 表示全库检索。
    """
    k = top_k or settings.TOP_K
    # courses=None 表示不限制；courses=[] 表示"没有可检索的范围"，必须返回空。
    # 否则空列表会被当成"不过滤"，反而把全部知识库都捞出来。
    if courses is not None and not courses:
        return []
    filter_ = None
    if courses:
        filter_ = {"course": {"$in": courses}}
    results = get_vector_store().similarity_search_with_score(
        query, k=max(k * 3, 12), filter=filter_
    )

    # 去重：同一文档的同一分块、以及内容完全相同的资料，只保留最相关的一条
    seen_keys: set[tuple] = set()
    seen_content: set[str] = set()
    deduped: list[tuple[Document, float]] = []
    for doc, score in results:
        src = doc.metadata
        key = (
            src.get("course"),
            src.get("doc_id") or src.get("point_id") or src.get("filename") or src.get("title"),
            src.get("chunk_index"),
        )
        content = doc.page_content.strip()
        if key in seen_keys or content in seen_content:
            continue
        seen_keys.add(key)
        seen_content.add(content)
        deduped.append((doc, score))
    return deduped[:k]
