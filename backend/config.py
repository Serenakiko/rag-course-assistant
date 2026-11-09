"""全局配置：从项目根目录 .env 加载，统一管理路径与模型参数。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录 = backend 的上一级
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8")


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
    # 统计口径用的时区偏移（默认东八区），换部署环境时改这里
    UTC_OFFSET_HOURS: int = int(_get("UTC_OFFSET_HOURS", "8"))
    # 不提供默认值：缺失或仍是模板占位值时由 _check_secret 直接拒绝启动，
    # 避免用公开的默认密钥签发令牌（否则任何人都能伪造管理员 token）。
    JWT_SECRET: str = _get("JWT_SECRET", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = int(_get("JWT_EXPIRE_HOURS", "24"))
    # 可选：显式指定管理员首次创建密码。留空则生成随机初始密码并写入
    # data/admin_initial_credentials.txt；与公开默认密码相同也会被拒绝使用。
    DEFAULT_ADMIN_PASSWORD: str = _get("DEFAULT_ADMIN_PASSWORD", "")

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
    OLLAMA_LLM_MODEL: str = _get(
        "OLLAMA_LLM_MODEL", "qwen3:4b-instruct-2507-q4_K_M"
    )
    OLLAMA_EMBED_MODEL: str = _get("OLLAMA_EMBED_MODEL", "bge-m3")
    OLLAMA_TEMPERATURE: float = float(_get("OLLAMA_TEMPERATURE", "0.3"))
    # 0 = 向量化请求结束立即从显存/内存卸载。16 GB 机器上 bge-m3 若常驻，
    # 随后加载对话模型会因两个模型争内存而 OOM。
    OLLAMA_EMBED_KEEP_ALIVE: int = int(_get("OLLAMA_EMBED_KEEP_ALIVE", "0"))
    # 同一轮里的改写、可答性判定和生成复用同一个模型；下一次检索前显式卸载。
    OLLAMA_LLM_KEEP_ALIVE: int = int(_get("OLLAMA_LLM_KEEP_ALIVE", "30"))

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
    # 模型上下文窗口（token 总数，prompt + 输出共用）；Ollama num_ctx 取同一值。
    # 默认配合 4B Instruct，16 GB 内存可稳定加载 8192；超长时由 rag_service
    # 自动裁剪历史与检索段落。
    LLM_CTX: int = int(_get("LLM_CTX", "8192"))
    # 预留的输出 token 上限（对应 num_predict）；可用上下文 = LLM_CTX - LLM_MAX_TOKENS - 安全边际。
    LLM_MAX_TOKENS: int = int(_get("LLM_MAX_TOKENS", "1024"))
    # 中文按「1 字符 ≈ 1.5 token」粗估，取 1.6 留余量（纯汉字最多约 2 token/字）。
    CHAR_TO_TOKEN: float = 1.6
    # 知识库无匹配判定阈值（余弦相似度）：检索最相似资料与问题的相似度低于该值，
    # 判定知识库未覆盖该问题，直接拒绝回答并记录“未命中问题”。
    # 该值越小越宽松（放行越多）。40 条标注集实测：0.40 会放行全部无关问题；
    # 0.54 拒答 8/10、误拒 1/30；0.56 拒答 8/10、误拒 2/30；
    # 0.58 拒答 9/10、误拒 2/30。0.56 保留为较保守的默认值，
    # 剩余 near_miss 交给 ANSWERABILITY_CHECK。换模型或资料后需重新标定。
    UNMATCHED_SIMILARITY: float = float(_get("UNMATCHED_SIMILARITY", "0.56"))
    # 二次可答性判定：距离门控通过后，再让模型只回答"有/没有"，判断资料里是否直接讲了这个问题。
    # 防的是"资料讲二叉树、学生问红黑树"这类相近但未覆盖的情况；代价是每次提问多一次模型调用。
    # 默认开启，与 .env.example 保持一致：三重拒答是系统的核心设计，不能因为少建 .env 就退化成两道。
    ANSWERABILITY_CHECK: bool = _get("ANSWERABILITY_CHECK", "true").lower() in ("1", "true", "yes")
    # 可答性判定是独立模型调用，超时是系统错误，不应升级成“资料不可答”
    ANSWERABILITY_TIMEOUT: float = float(_get("ANSWERABILITY_TIMEOUT", "45"))
    # 只把最相关的有限证据交给短分类，避免长上下文让 CPU 推理拖过超时。
    ANSWERABILITY_MAX_CONTEXT_CHARS: int = int(
        _get("ANSWERABILITY_MAX_CONTEXT_CHARS", "1000")
    )
    # 多轮追问的检索 query 改写：把最近的历史轮拼接进 query，让「它」「这个」类指代能召回对的内容。
    QUERY_REWRITE_HISTORY_TURNS: int = 3  # 拼入检索的最近历史轮数
    # 引用资料相对裁剪：只保留与 top1 相似度之比不低于该值的资料（相对判据，跨模型稳定）。
    TRIM_RATIO: float = 0.85
    # 轻量混合重排：向量候选再用 BM25 词法分数重排，不引入额外模型或网络调用。
    RERANK_ENABLED: bool = _get("RERANK_ENABLED", "true").lower() in ("1", "true", "yes")
    RERANK_CANDIDATES: int = int(_get("RERANK_CANDIDATES", "12"))
    RERANK_VECTOR_WEIGHT: float = float(_get("RERANK_VECTOR_WEIGHT", "0.95"))
    RERANK_LEXICAL_WEIGHT: float = float(_get("RERANK_LEXICAL_WEIGHT", "0.05"))

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
