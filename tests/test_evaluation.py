"""检索评测的作用域与类型化资料命中。"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

from langchain_core.documents import Document


def test_relevant_match_supports_knowledge_base_document_and_chunk_ids():
    from evaluation.evaluate import matches_relevant

    doc = Document(
        page_content="进程与线程",
        metadata={
            "knowledge_base_id": "kb-operating-system",
            "doc_id": "42",
            "chunk_id": "chunk-42-3",
            "title": "操作系统讲义",
        },
    )
    item = {
        "relevant_knowledge_base_ids": ["kb-operating-system"],
        "relevant_document_ids": ["42"],
        "relevant_chunk_ids": ["chunk-42-3"],
    }

    assert matches_relevant(doc, item)
    assert not matches_relevant(
        Document(page_content="干扰", metadata={"doc_id": "99"}), item
    )


def test_knowledge_point_scope_does_not_penalize_document_chunks(monkeypatch):
    """知识点库评测只检索知识点，不被完整混合语料中的文档分块挤到第 2 位。"""
    from evaluation import evaluate as evaluate_mod

    point = Document(
        page_content="进程与线程",
        metadata={
            "source_type": "knowledge_point",
            "point_id": "11",
            "knowledge_base_id": "kb-os",
            "title": "进程与线程",
        },
    )
    document = Document(
        page_content="操作系统课程文档",
        metadata={
            "source_type": "document",
            "doc_id": "42",
            "chunk_id": "chunk-42-1",
            "knowledge_base_id": "kb-os",
            "title": "课程讲义",
        },
    )

    def fake_search(_question, top_k=5, source_types=None, **_kwargs):
        assert top_k == 5
        if source_types == ("knowledge_point",):
            return [(point, 0.8)]
        return [(document, 0.9), (point, 0.8)]

    monkeypatch.setattr(evaluate_mod, "search", fake_search)
    golden = [
        {
            "question": "进程和线程有什么区别？",
            "course": "操作系统",
            "relevant_point_ids": ["11"],
            "relevant_titles": ["进程与线程"],
            "expect_unmatched": False,
            "category": "answerable",
        }
    ]

    knowledge_point_result = evaluate_mod.evaluate(
        golden, top_k=5, scope="knowledge_points"
    )
    full_result = evaluate_mod.evaluate(golden, top_k=5, scope="full")

    assert knowledge_point_result["mrr"] == 1.0
    assert full_result["mrr"] == 0.5
    assert knowledge_point_result["scope"] == "knowledge_points"
    assert full_result["scope"] == "full"
    assert knowledge_point_result["reranker"]["enabled"] is True
    assert knowledge_point_result["reranker"]["vector_weight"] == 0.95
    assert knowledge_point_result["reranker"]["lexical_weight"] == 0.05
    assert knowledge_point_result["embedding_model"]


def test_top_similarity_uses_max_semantic_score_after_rerank():
    from langchain_core.documents import Document

    from evaluation.evaluate import _top_similarity

    docs = [
        (Document(page_content="重排后的首选", metadata={}), 0.40),
        (Document(page_content="语义最高候选", metadata={}), 0.82),
    ]
    assert _top_similarity(docs) == 0.82


def test_relevance_checks_all_typed_id_fields():
    from evaluation.evaluate import matches_relevant

    doc = Document(
        page_content="进程与线程",
        metadata={"knowledge_base_id": "kb-other", "point_id": "11"},
    )
    item = {
        "relevant_knowledge_base_ids": ["kb-os"],
        "relevant_point_ids": ["11"],
    }

    assert matches_relevant(doc, item)
