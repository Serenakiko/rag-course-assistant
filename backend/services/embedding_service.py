"""向量化模型封装：跟随配置（Ollama 推荐 bge-m3 / OpenAI 兼容 API）。

除了选模型，这里还负责两件稳定性的事（P1-11）：
1. 分批：一段几千字的文档会被切成几十上百个片段，一次性丢给 Ollama/OpenAI 很容易
   超时或被网关截断，改成按 EMBED_BATCH_SIZE 逐批提交；
2. 重试：单批偶发 5xx / 连接重置时按指数退避重试，避免整份文档因为一次抖动
   直接入库失败（Chroma 的 add_documents 是全成全败语义）。
"""
from __future__ import annotations

import logging
import time

from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from ..config import settings

logger = logging.getLogger(__name__)


def get_embedding_model_name() -> str:
    """返回当前 embedding 模型的稳定标识（用于记录/检测模型切换）。"""
    provider = settings.EMBEDDING_PROVIDER or settings.LLM_PROVIDER
    if provider == "openai":
        return f"openai:{settings.OPENAI_EMBED_MODEL}"
    return f"ollama:{settings.OLLAMA_EMBED_MODEL}"


def _build_embeddings() -> Embeddings:
    """按 EMBEDDING_PROVIDER（缺省跟随 LLM_PROVIDER）构造底层向量化模型。"""
    provider = settings.EMBEDDING_PROVIDER or settings.LLM_PROVIDER
    if provider == "openai":
        return OpenAIEmbeddings(
            model=settings.OPENAI_EMBED_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE,
            chunk_size=settings.EMBED_BATCH_SIZE,
        )
    # ollama
    return OllamaEmbeddings(
        model=settings.OLLAMA_EMBED_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        keep_alive=settings.OLLAMA_EMBED_KEEP_ALIVE,
    )


class BatchedEmbeddings(Embeddings):
    """把 embed_documents 切成小批提交，失败按指数退避重试。

    只包 embed_documents / embed_query；检索侧的 embed_query 也走同一个重试逻辑，
    否则用户一次提问碰到 Ollama 抖动就直接看到「检索失败」。
    """

    def __init__(
        self,
        inner: Embeddings,
        *,
        batch_size: int,
        max_retries: int,
        label: str = "",
    ) -> None:
        self._inner = inner
        self._batch_size = max(1, int(batch_size))
        self._max_retries = max(0, int(max_retries))
        self._label = label or type(inner).__name__

    # ---- 内部：单次调用的重试 ----
    def _with_retry(self, fn, payload):
        delay = 0.5
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return fn(payload)
            except Exception as exc:  # noqa: BLE001  网络类异常形态多，统一重试
                last = exc
                if attempt >= self._max_retries:
                    break
                logger.warning(
                    "向量化失败（%s）第 %d/%d 次，%.1fs 后重试：%s",
                    self._label,
                    attempt + 1,
                    self._max_retries,
                    delay,
                    exc,
                )
                time.sleep(delay)
                delay = min(delay * 2, 4.0)
        assert last is not None
        raise last

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(self._with_retry(self._inner.embed_documents, batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._with_retry(self._inner.embed_query, text)


def get_embeddings() -> Embeddings:
    """返回带分批与重试的向量化模型（调用方无需关心底层是 Ollama 还是 OpenAI）。"""
    inner = _build_embeddings()
    return BatchedEmbeddings(
        inner,
        batch_size=settings.EMBED_BATCH_SIZE,
        max_retries=settings.EMBED_MAX_RETRIES,
        label=get_embedding_model_name(),
    )
