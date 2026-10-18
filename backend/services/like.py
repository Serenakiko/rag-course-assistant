"""LIKE 搜索的转义工具。

搜索框里输入的 % 和 _ 是普通字符，不是通配符。不转义的话搜「100%」等于把整表
捞出来，搜「os_lab」会连「osXlab」一起命中。所有 like("%关键词%") 都走这里，
别再各写各的 f"%{kw}%"。
"""
from __future__ import annotations

# 反斜杠作转义符：传给 SQL 就是 LIKE ... ESCAPE '\'
LIKE_ESCAPE = chr(92)


def escape_like(text: str) -> str:
    """转义 LIKE 通配符（% _ 以及转义符本身）。"""
    out = (text or "").replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    return out.replace("%", LIKE_ESCAPE + "%").replace("_", LIKE_ESCAPE + "_")


def contains(column, text: str):
    """column LIKE '%text%'，text 里的 % 和 _ 按普通字符处理。"""
    return column.like(f"%{escape_like(text)}%", escape=LIKE_ESCAPE)
