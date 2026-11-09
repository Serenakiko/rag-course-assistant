"""文档解析：支持 PDF / TXT / Markdown / CSV。"""
from __future__ import annotations

import io
from pathlib import Path

SUPPORTED_EXTS: dict[str, str] = {
    ".pdf": "pdf",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".csv": "csv",
}


class ParseError(Exception):
    """文档解析失败。"""


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTS


def parse_file(path: Path) -> str:
    """解析文件为纯文本，返回全文。"""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ParseError(f"不支持的文件类型 {ext}，仅支持：PDF / TXT / Markdown / CSV")

    try:
        if ext in (".txt", ".md", ".markdown"):
            return _read_text(path)
        if ext == ".pdf":
            return _parse_pdf(path)
        if ext == ".csv":
            return _parse_csv(path)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"文件读取失败：{exc}") from exc
    raise ParseError("未知解析错误")


def _read_text(path: Path) -> str:
    """优先 UTF-8，兼容 Windows 中文环境常见的 GB18030 文本。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("文本编码无法识别，请保存为 UTF-8 或 GB18030")


def _parse_pdf(path: Path) -> str:
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover
        raise ParseError("缺少 pypdf 依赖，请执行 pip install pypdf") from exc

    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"PDF 解析失败（可能已加密或损坏）：{exc}") from exc

    pages: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text.strip():
            pages.append(f"【第 {i} 页】\n{text.strip()}")
    if not pages:
        raise ParseError("未从 PDF 中提取到文本（可能是扫描件，需要 OCR）")
    return "\n\n".join(pages)


def _parse_csv(path: Path) -> str:
    import csv

    rows: list[str] = []
    text = _read_text(path)
    with io.StringIO(text, newline="") as f:
        for row in csv.reader(f):
            rows.append(" | ".join(cell.strip() for cell in row))
    if not rows:
        raise ParseError("CSV 文件为空")
    return "\n".join(rows)
