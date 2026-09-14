"""文本分块：递归字符分割，兼顾中文标点。"""
from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config import settings


def split_text(text: str) -> list[str]:
    """将长文本切分为带重叠的块。"""
    if not text or not text.strip():
        return []
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
