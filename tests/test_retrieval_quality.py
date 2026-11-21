"""混合重排、旧向量引用回填与评测数据回归。"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from langchain_core.documents import Document


def test_hybrid_reranker_promotes_exact_title_match():
    from backend.services.reranker import rerank_documents

    query = "进程和线程有什么区别？"
    unrelated = Document(
        page_content="OSI 七层模型从物理层到应用层依次分层。",
        metadata={"title": "网络体系结构", "course": "计算机网络"},
    )
    target = Document(
        page_content="进程是资源分配单位，线程是 CPU 调度单位，线程切换开销更小。",
        metadata={"title": "进程与线程", "course": "操作系统"},
    )

    reranked = rerank_documents(
        query,
        [(unrelated, 0.82), (target, 0.74)],
        vector_weight=0.7,
        lexical_weight=0.3,
    )

    assert reranked[0][0] is target
    assert reranked[0][0].metadata["rerank_score"] > reranked[1][0].metadata["rerank_score"]


def test_vector_search_preserves_semantic_score_after_rerank(monkeypatch):
    from backend.services import vector_store

    unrelated = Document(
        page_content="OSI 七层模型从物理层到应用层依次分层。",
        metadata={
            "source_type": "knowledge_point",
            "course": "计算机网络",
            "knowledge_base_id": "kb-net",
            "title": "网络体系结构",
        },
    )
    target = Document(
        page_content="进程是资源分配单位，线程是 CPU 调度单位。",
        metadata={
            "source_type": "knowledge_point",
            "course": "操作系统",
            "knowledge_base_id": "kb-os",
            "title": "进程与线程",
        },
    )

    class FakeStore:
        def similarity_search_with_score(self, *_args, **_kwargs):
            return [(unrelated, 0.18), (target, 0.26)]

    monkeypatch.setattr(vector_store, "get_vector_store", lambda: FakeStore())
    monkeypatch.setattr(
        vector_store,
        "_active_vector_scope",
        lambda _courses: ({"kb-net", "kb-os"}, set()),
    )
    monkeypatch.setattr(vector_store.settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(vector_store.settings, "RERANK_VECTOR_WEIGHT", 0.7)
    monkeypatch.setattr(vector_store.settings, "RERANK_LEXICAL_WEIGHT", 0.3)

    result = vector_store.search("进程和线程有什么区别？", top_k=2)

    assert len(result) == 2
    assert result[0][0] is target
    assert result[0][1] == 0.74


def test_vector_search_keeps_highest_semantic_candidate_after_top_k(monkeypatch):
    from backend.services import vector_store

    semantic_best = Document(
        page_content="语义最高但没有词法命中",
        metadata={
            "source_type": "knowledge_point",
            "course": "操作系统",
            "knowledge_base_id": "kb-os",
            "title": "语义候选",
            "point_id": "100",
        },
    )
    lexical_matches = [
        Document(
            page_content="进程 进程 进程 进程 进程",
            metadata={
                "source_type": "knowledge_point",
                "course": "操作系统",
                "knowledge_base_id": "kb-os",
                "title": f"进程候选 {i}",
                "point_id": str(i),
            },
        )
        for i in range(1, 7)
    ]

    class FakeStore:
        def similarity_search_with_score(self, *_args, **_kwargs):
            rows = [(semantic_best, 0.40)]
            rows.extend((doc, 0.45) for doc in lexical_matches)
            return rows

    monkeypatch.setattr(vector_store, "get_vector_store", lambda: FakeStore())
    monkeypatch.setattr(
        vector_store, "_active_vector_scope", lambda _courses: ({"kb-os"}, set())
    )
    monkeypatch.setattr(vector_store.settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(vector_store.settings, "RERANK_VECTOR_WEIGHT", 0.7)
    monkeypatch.setattr(vector_store.settings, "RERANK_LEXICAL_WEIGHT", 0.3)

    result = vector_store.search("进程", top_k=5)

    assert semantic_best in [doc for doc, _score in result]
    assert max(score for _doc, score in result) == 0.6


def test_vector_add_accepts_explicit_knowledge_base_identity(monkeypatch):
    from backend.services import vector_store

    captured = []

    class FakeStore:
        def add_documents(self, docs, ids):
            captured.extend(zip(ids, docs, strict=False))

    monkeypatch.setattr(vector_store, "get_vector_store", lambda: FakeStore())
    monkeypatch.setattr(vector_store, "_next_version", lambda *_args: 1)
    monkeypatch.setattr(vector_store, "_delete_point_versions", lambda *_args, **_kwargs: True)

    vector_store.add_knowledge_point(
        11,
        "进程与线程",
        "进程是资源分配单位。",
        "操作系统",
        "",
        knowledge_base_id="kb-restored",
    )

    assert captured[0][1].metadata["knowledge_base_id"] == "kb-restored"


def test_long_knowledge_point_is_split_into_chunked_vectors(monkeypatch):
    """两万字的知识点不能压成一个向量：超过模型输入上限的部分会被静默截断。"""
    from backend.services import vector_store

    captured = []

    class FakeStore:
        def add_documents(self, docs, ids):
            captured.extend(zip(ids, docs, strict=False))

    monkeypatch.setattr(vector_store, "get_vector_store", lambda: FakeStore())
    monkeypatch.setattr(vector_store, "_next_version", lambda *_args: 3)
    monkeypatch.setattr(vector_store, "_delete_point_versions", lambda *_args, **_kwargs: True)

    long_content = "进程是资源分配的基本单位，线程是 CPU 调度的基本单位。" * 200
    vector_store.add_knowledge_point(7, "进程与线程", long_content, "操作系统", "")

    assert len(captured) > 1, "长知识点应切成多个分块向量"
    ids = [vector_id for vector_id, _doc in captured]
    assert ids[0] == "point-7-v3-c0"
    assert all(vector_store._vector_id_version(i) == "3" for i in ids)
    assert [doc.metadata["chunk_index"] for _i, doc in captured] == list(range(len(captured)))
    # 每块都要带标题作上下文，否则单独一块看不出讲的是哪个知识点
    assert all("进程与线程" in doc.page_content for _i, doc in captured)


def test_vector_id_version_parses_chunked_ids():
    from backend.services.vector_store import _vector_id_version

    assert _vector_id_version("point-7-v3-c0") == "3"
    assert _vector_id_version("point-7-v12") == "12"
    assert _vector_id_version("doc-1-4-v2") == "2"
    assert _vector_id_version("no-version-here") is None


def test_vector_search_caps_chunks_per_source(monkeypatch):
    """同一份资料的多块不能把 top_k 全占满，其它资料至少留出位置。"""
    from backend.services import vector_store

    def point_doc(pid, idx, text):
        return Document(
            page_content=text,
            metadata={
                "source_type": "knowledge_point",
                "course": "操作系统",
                "knowledge_base_id": "kb-os",
                "title": f"知识点 {pid}",
                "point_id": str(pid),
                "chunk_index": idx,
            },
        )

    rows = [
        (point_doc(1, 0, "进程 进程 进程 甲"), 0.30),
        (point_doc(1, 1, "进程 进程 进程 乙"), 0.32),
        (point_doc(1, 2, "进程 进程 进程 丙"), 0.34),
        (point_doc(2, 0, "线程 线程 线程 丁"), 0.40),
    ]

    class FakeStore:
        def similarity_search_with_score(self, *_args, **_kwargs):
            return rows

    monkeypatch.setattr(vector_store, "get_vector_store", lambda: FakeStore())
    monkeypatch.setattr(
        vector_store, "_active_vector_scope", lambda _courses: ({"kb-os"}, set())
    )
    monkeypatch.setattr(vector_store.settings, "RERANK_ENABLED", False)

    result = vector_store.search("进程", top_k=4)
    point_ids = [doc.metadata["point_id"] for doc, _score in result]

    assert point_ids.count("1") == vector_store.MAX_CHUNKS_PER_SOURCE
    assert "2" in point_ids


def test_format_sources_backfills_legacy_page_and_section():
    from backend.services.rag_service import _format_sources

    legacy = Document(
        page_content="【第 3 页】\n## 进程管理\n进程是资源分配的基本单位。",
        metadata={
            "source_type": "document",
            "filename": "操作系统讲义.pdf",
            "title": "操作系统讲义",
            "course": "操作系统",
            "chunk_index": 0,
        },
    )

    source = _format_sources([(legacy, 0.8)])[0]
    assert source["page"] == "3"
    assert source["section"] == "进程管理"


def test_rag_gate_uses_max_semantic_candidate_after_rerank():
    from backend.services.rag_service import _max_similarity

    docs = [
        (Document(page_content="重排首选", metadata={}), 0.22),
        (Document(page_content="语义最高", metadata={}), 0.81),
    ]
    assert _max_similarity(docs) == 0.81


def test_chunk_locators_propagate_page_and_section_across_chunks():
    from backend.services.vector_store import _chunk_locators

    locators = _chunk_locators(
        [
            "【第 3 页】\n## 进程管理\n进程是资源分配单位。",
            "线程是 CPU 调度单位。",
            "【第 4 页】\n线程切换开销更小。",
        ]
    )

    assert locators == [("3", "进程管理"), ("3", "进程管理"), ("4", "进程管理")]


def test_pdf_extraction_summary_reports_page_quality():
    from evaluation.pdf_extraction import summarize_pages

    summary = summarize_pages(
        [
            "第一章 进程管理\n" + "进程是资源分配单位。" * 5,
            "",
            "短页",
        ]
    )

    assert summary["page_count"] == 3
    assert summary["nonempty_pages"] == 2
    assert summary["short_pages"] == 2
    assert summary["marker_coverage"] == 0.6667
    assert summarize_pages([])["status"] == "no_samples"


def test_student_question_dataset_has_unique_typed_labels():
    path = Path(__file__).resolve().parent.parent / "evaluation" / "student_questions.json"
    if not path.is_file():
        pytest.skip("缺少 evaluation/student_questions.json（只做部分拷贝时不会带上评测集）")
    items = json.loads(path.read_text(encoding="utf-8"))

    assert len(items) >= 15
    questions = [item["question"] for item in items]
    assert len(questions) == len(set(questions))
    assert all(item.get("source_message_id") for item in items)
    assert all(
        item.get("relevant_titles")
        or item.get("relevant_point_ids")
        or item.get("relevant_document_ids")
        for item in items
    )
