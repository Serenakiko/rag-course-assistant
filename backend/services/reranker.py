"""轻量混合重排：向量相似度 + BM25 词法相关性。"""
from __future__ import annotations

import math
import re
from collections import Counter

from langchain_core.documents import Document

_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9.+#_-]*")
_CJK_SEQUENCE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """英文按词、中文按二元组切分，避免引入分词依赖。"""
    normalized = (text or "").lower()
    tokens = _ASCII_TERM.findall(normalized)
    for sequence in _CJK_SEQUENCE.findall(normalized):
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(sequence[i : i + 2] for i in range(len(sequence) - 1))
    return tokens


def _bm25_scores(query: str, docs: list[Document]) -> list[float]:
    if not docs:
        return []
    query_terms = set(tokenize(query))
    if not query_terms:
        return [0.0] * len(docs)

    doc_terms = [tokenize(doc.page_content) for doc in docs]
    lengths = [len(terms) for terms in doc_terms]
    avg_len = sum(lengths) / max(1, len(lengths))
    df = Counter()
    for terms in doc_terms:
        df.update(set(terms))

    scores: list[float] = []
    k1 = 1.5
    b = 0.75
    for terms, length in zip(doc_terms, lengths, strict=False):
        tf = Counter(terms)
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
            denom = freq + k1 * (1 - b + b * length / max(avg_len, 1e-9))
            score += idf * freq * (k1 + 1) / max(denom, 1e-9)
        scores.append(score)
    return scores


def _title_boost(query: str, doc: Document) -> float:
    query_terms = set(tokenize(query))
    title = (doc.metadata or {}).get("title") or ""
    section = (doc.metadata or {}).get("section") or ""
    title_terms = set(tokenize(title))
    section_terms = set(tokenize(section))
    if not query_terms:
        return 0.0
    title_overlap = len(query_terms & title_terms) / len(query_terms)
    section_overlap = len(query_terms & section_terms) / len(query_terms)
    return min(1.0, title_overlap * 0.75 + section_overlap * 0.25)


def rerank_documents(
    query: str,
    ranked: list[tuple[Document, float]],
    *,
    vector_weight: float = 0.95,
    lexical_weight: float = 0.05,
) -> list[tuple[Document, float]]:
    """按混合分数重排候选，但保留原向量相似度作为返回分数。"""
    if not ranked:
        return []
    docs = [doc for doc, _score in ranked]
    lexical = _bm25_scores(query, docs)
    lexical_max = max(lexical, default=0.0)
    enriched: list[tuple[float, int, Document, float]] = []
    for index, ((doc, vector_score), lexical_score) in enumerate(
        zip(ranked, lexical, strict=False)
    ):
        lexical_norm = lexical_score / lexical_max if lexical_max > 0 else 0.0
        boost = _title_boost(query, doc) * 0.05
        combined = (
            vector_weight * max(0.0, min(1.0, float(vector_score)))
            + lexical_weight * lexical_norm
            + boost
        )
        metadata = dict(doc.metadata or {})
        metadata["rerank_score"] = round(combined, 6)
        metadata["lexical_score"] = round(lexical_norm, 6)
        doc.metadata = metadata
        enriched.append((combined, index, doc, float(vector_score)))

    enriched.sort(key=lambda item: (-item[0], item[1]))
    return [(doc, vector_score) for _combined, _index, doc, vector_score in enriched]
