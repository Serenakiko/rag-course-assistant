"""后端包：在任何第三方 HTTP 客户端导入前清理失效的 TLS 环境配置。"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _drop_invalid_tls_env() -> None:
    """移除指向不存在路径的证书变量，避免 httpx/ollama 在导入时崩溃。"""
    cert_file = os.environ.get("SSL_CERT_FILE", "")
    if cert_file and not Path(cert_file).is_file():
        logger.warning("忽略失效的 SSL_CERT_FILE：%s", cert_file)
        os.environ.pop("SSL_CERT_FILE", None)

    cert_dir = os.environ.get("SSL_CERT_DIR", "")
    if cert_dir and not Path(cert_dir).is_dir():
        logger.warning("忽略失效的 SSL_CERT_DIR：%s", cert_dir)
        os.environ.pop("SSL_CERT_DIR", None)


_drop_invalid_tls_env()
