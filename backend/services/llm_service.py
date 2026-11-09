"""LLM 封装：Ollama 本地 / OpenAI 兼容 API 双模式。"""
from __future__ import annotations

import json
import logging
from urllib.request import Request, urlopen

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from ..config import settings

logger = logging.getLogger(__name__)


def unload_ollama_llm() -> None:
    """请求 Ollama 立即卸载对话模型，避免与下一次 embedding 争内存。"""
    if settings.LLM_PROVIDER != "ollama":
        return
    payload = json.dumps(
        {
            "model": settings.OLLAMA_LLM_MODEL,
            "prompt": "",
            "keep_alive": 0,
            "stream": False,
        }
    ).encode("utf-8")
    request = Request(
        settings.OLLAMA_BASE_URL.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            response.read()
    except Exception:  # noqa: BLE001
        logger.warning("请求 Ollama 卸载对话模型失败", exc_info=True)


def get_llm(*, num_predict: int | None = None, streaming: bool = True) -> BaseChatModel:
    """按配置返回对话模型。

    num_predict / streaming 供短任务覆盖：可答性判定只需要输出“有/没有”，
    不应该继承正常回答的 1024-token 流式预算。
    """
    if settings.LLM_PROVIDER == "openai":
        return ChatOpenAI(
            model=settings.OPENAI_LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE,
            temperature=settings.OPENAI_TEMPERATURE,
            streaming=streaming,
            max_retries=1,
            timeout=120,
            **({"max_tokens": num_predict} if num_predict is not None else {}),
        )
    # 默认 ollama
    return ChatOllama(
        model=settings.OLLAMA_LLM_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=settings.OLLAMA_TEMPERATURE,
        streaming=streaming,
        num_predict=num_predict if num_predict is not None else settings.LLM_MAX_TOKENS,
        # Ollama 默认窗口 4096，多轮对话加检索资料会把系统规则挤出去。
        # 这里用配置的 LLM_CTX（默认 8192）；真正的超窗防护在 rag_service 按 token 预算做，
        # 超长先裁历史再裁段落，避免 Ollama 从头截断丢掉系统规则。
        num_ctx=settings.LLM_CTX,
        # qwen3 系列默认先思考再回答，长思考会把 num_predict 吃光导致正文为空
        reasoning=False,
        keep_alive=settings.OLLAMA_LLM_KEEP_ALIVE,
        client_kwargs={"timeout": settings.LLM_TIMEOUT},
        async_client_kwargs={"timeout": settings.LLM_TIMEOUT},
    )
