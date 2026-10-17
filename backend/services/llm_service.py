"""LLM 封装：Ollama 本地 / OpenAI 兼容 API 双模式。"""
from __future__ import annotations

import httpx
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


def check_llm_connection() -> tuple[bool, str]:
    """探测当前大模型是否可用，返回 (是否可用, 提示信息)。"""
    if settings.LLM_PROVIDER == "openai":
        if not settings.OPENAI_API_KEY:
            return False, "未配置 OPENAI_API_KEY，请在 .env 中填写后重启服务"
        # 轻量探测：请求模型列表接口
        try:
            url = settings.OPENAI_API_BASE.rstrip("/") + "/models"
            resp = httpx.get(
                url, headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "OpenAI 兼容 API 连接正常"
            return False, f"API 返回异常状态码 {resp.status_code}，请检查 BASE/KEY/模型权限"
        except Exception as exc:  # noqa: BLE001
            return False, f"无法连接 API：{exc}"
    # ollama
    try:
        url = settings.OLLAMA_BASE_URL.rstrip("/") + "/api/tags"
        resp = httpx.get(url, timeout=10)
        if resp.status_code != 200:
            return False, f"Ollama 服务异常（HTTP {resp.status_code}），请确认已启动"
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        wanted = settings.OLLAMA_LLM_MODEL
        if wanted not in models:
            hint = "、".join(models[:8]) if models else "（暂无模型）"
            return (
                False,
                f"Ollama 中未找到模型 {wanted}，当前已有：{hint}。"
                f"请运行：ollama pull {wanted}",
            )
        return True, "Ollama 连接正常"
    except httpx.ConnectError:
        return False, "无法连接 Ollama（11434），请先启动 Ollama 并拉取模型"
    except Exception as exc:  # noqa: BLE001
        return False, f"Ollama 探测失败：{exc}"
