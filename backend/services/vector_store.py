"""向量库封装：基于 ChromaDB（持久化）+ LangChain Chroma。

两类数据写入同一 collection：
  - 课程文档分块   source_type=document，doc_id=文档ID
  - 手工知识点     source_type=knowledge_point，point_id=知识点ID

相似度口径统一为**余弦相似度**（collection 用 cosine space），score 越大越相关。
这取代了旧版的平方 L2 距离：距离阈值 0.85 这类「越大越差」的写法全部废弃。
"""
from __future__ import annotations

import logging
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document

from ..config import settings
from .embedding_service import get_embeddings

logger = logging.getLogger(__name__)

_client: chromadb.ClientAPI | None = None
_vector_store: Chroma | None = None

# Chroma 的 cosine space 返回的距离其实是 1 - cosine_similarity（值域 [0, 2]），
# 与归一化向量的平方 L2 距离正好相等。这里显式转回「相似度」：sim = 1 - distance。
_COSINE_DISTANCE = "cosine"


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(settings.CHROMA_DIR))
    return _client


def get_collection() -> chromadb.Collection:
    """返回底层 Chroma collection（供需要直接按 metadata 查询的场景使用）。

    优先用 get_vector_store() 做常规读写；只有像「按 doc_id 取某文档全部分块」
    这种 Chroma 原生 where 查询才直接拿 collection，避免让调用方 import 私有 _get_client。
    """
    return _get_client().get_collection(settings.COLLECTION_NAME)


def check_embedding_dimension(collection: chromadb.Collection) -> None:
    """校验当前 embedding 模型维度是否与既有 collection 一致。

    collection 维度建库即固定，切换 .env 里的 embedding 模型（bge-m3=1024 维、
    text-embedding-3-small=1536 维）后不重建向量库，每次检索都会抛维度错。
    这里在建 store 前主动查一次，不匹配给明确报错和重建指引。
    """
    try:
        dim = collection.count()
    except Exception:  # noqa: BLE001  集合尚未初始化等
        return
    if dim == 0:
        return  # 空库无所谓维度，首次写入自然对齐
    try:
        existing = collection.get(limit=1, include=["embeddings"])
        vecs = existing.get("embeddings")
        if vecs is None or len(vecs) == 0:
            return
        first = vecs[0]
        if first is None or len(first) == 0:
            return
        existing_dim = len(first)
    except Exception:  # noqa: BLE001
        logger.warning("读取向量库维度失败，跳过维度校验", exc_info=True)
        return
    emb = get_embeddings()
    try:
        current_dim = len(emb.embed_query("维度校验"))
    except Exception:  # noqa: BLE001
        logger.warning("无法获取当前 embedding 维度，跳过校验", exc_info=True)
        return
    if current_dim != existing_dim:
        raise RuntimeError(
            f"向量库维度不匹配：当前 embedding 模型输出 {current_dim} 维，"
            f"但已有 collection 是 {existing_dim} 维（collection 维度建库即固定）。\n"
            "已切换 embedding 模型时需重建向量库：\n"
            "  1) 删除 data/chroma 目录（或改用新的 COLLECTION_NAME）；\n"
            "  2) 重启服务，让种子数据 / 文档重新入库。"
        )


def _assert_cosine_space(collection: chromadb.Collection) -> None:
    """校验既有 collection 是 cosine space，否则检索分数语义错乱（返回平方 L2）。

    collection_metadata={"hnsw:space": "cosine"} 只在**新建** collection 时生效；
    拷来一份旧 l2 库（或换 COLLECTION_NAME 撞上残留），会静默沿用 l2，再被 search 里的
    `1.0 - score` 当成 1-cosine，相似度大面积变负 → 全部走拒答。这里主动断言，不等出问题。
    """
    try:
        meta = collection.metadata or {}
    except Exception:  # noqa: BLE001  拿不到 metadata 时不强行校验
        return
    space = meta.get("hnsw:space")
    if space is not None and space != _COSINE_DISTANCE:
        raise RuntimeError(
            f"向量库空间不匹配：已有 collection 是 {space} space，而代码要求 cosine。\n"
            "collection 的 space 建库即固定，无法原地切换。请重建向量库：\n"
            "  1) 删除 data/chroma 目录（或改用新的 COLLECTION_NAME）；\n"
            "  2) 重启服务，让种子数据 / 文档重新入库。"
        )


def get_vector_store() -> Chroma:
    """懒加载向量库（首次调用时创建 collection，并校验维度与空间）。"""
    global _vector_store
    if _vector_store is None:
        # collection 可能尚不存在（首次入库），只容忍「不存在」这一种情况跳过校验；
        # 其余异常（维度不匹配的 RuntimeError、空间不匹配的 RuntimeError）必须放出去。
        try:
            collection = get_collection()
        except Exception:  # noqa: BLE001  collection 不存在 → 首次创建，无需校验
            collection = None
        if collection is not None:
            check_embedding_dimension(collection)
            _assert_cosine_space(collection)
        _vector_store = Chroma(
            client=_get_client(),
            collection_name=settings.COLLECTION_NAME,
            embedding_function=get_embeddings(),
            collection_metadata={"hnsw:space": _COSINE_DISTANCE},
        )
    return _vector_store


