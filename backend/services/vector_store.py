"""向量库封装：基于 ChromaDB（持久化）+ LangChain Chroma。

两类数据写入同一 collection：
  - 课程文档分块   source_type=document，doc_id=文档ID
  - 手工知识点     source_type=knowledge_point，point_id=知识点ID

相似度口径统一为**余弦相似度**（collection 用 cosine space），score 越大越相关。
这取代了旧版的平方 L2 距离：距离阈值 0.85 这类「越大越差」的写法全部废弃。
"""
from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document

from ..config import settings
from .chunker import split_text
from .embedding_service import get_embedding_model_name, get_embeddings
from .reranker import rerank_documents

logger = logging.getLogger(__name__)

_client: chromadb.ClientAPI | None = None
_vector_store: Chroma | None = None
# 双检锁：多个请求/后台线程同时首访时只初始化一次，避免并发建 client/collection
_client_lock = threading.Lock()
_vector_store_lock = threading.Lock()

# 知识库身份缓存：检索每个请求都要查 vector_key，不缓存等于每请求新开 Session。
# 用短 TTL 兜住 rename/新建等「本模块感知不到」的 DB 变更；删除类操作会主动失效。
_VECTOR_KEY_TTL = 15.0
_scope_cache_lock = threading.Lock()
_scope_cache: tuple[float, dict[str, tuple[str, bool, bool]]] | None = None

# 内置的「已迁移、无数据」Chroma 库模板。部分环境（如 Windows 上的 chromadb 1.5.x）
# 首次初始化全新的持久化目录会因迁移引导失败而报 (code: 14)；预置迁移表后可正常打开。
_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "resources" / "chroma_template.sqlite3"

# Chroma 的 cosine space 返回的距离其实是 1 - cosine_similarity（值域 [0, 2]），
# 与归一化向量的平方 L2 距离正好相等。这里显式转回「相似度」：sim = 1 - distance。
_COSINE_DISTANCE = "cosine"


def _chroma_dir_exists() -> bool:
    """向量目录是否存在。

    目录不存在说明库还没建过（新装/被清理），此时没有任何向量可读可删；
    删除类操作应当按 no-op 处理，而不是因为打开数据库失败抛 500。
    """
    return Path(settings.CHROMA_DIR).exists()


def _bootstrap_chroma_dir() -> None:
    """全新向量目录先用内置模板落一份已迁移的 chroma.sqlite3。

    只在 `chroma.sqlite3` 不存在时执行，绝不覆盖既有库；模板缺失时保持原行为，
    由 PersistentClient 自己报错（便于定位环境问题）。
    """
    db_file = Path(settings.CHROMA_DIR) / "chroma.sqlite3"
    if db_file.exists() or not _TEMPLATE_PATH.is_file():
        return
    Path(settings.CHROMA_DIR).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_TEMPLATE_PATH, db_file)
    logger.info("向量库为空，已使用内置模板初始化：%s", db_file)


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                Path(settings.CHROMA_DIR).mkdir(parents=True, exist_ok=True)
                _bootstrap_chroma_dir()
                try:
                    _client = chromadb.PersistentClient(path=str(settings.CHROMA_DIR))
                except Exception:
                    logger.error(
                        "向量库初始化失败 path=%s（请确认目录可写；目录异常时需备份后重建）",
                        settings.CHROMA_DIR,
                        exc_info=True,
                    )
                    raise
    return _client


def get_collection() -> chromadb.Collection:
    """返回底层 Chroma collection（供需要直接按 metadata 查询的场景使用）。

    优先用 get_vector_store() 做常规读写；只有像「按 doc_id 取某文档全部分块」
    这种 Chroma 原生 where 查询才直接拿 collection，避免让调用方 import 私有 _get_client。
    """
    return _get_client().get_collection(settings.COLLECTION_NAME)


