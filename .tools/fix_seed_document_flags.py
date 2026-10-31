# -*- coding: utf-8 -*-
"""一次性修正：把 seed_demo 生成的「展示壳」文档补标 is_seed=1（幂等，可重跑）。

背景：seed_demo.py 早期版本把文档的 is_seed 硬编码为 0，但这些文档的正文
已经通过 seed_points 以知识点形式写入向量库，文档记录只用于「文档管理」页展示，
不应再切分入库。is_seed=0 会让 rebuild_vectors.py / 回收站恢复误把它们当成
真文档重新解析入库，与知识点内容在向量库里重复命中。

识别规则：seed_demo 生成的壳文档，其 stored_path 文件名以 `demo-` 开头
（seed_demo.py 里 `path = UPLOAD_DIR / f"demo-{...}.txt"`）。真正上传入库的
文档 stored_path 是 uuid 格式，不受影响。

用法：
    python .tools/fix_seed_document_flags.py            # 预览将要修改的记录
    python .tools/fix_seed_document_flags.py --apply    # 实际写库
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402
from backend.models import Document  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="补标 seed_demo 壳文档的 is_seed=1")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只预览）")
    args = parser.parse_args()

    init_db()
    session = SessionLocal()
    try:
        # 只挑「ready 状态、is_seed=0、stored_path 以 demo- 开头」的壳文档
        candidates = list(
            session.scalars(
                select(Document)
                .where(Document.status == "ready")
                .where(Document.is_seed == 0)
                .order_by(Document.id)
            )
        )
        shells = [
            d
            for d in candidates
            if d.stored_path and Path(d.stored_path).name.startswith("demo-")
        ]

        if not shells:
            print("没有需要修正的记录（壳文档已全部补标，或库里没有 seed_demo 数据）。")
            return

        print(f"找到 {len(shells)} 份 seed_demo 壳文档需要补标 is_seed=1：")
        for d in shells:
            print(f"  id={d.id:>3}  {d.filename}  [{d.course}]")

        # 顺带检查是否有 uuid 命名的真文档被误判（理论上不会，但打印出来供人工核对）
        real = [d for d in candidates if d not in shells and d.stored_path]
        if real:
            print(f"\n以下 {len(real)} 份是真实文档（stored_path 非 demo- 前缀），不会改动：")
            for d in real:
                print(f"  id={d.id:>3}  {d.filename}")

        if not args.apply:
            print("\n[预览模式] 未写库。加 --apply 才会真正更新。")
            return

        for d in shells:
            d.is_seed = 1
        session.commit()
        print(f"\n已补标 {len(shells)} 份壳文档 is_seed=1，完成。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
