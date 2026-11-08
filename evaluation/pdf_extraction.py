"""真实 PDF 页码抽取质量评估。

评估每份 PDF 的页数、可提取文本页覆盖率、短页比例，以及分块后
能定位到页码的 chunk 比例。没有 PDF 样本时明确输出 no_samples。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.chunker import split_text  # noqa: E402
from backend.services.document_parser import ParseError, parse_file  # noqa: E402
from backend.services.vector_store import _chunk_locator  # noqa: E402

PAGE_MARKER = re.compile(r"【第\s*(\d+)\s*页】")


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def summarize_pages(page_texts: list[str]) -> dict[str, Any]:
    page_count = len(page_texts)
    if not page_count:
        return {
            "status": "no_samples",
            "page_count": 0,
            "nonempty_pages": 0,
            "marker_coverage": 0.0,
            "short_page_ratio": 0.0,
            "avg_chars_per_page": 0.0,
        }
    stripped = [text.strip() for text in page_texts]
    nonempty = sum(bool(text) for text in stripped)
    short_pages = sum(len(text) < 40 for text in stripped)
    total_chars = sum(len(text) for text in stripped)
    coverage = _ratio(nonempty, page_count)
    return {
        "status": "ok" if coverage >= 0.8 else "degraded",
        "page_count": page_count,
        "nonempty_pages": nonempty,
        "marker_coverage": coverage,
        "short_pages": short_pages,
        "short_page_ratio": _ratio(short_pages, page_count),
        "total_chars": total_chars,
        "avg_chars_per_page": round(total_chars / page_count, 1),
    }


def evaluate_pdf(path: Path) -> dict[str, Any]:
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover
        raise ParseError("缺少 pypdf 依赖") from exc

    reader = pypdf.PdfReader(str(path))
    raw_pages: list[str] = []
    for page in reader.pages:
        try:
            raw_pages.append(page.extract_text() or "")
        except Exception:
            raw_pages.append("")

    summary = summarize_pages(raw_pages)
    text = parse_file(path)
    marker_pages = sorted({int(value) for value in PAGE_MARKER.findall(text)})
    chunks = split_text(text)
    locatable_chunks = sum(bool(_chunk_locator(chunk)[0]) for chunk in chunks)
    summary.update(
        {
            "filename": path.name,
            "marker_pages": len(marker_pages),
            "marker_page_coverage": _ratio(len(marker_pages), len(raw_pages)),
            "chunks": len(chunks),
            "locatable_chunks": locatable_chunks,
            "chunk_page_coverage": _ratio(locatable_chunks, len(chunks)),
        }
    )
    return summary


def evaluate_directory(path: Path) -> dict[str, Any]:
    files = [path] if path.is_file() else sorted(path.rglob("*.pdf"))
    pdfs = [file for file in files if file.suffix.lower() == ".pdf"]
    if not pdfs:
        return {
            "status": "no_samples",
            "sample_count": 0,
            "message": f"{path} 下没有 PDF 样本",
            "files": [],
        }

    results = []
    for pdf in pdfs:
        try:
            results.append(evaluate_pdf(pdf))
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "filename": pdf.name,
                    "status": "failed",
                    "error": str(exc)[:500],
                }
            )

    page_count = sum(item.get("page_count", 0) for item in results)
    nonempty_pages = sum(item.get("nonempty_pages", 0) for item in results)
    chunks = sum(item.get("chunks", 0) for item in results)
    locatable = sum(item.get("locatable_chunks", 0) for item in results)
    return {
        "status": "ok" if all(item.get("status") == "ok" for item in results) else "degraded",
        "sample_count": len(results),
        "page_count": page_count,
        "page_text_coverage": _ratio(nonempty_pages, page_count),
        "chunk_page_coverage": _ratio(locatable, chunks),
        "files": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="真实 PDF 页码抽取质量评估")
    parser.add_argument("--path", default=str(ROOT / "data" / "uploads"))
    parser.add_argument("--json", default=str(ROOT / "evaluation" / "results" / "pdf_extraction.json"))
    args = parser.parse_args()

    result = evaluate_directory(Path(args.path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    output = Path(args.json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {output}")


if __name__ == "__main__":
    main()