def ensure_collection() -> None:
    """确保主集合存在。

    全新安装（没有任何知识点/文档）时 Chroma 里还没有 collection，
    健康检查会显示 vector_store unavailable、首次检索也会报集合不存在。
    这里按与 Chroma 默认一致的距离函数幂等创建一次。
    """
    client = _get_client()
    try:
        client.get_collection(settings.COLLECTION_NAME)
    except Exception:  # noqa: BLE001  不存在才创建
        client.create_collection(
            name=settings.COLLECTION_NAME,
            metadata={"hnsw:space": _COSINE_DISTANCE},
        )
        logger.info("已创建空向量集合：%s", settings.COLLECTION_NAME)


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


def embedding_model_marker_path() -> Path:
    return Path(settings.CHROMA_DIR) / "embedding_model.txt"


def recorded_embedding_model() -> str | None:
    """读取建库时使用的 embedding 模型标识（没有记录返回 None）。"""
    marker = embedding_model_marker_path()
    try:
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip() or None
    except Exception:  # noqa: BLE001
        logger.warning("读取 embedding 模型标记失败", exc_info=True)
    return None


def record_embedding_model(name: str | None = None) -> None:
    """写入当前 embedding 模型标识（重建完成后再写，避免失败时误判为已对齐）。"""
    marker = embedding_model_marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(name or get_embedding_model_name(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("记录 embedding 模型失败，跳过模型一致性检查", exc_info=True)


def reset_vector_store() -> None:
    """删掉当前 collection 的所有向量并重置懒加载实例（保留目录与模型标记）。

    换 embedding 模型后旧向量在新语义空间里毫无意义，必须先清空再重建；
    这里不删 CHROMA_DIR，模型标记还在，重建失败时下次启动仍能识别出「未对齐」。
    """
    global _vector_store
    with _vector_store_lock:
        try:
            _get_client().delete_collection(settings.COLLECTION_NAME)
            logger.warning("已清空向量集合 %s，准备按新模型重建", settings.COLLECTION_NAME)
        except Exception:  # noqa: BLE001  集合本来就不存在
            logger.info("向量集合不存在，无需清空", exc_info=True)
        _vector_store = None
    ensure_collection()


def iter_vectors() -> list[tuple[str, dict]]:
    """列出全部向量 id 与 metadata（孤儿清理用，不取正文与向量本身）。"""
    if not _chroma_dir_exists():
        return []
    try:
        collection = get_collection()
        data = collection.get(include=["metadatas"])
    except Exception:  # noqa: BLE001  集合不存在
        return []
    ids = data.get("ids") or []
    metas = data.get("metadatas") or [{} for _ in ids]
    return [(str(i), m or {}) for i, m in zip(ids, metas, strict=False)]


def delete_vectors_by_ids(vector_ids: list[str]) -> int:
    """按 id 精确删除向量（孤儿清理），返回删除条数。"""
    if not vector_ids or not _chroma_dir_exists():
        return 0
    get_collection().delete(ids=vector_ids)
    return len(vector_ids)


def _assert_embedding_model() -> None:
    """记录并检查 embedding 模型身份。

    维度校验只能发现「换成了不同维度的模型」；同维度换模型（例如两个 1024 维模型）
    不会报错，但相似度分布和 UNMATCHED_SIMILARITY 会静默漂移。这里在 CHROMA_DIR
    下留一个 sidecar 文件；模型变化时给出警告并**不覆盖标记**，由启动维护
    （vector_reconcile.reconcile_embedding_model）重建全库后才重写标记。
    """
    current = get_embedding_model_name()
    recorded = recorded_embedding_model()
    if recorded and recorded != current:
        logger.warning(
            "embedding 模型已变化（%s → %s）：现有向量与新模型不在同一语义空间，"
            "启动维护会重建向量库；若重建未完成，请检查日志或手动重建。",
            recorded,
            current,
        )
        return
    record_embedding_model(current)


def get_vector_store() -> Chroma:
    """懒加载向量库（首次调用时创建 collection，并校验维度与空间）。"""
    global _vector_store
    if _vector_store is None:
        with _vector_store_lock:
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
                _assert_embedding_model()
                _vector_store = Chroma(
                    client=_get_client(),
                    collection_name=settings.COLLECTION_NAME,
                    embedding_function=get_embeddings(),
                    collection_metadata={"hnsw:space": _COSINE_DISTANCE},
                )
    return _vector_store


def _doc(text: str, metadata: dict) -> Document:
    return Document(page_content=text, metadata=metadata)


def _chunk_locator(text: str) -> tuple[str, str]:
    """从 PDF 页标记和 Markdown/章节标题提取引用定位信息。"""
    page_match = re.search(r"【第\s*(\d+)\s*页】", text)
    page = page_match.group(1) if page_match else ""

    heading = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", text)
    if heading:
        section = heading.group(1).strip()
    else:
        numbered = re.search(
            r"(?m)^(第[一二三四五六七八九十百\d]+[章节][^\n]{0,80})$", text
        )
        section = numbered.group(1).strip() if numbered else ""
    return page, section


def _chunk_locators(chunks: list[str]) -> list[tuple[str, str]]:
    """为每个分块补齐最近一次出现的页码和章节。"""
    current_page = ""
    current_section = ""
    locators: list[tuple[str, str]] = []
    for text in chunks:
        page, section = _chunk_locator(text)
        if page:
            current_page = page
        if section:
            current_section = section
        locators.append((current_page, current_section))
    return locators


def invalidate_vector_key_cache() -> None:
    """知识库删除 / 重建后主动失效缓存；其他进程的变更最多滞后 TTL。"""
    global _scope_cache
    with _scope_cache_lock:
        _scope_cache = None


def _scope_snapshot() -> dict[str, tuple[str, bool, bool]]:
    """缓存 课程名 -> (vector_key, 是否有效, 是否可兼容旧向量)。

    检索 / 入库每次都要判断 vector_key 与 tombstone，这里用 15s 小缓存
    避免每个请求都新开 Session；删除知识库时会调用 invalidate_vector_key_cache()。
    """
    global _scope_cache
    cached = _scope_cache
    if cached is not None and time.monotonic() - cached[0] < _VECTOR_KEY_TTL:
        return cached[1]
    with _scope_cache_lock:
        cached = _scope_cache
        if cached is not None and time.monotonic() - cached[0] < _VECTOR_KEY_TTL:
            return cached[1]
        from sqlalchemy import select

        from ..database import SessionLocal
        from ..models import KnowledgeBase, KnowledgeBaseTombstone

        with SessionLocal() as db:
            rows = db.execute(
                select(KnowledgeBase.name, KnowledgeBase.vector_key)
            ).all()
            tombstones = db.execute(
                select(
                    KnowledgeBaseTombstone.course_name,
                    KnowledgeBaseTombstone.knowledge_base_id,
                )
            ).all()
        tombstoned_names = {str(name) for name, _key in tombstones}
        tombstoned_keys = {str(key) for _name, key in tombstones if key}
        snapshot = {
            str(name): (
                str(key or ""),
                bool(key) and str(key) not in tombstoned_keys,
                str(name) not in tombstoned_names,
            )
            for name, key in rows
        }
        _scope_cache = (time.monotonic(), snapshot)
        return snapshot


def _knowledge_base_vector_key(course: str) -> str:
    """按课程名读取当前知识库的稳定向量身份（带短 TTL 缓存）。"""
    if not course:
        return ""
    entry = _scope_snapshot().get(course)
    return entry[0] if entry else ""


def _active_vector_scope(
    courses: list[str] | None,
) -> tuple[set[str], set[str]]:
    """返回当前有效知识库身份与可接受旧 metadata 的课程名（带短 TTL 缓存）。"""
    snapshot = _scope_snapshot()
    if courses:
        selected = [(name, snapshot[name]) for name in courses if name in snapshot]
    else:
        selected = list(snapshot.items())
    active_keys = {key for _name, (key, active, _legacy) in selected if active}
    legacy_courses = {name for name, (_key, _active, legacy) in selected if legacy}
    return active_keys, legacy_courses


def is_active_vector(
    doc: Document,
    active_keys: set[str],
    legacy_courses: set[str],
) -> bool:
    """把向量记录限定到当前有效知识库身份。

    新向量必须命中 active_keys；旧版没有 knowledge_base_id 的向量只允许在
    课程尚未删除（无 tombstone）时兼容。
    """
    src = doc.metadata or {}
    kb_id = str(src.get("knowledge_base_id") or "")
    if kb_id:
        return kb_id in active_keys
    course = str(src.get("course") or "")
    return bool(course) and course in legacy_courses


# ---------- 文档 ----------
def add_document_chunks(
    doc_id: int,
    filename: str,
    course: str,
    chunks: list[str],
    *,
    knowledge_base_id: str | None = None,
) -> int:
    """文档分块入库，返回写入块数。

    版本化 id：先写新版本（version 自增），成功后再删旧版本。这样 add 中途失败时
    旧向量还在，DB 里的 status 也保持 failed，而不是「向量全丢、DB 却标 ready」。
    """
    if not chunks:
        return 0
    # 文档分块同时写 title（去扩展名）和 filename（完整名）：
    # title 供引用卡展示干净的标题（不带 .pdf 后缀），filename 供删文档时精确回退反馈。
    title = Path(filename).stem or filename
    knowledge_base_id = knowledge_base_id or _knowledge_base_vector_key(course)
    docs = []
    for i, (text, (page, section)) in enumerate(
        zip(chunks, _chunk_locators(chunks), strict=False)
    ):
        docs.append(
            _doc(
                text,
                {
                    "source_type": "document",
                    "doc_id": str(doc_id),
                    "filename": filename,
                    "title": title,
                    "course": course,
                    "knowledge_base_id": knowledge_base_id,
                    "chunk_index": i,
                    "chunk_id": f"{doc_id}:{i}",
                    "page": page,
                    "section": section,
                },
            )
        )
    version = _next_version("document", str(doc_id))
    ids = [f"doc-{doc_id}-{i}-v{version}" for i in range(len(chunks))]
    try:
        get_vector_store().add_documents(docs, ids=ids)
    except Exception:
        # add_documents 分批写入，失败时可能已留下半截分块；按本次 id 精确删除，
        # 旧版本 id 带不同 -vN 后缀，不会被误删。
        try:
            get_collection().delete(ids=ids)
        except Exception:  # noqa: BLE001 清理失败也保留原始异常，由上层标 failed
            logger.warning(
                "向量写入失败后清理半截分块失败 doc_id=%s version=%s",
                doc_id,
                version,
                exc_info=True,
            )
        raise
    # 新版本写入成功，再清旧版本（不包含刚写的 version）
    _delete_document_versions(doc_id, keep_version=version)
    return len(chunks)


def _vector_id_version(vector_id: str) -> str | None:
    """从向量 id 里取出版本号（`point-3-v2-c1` → `2`），解析不出返回 None。

    知识点是多分块写入，id 形如 `point-{id}-v{n}-c{i}`，不能再用 endswith("-v{n}")
    判断版本，否则清理旧版本时会把新版本一起删掉。
    """
    text = str(vector_id)
    marker = text.rfind("-v")
    if marker < 0:
        return None
    head = text[marker + 2 :].split("-", 1)[0]
    return head if head.isdigit() else None


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


def _delete_document_versions(
    doc_id: int, keep_version: int | None = None, *, strict: bool = False
) -> bool:
    """删除某文档的全部向量（可按版本保留最新一份）。

    返回是否成功。strict=True 时失败会抛出，供「先删向量、再删 DB」的调用方
    保留记录让用户重试；strict=False 用于 DB 已删后的收尾，失败只记日志。
    """
    if not _chroma_dir_exists():
        return True
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
        logger.error(
            "删除文档向量失败 doc_id=%s（该文档可能仍会被检索到）", doc_id, exc_info=True
        )
        if strict:
            raise
        return False
    return True


def delete_document(doc_id: int, *, strict: bool = True) -> bool:
    """删除某文档的全部向量（不存在时静默跳过）。

    默认 strict=True：删除失败会抛出，调用方据此保留 DB 记录并让用户重试。
    """
    return _delete_document_versions(doc_id, strict=strict)


# ---------- 知识点 ----------
def add_knowledge_point(
    point_id: int,
    title: str,
    content: str,
    course: str,
    tags: str,
    *,
    knowledge_base_id: str | None = None,
) -> None:
    knowledge_base_id = knowledge_base_id or _knowledge_base_vector_key(course)
    # 知识点正文可以写到两万字，整段压成一个向量必然超过模型输入上限
    # （bge-m3 是 8192 token），超出部分会被静默截断——搜索里只有前几百字有效。
    # 按 chunker 的规则切块，每块都带上标题作上下文，块内保留 chunk_index 供去重。
    body = f"{title}\n{content}".strip()
    pieces = split_text(body) or [body]
    if len(pieces) > 1:
        pieces = [f"{title}\n{piece}" if not piece.startswith(title) else piece for piece in pieces]
    docs = [
        _doc(
            piece,
            {
                "source_type": "knowledge_point",
                "point_id": str(point_id),
                "title": title,
                "course": course,
                "knowledge_base_id": knowledge_base_id,
                "tags": tags,
                "chunk_index": i,
                "chunk_id": f"point-{point_id}:{i}",
            },
        )
        for i, piece in enumerate(pieces)
    ]
    version = _next_version("knowledge_point", str(point_id))
    ids = [f"point-{point_id}-v{version}-c{i}" for i in range(len(docs))]
    try:
        get_vector_store().add_documents(docs, ids=ids)
    except Exception:
        # 分批写入可能只成功一半，按本次 id 精确回滚，旧版本不受影响
        try:
            get_collection().delete(ids=ids)
        except Exception:  # noqa: BLE001  清理失败保留原始异常
            logger.warning(
                "知识点向量写入失败后清理半截分块失败 point_id=%s version=%s",
                point_id,
                version,
                exc_info=True,
            )
        raise
    _delete_point_versions(point_id, keep_version=version)


def _delete_point_versions(
    point_id: int, keep_version: int | None = None, *, strict: bool = False
) -> bool:
    if not _chroma_dir_exists():
        return True
    try:
        collection = get_collection()
        data = collection.get(
            where={"$and": [{"source_type": "knowledge_point"}, {"point_id": str(point_id)}]},
            include=["metadatas"],
        )
        ids = [
            i for i in (data.get("ids") or [])
            if keep_version is None
            or _vector_id_version(i) != str(keep_version)
        ]
        if ids:
            collection.delete(ids=ids)
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc).lower():
            return True
        logger.error(
            "删除知识点向量失败 point_id=%s（该知识点可能仍会被检索到）",
            point_id,
            exc_info=True,
        )
        if strict:
            raise
        return False
    return True


def update_knowledge_point(point_id: int, title: str, content: str, course: str, tags: str) -> None:
    # 版本化重写：先写新版本成功，再删旧版本
    add_knowledge_point(point_id, title, content, course, tags)


def delete_knowledge_point(point_id: int, *, strict: bool = True) -> bool:
    """删除某知识点的全部向量；默认 strict=True，失败时抛出供调用方保留 DB 记录。"""
    return _delete_point_versions(point_id, strict=strict)


def delete_knowledge_base_vectors(
    knowledge_base_id: str, course: str, *, strict: bool = True
) -> bool:
    """删除某知识库的全部向量，包括没有 knowledge_base_id 的旧版记录。"""
    # 知识库删除后，按课程名缓存的 vector_key / tombstone 立刻失效，
    # 避免检索在 TTL 内继续使用已删知识库的身份。
    invalidate_vector_key_cache()
    if not _chroma_dir_exists():
        return True
    try:
        try:
            collection = get_collection()
        except Exception as exc:  # noqa: BLE001
            if "does not exist" in str(exc).lower():
                return True
            raise
        data = collection.get(
            where={"course": course},
            include=["metadatas"],
        )
        ids = [
            vec_id
            for vec_id, metadata in zip(
                data.get("ids") or [],
                data.get("metadatas") or [],
                strict=False,
            )
            if str((metadata or {}).get("knowledge_base_id") or "") in (
                "",
                knowledge_base_id,
            )
        ]
        if ids:
            collection.delete(ids=ids)
    except Exception:  # noqa: BLE001
        logger.error(
            "删除知识库向量失败 knowledge_base_id=%s course=%s",
            knowledge_base_id,
            course,
            exc_info=True,
        )
        if strict:
            raise
        return False
    return True


# ---------- 检索 ----------
def _query_by_vector(
    store: Chroma,
    embedding: list[float],
    k: int,
    filter_: dict | None = None,
) -> list[tuple[Document, float]]:
    """按查询向量检索，返回 (文档, cosine 距离)，与 Chroma 内置检索同口径。

    langchain-chroma 不同版本的「按向量检索」方法名不一致（有的版本没有
    ``similarity_search_with_score_by_vector``），这里直接走底层 collection，
    既避免版本差异，也让检索调试能在真实向量库上工作。
    """
    collection = getattr(store, "_collection", None)
    if collection is None:  # 兜底：拿不到底层 collection 时退回库自带方法
        return store.similarity_search_with_score_by_vector(
            embedding, k=k, filter=filter_
        )
    payload = collection.query(
        query_embeddings=[list(embedding)],
        n_results=k,
        where=filter_ or None,
        include=["documents", "metadatas", "distances"],
    )
    documents = (payload.get("documents") or [[]])[0]
    metadatas = (payload.get("metadatas") or [[]])[0]
    distances = (payload.get("distances") or [[]])[0]
    return [
        (Document(page_content=text or "", metadata=dict(meta or {})), float(dist))
        for text, meta, dist in zip(documents, metadatas, distances)
    ]


# 单份资料（同一文档 / 同一知识点）在一次检索里最多贡献的片段数
MAX_CHUNKS_PER_SOURCE = 2


def _search_pipeline(
    query: str,
    top_k: int | None = None,
    courses: list[str] | None = None,
    source_types: tuple[str, ...] | list[str] | None = None,
    *,
    embedding: list[float] | None = None,
) -> tuple[list[tuple[Document, float]], dict]:
    """检索主流程，search() 与检索调试共用，返回 (最终结果, 过程信息)。

    embedding 非 None 时直接用这个查询向量检索（调试端点因此能把「向量化」
    和「向量查询」分段计时）；为 None 时保持原行为，交给 Chroma 内部向量化。
    """
    k = top_k or settings.TOP_K
    timings = {"embed": 0.0, "query": 0.0, "rerank": 0.0}
    trace = {
        "k": k,
        "candidate_k": 0,
        "timings": timings,
        "before_rerank": [],
        "after_rerank": [],
    }
    # courses=None 表示不限制；courses=[] 表示"没有可检索的范围"，必须返回空。
    # 否则空列表会被当成"不过滤"，反而把全部知识库都捞出来。
    if courses is not None and not courses:
        return [], trace
    conditions: list[dict] = []
    if courses:
        conditions.append({"course": {"$in": courses}})
    if source_types:
        conditions.append({"source_type": {"$in": list(source_types)}})
    filter_ = None
    if len(conditions) == 1:
        filter_ = conditions[0]
    elif len(conditions) > 1:
        filter_ = {"$and": conditions}
    candidate_k = max(k * 4, settings.RERANK_CANDIDATES) if settings.RERANK_ENABLED else max(k * 3, 12)
    trace["candidate_k"] = candidate_k
    store = get_vector_store()
    query_started = time.perf_counter()
    if embedding is None:
        results = store.similarity_search_with_score(query, k=candidate_k, filter=filter_)
    else:
        results = _query_by_vector(store, embedding, candidate_k, filter_)
    timings["query"] = (time.perf_counter() - query_started) * 1000
    active_keys, legacy_courses = _active_vector_scope(courses)

    # 去重：同一文档的同一分块、以及内容完全相同的资料，只保留最相关的一条
    seen_keys: set[tuple] = set()
    seen_content: set[str] = set()
    per_source: dict[tuple, int] = {}
    deduped: list[tuple[Document, float]] = []
    for doc, score in results:
        if not is_active_vector(doc, active_keys, legacy_courses):
            continue
        src = doc.metadata
        source_key = (
            src.get("course"),
            src.get("doc_id") or src.get("point_id") or src.get("filename") or src.get("title"),
        )
        key = source_key + (src.get("chunk_index"),)
        content = doc.page_content.strip()
        if key in seen_keys or content in seen_content:
            continue
        # 同一份资料最多贡献 MAX_CHUNKS_PER_SOURCE 条：长文档/长知识点会被切成很多块，
        # 不设上限时一篇命中率高的文档能把 top_k 全部占满，其它资料直接被挤掉。
        if per_source.get(source_key, 0) >= MAX_CHUNKS_PER_SOURCE:
            continue
        seen_keys.add(key)
        seen_content.add(content)
        per_source[source_key] = per_source.get(source_key, 0) + 1
        # Chroma cosine space 的 score 是距离（1 - cosine），转回相似度
        deduped.append((doc, 1.0 - float(score)))
    trace["before_rerank"] = list(deduped)
    if settings.RERANK_ENABLED:
        rerank_started = time.perf_counter()
        deduped = rerank_documents(
            query,
            deduped,
            vector_weight=settings.RERANK_VECTOR_WEIGHT,
            lexical_weight=settings.RERANK_LEXICAL_WEIGHT,
            title_weight=settings.RERANK_TITLE_WEIGHT,
        )
        timings["rerank"] = (time.perf_counter() - rerank_started) * 1000
        selected = deduped[:k]
        if selected:
            semantic_best = max(deduped, key=lambda item: item[1])
            if all(doc is not semantic_best[0] for doc, _score in selected):
                selected[-1] = semantic_best
        trace["after_rerank"] = deduped
        return selected, trace
    trace["after_rerank"] = list(deduped)
    return deduped[:k], trace


def search(
    query: str,
    top_k: int | None = None,
    courses: list[str] | None = None,
    source_types: tuple[str, ...] | list[str] | None = None,
) -> list[tuple[Document, float]]:
    """相似度检索，返回 (文档, 相似度) 列表，按相似度降序。

    courses 非空时只检索指定课程（知识库）的资料，空/None 表示全库检索。

    score 是余弦相似度（越大越相关），已从 Chroma cosine 距离（1 - cosine）
    显式转换，跨模型可比。
    """
    return _search_pipeline(query, top_k, courses, source_types)[0]


def search_trace(
    query: str,
    top_k: int | None = None,
    courses: list[str] | None = None,
) -> dict:
    """检索调试用：返回候选、重排前后顺序与分段耗时（毫秒），不改变正式检索行为。

    和 search() 共用 _search_pipeline，唯一区别是先自己算一次查询向量，
    这样才能把 embedding 与向量查询分开计时。
    """
    timings = {"embed": 0.0, "query": 0.0, "rerank": 0.0}
    embed_started = time.perf_counter()
    embedding = get_embeddings().embed_query(query)
    timings["embed"] += (time.perf_counter() - embed_started) * 1000
    selected, trace = _search_pipeline(query, top_k, courses, embedding=embedding)
    timings["query"] += trace["timings"]["query"]
    timings["rerank"] += trace["timings"]["rerank"]
    return {
        "selected": selected,
        "before_rerank": trace["before_rerank"],
        "after_rerank": trace["after_rerank"],
        "k": trace["k"],
        "candidate_k": trace["candidate_k"],
        "rerank_enabled": bool(settings.RERANK_ENABLED),
        "timings": timings,
    }