def _doc(text: str, metadata: dict) -> Document:
    return Document(page_content=text, metadata=metadata)


# ---------- 文档 ----------
def add_document_chunks(doc_id: int, filename: str, course: str, chunks: list[str]) -> int:
    """文档分块入库，返回写入块数。

    版本化 id：先写新版本（version 自增），成功后再删旧版本。这样 add 中途失败时
    旧向量还在，DB 里的 status 也保持 failed，而不是「向量全丢、DB 却标 ready」。
    """
    if not chunks:
        return 0
    # 文档分块同时写 title（去扩展名）和 filename（完整名）：
    # title 供引用卡展示干净的标题（不带 .pdf 后缀），filename 供删文档时精确回退反馈。
    title = Path(filename).stem or filename
    docs = [
        _doc(
            text,
            {
                "source_type": "document",
                "doc_id": str(doc_id),
                "filename": filename,
                "title": title,
                "course": course,
                "chunk_index": i,
            },
        )
        for i, text in enumerate(chunks)
    ]
    version = _next_version("document", str(doc_id))
    ids = [f"doc-{doc_id}-{i}-v{version}" for i in range(len(chunks))]
    get_vector_store().add_documents(docs, ids=ids)
    # 新版本写入成功，再清旧版本（不包含刚写的 version）
    _delete_document_versions(doc_id, keep_version=version)
    return len(chunks)


def _next_version(source_type: str, oid: str) -> int:
    """读取某资料当前的版本号并 +1（从 Chroma 元数据里推断，首次为 1）。

    版本号编码进向量 id 后缀，用于「先写新、再删旧」的原子替换。
    collection 尚未创建时（首次入库）返回 1。
    """
    try:
        collection = get_collection()
    except Exception:  # noqa: BLE001  collection 不存在 → 首次版本
        return 1
    id_field = "doc_id" if source_type == "document" else "point_id"
    try:
        data = collection.get(
            where={"$and": [{"source_type": source_type}, {id_field: oid}]},
            include=["metadatas"],
        )
        max_v = 0
        for raw in data.get("ids", []) or []:
            for part in str(raw).split("-"):
                if part.startswith("v"):
                    try:
                        max_v = max(max_v, int(part[1:]))
                    except ValueError:
                        continue
        return max_v + 1
    except Exception:  # noqa: BLE001
        return 1


def _delete_document_versions(doc_id: int, keep_version: int | None = None) -> None:
    """删除某文档的全部向量（可按版本保留最新一份）。"""
    try:
        collection = get_collection()
        if keep_version is not None:
            data = collection.get(
                where={"$and": [{"source_type": "document"}, {"doc_id": str(doc_id)}]},
                include=["metadatas"],
            )
            ids = [
                i for i in (data.get("ids") or [])
                if keep_version is None or not i.endswith(f"-v{keep_version}")
            ]
            if ids:
                collection.delete(ids=ids)
        else:
            get_vector_store().delete(
                where={"$and": [{"source_type": "document"}, {"doc_id": str(doc_id)}]}
            )
    except Exception:  # noqa: BLE001
        logger.warning("删除文档向量失败 doc_id=%s（该文档可能仍会被检索到）", doc_id, exc_info=True)


def delete_document(doc_id: int) -> None:
    """删除某文档的全部向量（不存在时静默跳过）。"""
    _delete_document_versions(doc_id)


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
    version = _next_version("knowledge_point", str(point_id))
    get_vector_store().add_documents([doc], ids=[f"point-{point_id}-v{version}"])
    _delete_point_versions(point_id, keep_version=version)


def _delete_point_versions(point_id: int, keep_version: int | None = None) -> None:
    try:
        collection = get_collection()
        data = collection.get(
            where={"$and": [{"source_type": "knowledge_point"}, {"point_id": str(point_id)}]},
            include=["metadatas"],
        )
        ids = [
            i for i in (data.get("ids") or [])
            if keep_version is None or not i.endswith(f"-v{keep_version}")
        ]
        if ids:
            collection.delete(ids=ids)
    except Exception:  # noqa: BLE001
        logger.warning("删除知识点向量失败 point_id=%s（该知识点可能仍会被检索到）", point_id, exc_info=True)


def update_knowledge_point(point_id: int, title: str, content: str, course: str, tags: str) -> None:
    # 版本化重写：先写新版本成功，再删旧版本
    add_knowledge_point(point_id, title, content, course, tags)


def delete_knowledge_point(point_id: int) -> None:
    _delete_point_versions(point_id)


# ---------- 检索 ----------
def search(
    query: str,
    top_k: int | None = None,
    courses: list[str] | None = None,
) -> list[tuple[Document, float]]:
    """相似度检索，返回 (文档, 相似度) 列表，按相似度降序。

    courses 非空时只检索指定课程（知识库）的资料，空/None 表示全库检索。

    score 是余弦相似度（越大越相关），已从 Chroma cosine 距离（1 - cosine）
    显式转换，跨模型可比。
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
        # Chroma cosine space 的 score 是距离（1 - cosine），转回相似度
        deduped.append((doc, 1.0 - float(score)))
    return deduped[:k]
