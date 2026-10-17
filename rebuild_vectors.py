"""向量库重建脚本：清理损坏的 Chroma 数据，并从数据库重新入库文档与知识点。

用法（先停止后端服务再运行）：
    python rebuild_vectors.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.database import SessionLocal  # noqa: E402
from backend.models import Document, KnowledgePoint  # noqa: E402
from backend.services.chunker import split_text  # noqa: E402
from backend.services.document_parser import parse_file  # noqa: E402
from backend.services.vector_store import (  # noqa: E402
    add_document_chunks,
    add_knowledge_point,
)


def main() -> None:
    # 1. 清空旧的向量库目录（索引损坏时强制重建）
    if settings.CHROMA_DIR.exists():
        shutil.rmtree(settings.CHROMA_DIR)
    settings.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"已清理向量库目录：{settings.CHROMA_DIR}")

    session = SessionLocal()
    try:
        # 2. 重新入库全部知识点
        points = list(session.scalars(select(KnowledgePoint).order_by(KnowledgePoint.id)))
        for p in points:
            add_knowledge_point(p.id, p.title, p.content, p.course, p.tags)
        print(f"知识点入库：{len(points)} 条")

        # 3. 重建文档向量（ready 的算重来，failed 的顺手重试一次）
        docs = list(
            session.scalars(
                select(Document)
                .where(Document.status.in_(("ready", "failed")))
                # 种子展示文档的内容已作为知识点入过库，再入一次会在检索里重复
                .where(Document.is_seed == 0)
                .order_by(Document.id)
            )
        )
        ok_docs = 0
        for d in docs:
            try:
                text = parse_file(Path(d.stored_path))
                chunks = split_text(text)
                if not chunks:
                    raise ValueError("没有提取到有效文本")
                # course 必须带上，否则分块丢失库归属，学生按库检索不到
                count = add_document_chunks(d.id, d.filename, d.course or "", chunks)
                d.status = "ready"
                d.chunk_count = count
                d.error = ""
                ok_docs += 1
                print(f"文档入库：{d.filename}（{count} 块，{d.course or '未分类'}）")
            except Exception as exc:  # noqa: BLE001
                d.status = "failed"
                d.error = str(exc)[:500]
                print(f"文档失败：{d.filename} → {exc}")
        session.commit()
        print(f"文档处理：{ok_docs}/{len(docs)} 个成功")
    finally:
        session.close()

    print("向量库重建完成。")


if __name__ == "__main__":
    main()
