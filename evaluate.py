"""RAG 检索质量离线评测。

不依赖在线 LLM，只评「检索层」：给定标注问答集，走 vector_store.search，
输出 recall@k / MRR / 拒答（门控）准确率，并把不同相似度阈值下的命中曲线画出来，
用于标定 UNMATCHED_SIMILARITY。

用法：
    python evaluate.py                 # 用 data/golden.json 的标注集跑全量
    python evaluate.py --thresholds     # 额外输出阈值扫描曲线

标注集格式（data/golden.json）：
    [
      {
        "question": "什么是死锁？",
        "course": "操作系统",          # 期望命中的课程（用于判断跨课误召回）
        "relevant_titles": ["死锁及其处理"],   # 期望命中的资料标题（召回 top-k 命中即算对）
        "expect_unmatched": false      # true = 期望拒答（知识库未收录）
      },
      ...
    ]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.config import settings  # noqa: E402
from backend.services.vector_store import search  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "data" / "golden.json"


def load_golden(path: Path | None = None) -> list[dict]:
    p = path or GOLDEN_PATH
    if not p.exists():
        print(f"未找到标注集：{p}，请先准备 data/golden.json")
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


def _hit_title(doc, titles: set[str]) -> bool:
    src = doc.metadata
    t = src.get("title") or src.get("filename") or ""
    return t in titles


def evaluate(golden: list[dict], top_k: int = 5) -> dict:
    """计算 recall@k / MRR / 拒答准确率。"""
    recall_hits = 0
    recall_total = 0
    mrr_sum = 0.0
    mrr_total = 0
    unmatched_correct = 0
    unmatched_total = 0
    cross_course = 0  # 命中了别的课程的段落

    for item in golden:
        q = item["question"]
        titles = set(item.get("relevant_titles", []))
        expect_unmatched = item.get("expect_unmatched", False)
        expect_course = item.get("course", "")

        docs = search(q, top_k=top_k)

        if expect_unmatched:
            # 期望拒答：top1 相似度应低于阈值（这里用「是否命中任何标注标题」近似判断）
            unmatched_total += 1
            if not docs or docs[0][1] < settings.UNMATCHED_SIMILARITY:
                unmatched_correct += 1
            continue

        # 期望命中
        recall_total += 1
        mrr_total += 1
        hit_rank = None
        for rank, (doc, _score) in enumerate(docs, start=1):
            if _hit_title(doc, titles):
                hit_rank = rank
                break
        if hit_rank is not None:
            recall_hits += 1
            mrr_sum += 1.0 / hit_rank

        # 跨课程：top1 落在别的课程即算一次跨课误召回
        if docs and expect_course:
            top_course = docs[0][0].metadata.get("course", "")
            if top_course and top_course != expect_course:
                cross_course += 1

    recall = recall_hits / recall_total if recall_total else 0.0
    mrr = mrr_sum / mrr_total if mrr_total else 0.0
    unmatched_acc = unmatched_correct / unmatched_total if unmatched_total else 0.0
    return {
        "total": len(golden),
        "recall_hits": recall_hits,
        "recall_total": recall_total,
        f"recall@{top_k}": round(recall, 4),
        "mrr": round(mrr, 4),
        "unmatched_correct": unmatched_correct,
        "unmatched_total": unmatched_total,
        "unmatched_acc": round(unmatched_acc, 4),
        "cross_course_misrecall": cross_course,
    }


def threshold_scan(golden: list[dict], top_k: int = 5) -> list[dict]:
    """扫描不同相似度阈值下的拒答准确率，用于标定 UNMATCHED_SIMILARITY。

    先对每个样本搜一次、缓存 top1 相似度，再在 0.30~0.80 以 0.02 步长扫描，
    避免每个阈值都重跑全量 search（9×N 次 embedding 很慢）。cosine 下「命中」普遍
    0.40~0.70，之前的 0.0~0.50 区间扫不到默认阈值 0.40 以上的部分，白扫。
    """
    # 预检索一次，缓存每个样本的 top1 相似度（以及是否期望拒答）
    cached: list[tuple[float, bool]] = []
    for item in golden:
        docs = search(item["question"], top_k=top_k)
        sim = docs[0][1] if docs else 0.0
        cached.append((sim, item.get("expect_unmatched", False)))

    rows = []
    threshold = 0.30
    while threshold <= 0.80 + 1e-9:
        tp = 0  # 期望拒答且正确拒答
        fn = 0  # 期望拒答但误放行
        fp = 0  # 期望命中但误拒答
        tn = 0  # 期望命中且放行
        for sim, expect_unmatched in cached:
            if expect_unmatched:
                if sim < threshold:
                    tp += 1
                else:
                    fn += 1
            else:
                if sim < threshold:
                    fp += 1
                else:
                    tn += 1
        acc = (tp + tn) / len(golden) if golden else 0.0
        rows.append(
            {
                "threshold": round(threshold, 2),
                "accuracy": round(acc, 4),
                "reject_caught": tp,
                "reject_missed": fn,
                "hit_false_reject": fp,
            }
        )
        threshold += 0.02
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索质量离线评测")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--thresholds", action="store_true", help="额外输出阈值扫描")
    parser.add_argument("--golden", type=str, default=None)
    args = parser.parse_args()

    golden = load_golden(Path(args.golden) if args.golden else None)
    result = evaluate(golden, top_k=args.top_k)

    print("=" * 60)
    print(f"RAG 检索质量评测（标注集 {result['total']} 条，top_k={args.top_k}）")
    print("=" * 60)
    print(f"recall@{args.top_k}:  {result[f'recall@{args.top_k}']}  "
          f"({result['recall_hits']}/{result['recall_total']})")
    print(f"MRR:               {result['mrr']}")
    print(f"拒答准确率:        {result['unmatched_acc']}  "
          f"({result['unmatched_correct']}/{result['unmatched_total']})")
    print(f"跨课程误召回:      {result['cross_course_misrecall']} 条")
    print()

    if args.thresholds:
        print("阈值扫描（相似度阈值 vs 综合准确率）：")
        print(f"{'threshold':>10} {'accuracy':>10} {'拒答抓到':>8} {'拒答漏放':>8} {'误拒':>6}")
        for row in threshold_scan(golden, top_k=args.top_k):
            print(
                f"{row['threshold']:>10} {row['accuracy']:>10} "
                f"{row['reject_caught']:>8} {row['reject_missed']:>8} {row['hit_false_reject']:>6}"
            )
        print(f"\n当前 UNMATCHED_SIMILARITY = {settings.UNMATCHED_SIMILARITY}")


if __name__ == "__main__":
    main()
