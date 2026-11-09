"""文档内容指纹与近似去重。

文件名不能代表资料内容；同一知识库里换名重传、只改少量文字，仍会污染
知识点列表和向量检索。这里用归一化哈希处理排版差异，用 SimHash 处理
轻微内容变化。
"""
from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Protocol

MAX_FINGERPRINT_CHARS = 50_000
NEAR_DUPLICATE_HAMMING = 3
NEAR_DUPLICATE_MIN_LENGTH_RATIO = 0.90
# 短代码片段和公式即使只差一个符号也可能语义相反，不做近似判定。
NEAR_DUPLICATE_MIN_CHARS = 200


class FingerprintedDocument(Protocol):
    id: int
    filename: str
    status: str
    content_hash: str
    content_simhash: int | str | None
    content_length: int


@dataclass(frozen=True)
class DocumentFingerprint:
    content_hash: str
    simhash: int
    length: int


def _normalize_text(text: str) -> str:
    """消除排版空白与全半角差异，但保留代码、公式中的符号和大小写。"""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in normalized if not ch.isspace())


def _sample_text(text: str, limit: int = MAX_FINGERPRINT_CHARS) -> str:
    """长文档取首、中、尾三段，避免 20MB 文件做全量 n-gram 指纹。"""
    if len(text) <= limit:
        return text
    segment = limit // 3
    middle_start = max(0, len(text) // 2 - segment // 2)
    return (
        text[:segment]
        + text[middle_start:middle_start + segment]
        + text[-segment:]
    )


def _simhash64(text: str) -> int:
    normalized = _sample_text(text)
    if not normalized:
        return 0
    shingles = Counter(
        normalized[i:i + 3] for i in range(max(1, len(normalized) - 2))
    )
    vector = [0] * 64
    for shingle, weight in shingles.items():
        digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            vector[bit] += weight if value & (1 << bit) else -weight

    result = 0
    for bit, score in enumerate(vector):
        if score >= 0:
            result |= 1 << bit
    return result


def fingerprint_text(text: str) -> DocumentFingerprint:
    normalized = _normalize_text(text)
    return DocumentFingerprint(
        content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        simhash=_simhash64(normalized),
        length=len(normalized),
    )


def is_duplicate_fingerprint(
    left: DocumentFingerprint,
    right: DocumentFingerprint,
) -> bool:
    if left.content_hash == right.content_hash:
        return True
    if not left.length or not right.length:
        return False
    if min(left.length, right.length) < NEAR_DUPLICATE_MIN_CHARS:
        return False
    ratio = min(left.length, right.length) / max(left.length, right.length)
    if ratio < NEAR_DUPLICATE_MIN_LENGTH_RATIO:
        return False
    distance = (left.simhash ^ right.simhash).bit_count()
    return distance <= NEAR_DUPLICATE_HAMMING


def find_duplicate(
    current_id: int,
    fingerprint: DocumentFingerprint,
    candidates: Iterable[FingerprintedDocument],
) -> FingerprintedDocument | None:
    """返回同库中最先命中的重复文档；并发处理时让较早的文档优先入库。"""
    ordered = sorted(
        (
            doc
            for doc in candidates
            if doc.id != current_id
            and doc.status in ("ready", "processing")
            and doc.content_hash
            and doc.content_simhash is not None
            and doc.content_length > 0
        ),
        key=lambda doc: (doc.status != "ready", doc.id),
    )
    for doc in ordered:
        existing = DocumentFingerprint(
            content_hash=doc.content_hash,
            simhash=int(doc.content_simhash),
            length=doc.content_length,
        )
        if doc.status == "processing" and doc.id > current_id:
            continue
        if is_duplicate_fingerprint(fingerprint, existing):
            return doc
    return None
