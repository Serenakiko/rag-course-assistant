"""全局配置：从项目根目录 .env 加载，统一管理路径与模型参数。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录 = backend 的上一级
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get(key: str, default: str = "") -> str:
    """读取环境变量，去首尾空白。"""
    value = os.getenv(key, default)
    return value.strip() if value else default


class Settings:
    """集中式配置对象，所有模块通过 settings.xxx 读取。"""

    # ---------- 应用与认证 ----------
    APP_NAME: str = "智答系统"
    APP_VERSION: str = "1.0.0"
    HOST: str = _get("HOST", "127.0.0.1")
    PORT: int = int(_get("PORT", "8000"))
    # 不提供默认值：缺失或仍是模板占位值时由 _check_secret 直接拒绝启动，
    # 避免用公开的默认密钥签发令牌（否则任何人都能伪造管理员 token）。
    JWT_SECRET: str = _get("JWT_SECRET", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = int(_get("JWT_EXPIRE_HOURS", "72"))

    # ---------- 数据目录 ----------
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    CHROMA_DIR: Path = DATA_DIR / "chroma"
    DB_PATH: Path = DATA_DIR / "app.db"

    # ---------- 大模型接入 ----------
    # ollama（本地） / openai（OpenAI 兼容 API）
    LLM_PROVIDER: str = _get("LLM_PROVIDER", "ollama").lower()

    # Ollama
    OLLAMA_BASE_URL: str = _get("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_LLM_MODEL: str = _get("OLLAMA_LLM_MODEL", "qwen2.5:3b")
    OLLAMA_EMBED_MODEL: str = _get("OLLAMA_EMBED_MODEL", "bge-m3")
    OLLAMA_TEMPERATURE: float = float(_get("OLLAMA_TEMPERATURE", "0.3"))

    # OpenAI 兼容 API
    OPENAI_API_BASE: str = _get("OPENAI_API_BASE", "https://api.deepseek.com/v1")
    OPENAI_API_KEY: str = _get("OPENAI_API_KEY", "")
    OPENAI_LLM_MODEL: str = _get("OPENAI_LLM_MODEL", "deepseek-chat")
    OPENAI_EMBED_MODEL: str = _get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    OPENAI_TEMPERATURE: float = float(_get("OPENAI_TEMPERATURE", "0.3"))

    # 向量化来源：留空则跟随 LLM_PROVIDER
    EMBEDDING_PROVIDER: str = _get("EMBEDDING_PROVIDER", "").lower()

    # ---------- RAG 参数 ----------
    CHUNK_SIZE: int = int(_get("CHUNK_SIZE", "800"))
    CHUNK_OVERLAP: int = int(_get("CHUNK_OVERLAP", "120"))
    TOP_K: int = int(_get("TOP_K", "5"))
    COLLECTION_NAME: str = _get("COLLECTION_NAME", "course_knowledge")

    # ---------- 对话 ----------
    HISTORY_LIMIT: int = 6  # 拼入提示词的历史轮数上限
    # 大模型单次回答的超时上限（秒）：超过即停止生成并返回错误提示，防止回答卡死
    LLM_TIMEOUT: float = float(_get("LLM_TIMEOUT", "180"))
    # 知识库无匹配判定阈值（L2 距离）：检索最相似资料的距离超过该值，判定知识库未覆盖该问题，
    # 直接拒绝回答并记录“未命中问题”。该值越小越严格（更多问题被判未覆盖）。
    UNMATCHED_DISTANCE: float = float(_get("UNMATCHED_DISTANCE", "0.85"))
    # 二次可答性判定：距离门控通过后，再让模型只回答"有/没有"，判断资料里是否直接讲了这个问题。
    # 防的是"资料讲二叉树、学生问红黑树"这类相近但未覆盖的情况；代价是每次提问多一次模型调用。
    ANSWERABILITY_CHECK: bool = _get("ANSWERABILITY_CHECK", "false").lower() in ("1", "true", "yes")

    # ---------- 系统角色 ----------
    ROLES: tuple = ("student", "admin")

    def ensure_dirs(self) -> None:
        """确保运行时目录存在。"""
        for d in (self.DATA_DIR, self.UPLOAD_DIR, self.CHROMA_DIR):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()


def _check_secret(secret: str) -> None:
    """JWT 密钥必须是够长的随机串，占位值一律拒绝启动。"""
    placeholder = secret.startswith("please-change-this-secret-key") or secret == "dev-secret-change-me"
    if placeholder or len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET 缺失、过短或仍是模板占位值，服务拒绝启动。\n"
            "生成随机密钥并写入 .env：\n"
            '  python -c "import secrets;print(secrets.token_hex(32))"\n'
            "然后填到 .env 的 JWT_SECRET= 后面，重启服务。"
        )


_check_secret(settings.JWT_SECRET)
