"""文本分块：递归字符分割，兼顾中文标点。"""
from __future__ import annotations

from ..config import settings


def split_text(text: str) -> list[str]:
    """将长文本切分为带重叠的块。"""
    if not text or not text.strip():
        return []

    # 延迟导入，避免启动时拉起 transformers 白等 20 秒
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
        # keep_separator="end"：分隔符（句号/问号/换行）留在上一块末尾。
        # 旧值 False 会把标点丢掉，切出来的「……的配置方法」直接接下一块，
        # 既丢了语气也丢了句子边界，检索时同一个问题的不同切法对不上。
        keep_separator="end",
    )
    return splitter.split_text(text)
