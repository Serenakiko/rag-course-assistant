"""把种子知识库的 20 个知识点同步为「文档管理」中的文档记录（幂等，可重复运行）。

背景：知识库（4 门课程）各有 5 个知识点（共 20 个），但 documents 表中没有对应记录，
导致「知识库管理」里的文档数与「文档管理」页不一致。本脚本为每个知识点生成一条文档：

  - filename  = {知识点标题}.md
  - file_type = md
  - status    = ready（已就绪）
  - chunk_count = 按真实分块逻辑（split_text）计算
  - course    = 所属知识库名称
  - 同时把知识点内容写为真实的源文件（data/uploads/xxx.md）

说明：不重复写入向量库——知识点已向量化并参与 RAG 检索，再按文档方式入库会造成
同一内容在检索结果中重复出现；文档记录仅用于「文档管理」展示与文件管理。

用法：
    python sync_seed_documents.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.models import Document, User  # noqa: E402
from backend.seed import SAMPLE_POINTS  # noqa: E402
from backend.services.chunker import split_text  # noqa: E402


def main() -> None:
    init_db()
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    session = SessionLocal()
    try:
        admin = session.scalar(select(User).where(User.username == "23040107"))
        if admin is None:
            print("未找到默认管理员账号，请先启动后端完成初始化")
            return

        created = skipped = 0
        for item in SAMPLE_POINTS:
            filename = f"{item['title']}.md"
            if session.scalar(select(Document).where(Document.filename == filename)):
                skipped += 1
                continue
            text = f"# {item['title']}\n\n{item['content']}"
            chunks = split_text(text)
            stored = settings.UPLOAD_DIR / f"{uuid.uuid4().hex}.md"
            stored.write_text(text, encoding="utf-8")
            session.add(
                Document(
                    filename=filename,
                    file_type="md",
                    file_size=len(text.encode("utf-8")),
                    stored_path=str(stored),
                    status="ready",
                    course=item["course"],
                    chunk_count=len(chunks),
                    uploaded_by=admin.id,
                    is_seed=1,
                )
            )
            created += 1
        session.commit()
        print(f"完成：新增 {created} 个文档，跳过 {skipped} 个已存在的同名文档")
    finally:
        session.close()


if __name__ == "__main__":
    main()
