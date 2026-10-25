"""文本分块：递归字符分割，兼顾中文标点。"""
from __future__ import annotations

from ..config import settings


def split_text(text: str) -> list[str]:
    """将长文本切分为带重叠的块。"""
    if not text or not text.strip():
        return []

    # 延迟导入：langchain_text_splitters 的 __init__/base 会顺手 import transformers
    # （可选依赖，用 try/except 兜住）。机器上装了 torch 那套的话，这一下就是 20 秒，
    # 而它只在真正解析文档时才需要——放在模块顶层等于每次启动都白等。
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=[
            "\n\n",
            "\n",
            "。",
            "！",
            "？",
            "；",
            ". ",
            "! ",
            "? ",
            " ",
            "",
        ],
        keep_separator=False,
    )
    return splitter.split_text(text)
