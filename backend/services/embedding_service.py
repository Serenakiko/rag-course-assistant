"""向量化模型封装：跟随配置（Ollama 推荐 bge-m3 / OpenAI 兼容 API）。"""
from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from ..config import settings


def get_embeddings() -> Embeddings:
    """按 EMBEDDING_PROVIDER（缺省跟随 LLM_PROVIDER）返回向量化模型。"""
    provider = settings.EMBEDDING_PROVIDER or settings.LLM_PROVIDER
    if provider == "openai":
        return OpenAIEmbeddings(
            model=settings.OPENAI_EMBED_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE,
        )
    # ollama
    return OllamaEmbeddings(
        model=settings.OLLAMA_EMBED_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
    )
