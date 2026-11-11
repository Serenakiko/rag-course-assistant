"""向量库重建脚本：清空 Chroma 集合内容并重新入库文档与知识点。

升级后旧向量缺少 knowledge_base_id、页码或章节 metadata 时，也可运行本脚本
一次性回填；新版上传与重建会自动带这些字段。

与旧版不同，本脚本不再删除 data/chroma 目录，而是复用已有数据库、逐个删除
集合后重建。这样在“新建 Chroma 持久库失败”的机器上也能完成重建（目录一旦
删除就无法重新初始化），并在删除前自动保留一份目录备份。

用法（先停止后端服务再运行）：
    python .tools/rebuild_vectors.py
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.database import SessionLocal  # noqa: E402
from backend.models import Document, KnowledgePoint  # noqa: E402
from backend.services.chunker import split_text  # noqa: E402
from backend.services.document_parser import parse_file  # noqa: E402
from backend.services import vector_store  # noqa: E402
from backend.services.vector_store import (  # noqa: E402
    add_document_chunks,
    add_knowledge_point,
)


def _reset_collections() -> None:
    """复用已有数据库，删除全部集合（不删除 data/chroma 目录）。"""
    client = vector_store._get_client()  # noqa: SLF001
    for collection in client.list_collections():
        client.delete_collection(collection.name)
    # 集合已删，缓存的 Chroma 包装对象必须失效，后续才能重新建集合
    vector_store._vector_store = None  # noqa: SLF001


def main() -> None:
    # 1. 先备份，再清空集合。绝不 rmtree：新建持久库失败的机器上删除目录后无法恢复。
    if settings.CHROMA_DIR.exists():
        backup = settings.CHROMA_DIR.with_name(
            f"{settings.CHROMA_DIR.name}_backup_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copytree(settings.CHROMA_DIR, backup)
        print(f"已备份向量库目录：{backup}")
    else:
        settings.CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        _reset_collections()
    except Exception as exc:  # noqa: BLE001
        print(f"清空向量集合失败：{exc}")
        print("未删除原目录；如需恢复，直接使用上面的备份目录替换 data/chroma。")
        raise SystemExit(1) from exc
    print(f"已清空向量集合：{settings.CHROMA_DIR}")

    session = SessionLocal()
    try:
        # 2. 重新入库知识点（按 is_auto 精确跳过文档自动生成的那批）
        # 不再靠标题猜：与文档同名的手工知识点也要正常入库。
        points = list(
            session.scalars(
                select(KnowledgePoint)
                .where(KnowledgePoint.is_auto == 0)
                .order_by(KnowledgePoint.id)
            )
        )
        for p in points:
            add_knowledge_point(p.id, p.title, p.content, p.course, p.tags)
        print(f"知识点入库：{len(points)} 条（已跳过文档自动生成的知识点）")

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
