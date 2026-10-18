"""LLM 封装：Ollama 本地 / OpenAI 兼容 API 双模式。"""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from ..config import settings


def get_llm() -> BaseChatModel:
    """按配置返回流式对话模型。"""
    if settings.LLM_PROVIDER == "openai":
        return ChatOpenAI(
            model=settings.OPENAI_LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE,
            temperature=settings.OPENAI_TEMPERATURE,
            streaming=True,
            max_retries=1,
            timeout=120,
        )
    # 默认 ollama
    return ChatOllama(
        model=settings.OLLAMA_LLM_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=settings.OLLAMA_TEMPERATURE,
        streaming=True,
        num_predict=1024,
        # Ollama 默认窗口 4096，多轮对话加检索资料会把系统规则挤出去，这里给到 8192。
        num_ctx=8192,
        # qwen3.5 默认先思考再回答，长思考会把 num_predict 吃光导致正文为空
        reasoning=False,
        client_kwargs={"timeout": settings.LLM_TIMEOUT},
        async_client_kwargs={"timeout": settings.LLM_TIMEOUT},
    )
