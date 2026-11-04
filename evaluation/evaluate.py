"""RAG 检索质量离线评测。

只评检索层，不依赖在线 LLM：给定标注问答集，走 vector_store.search，
输出 recall@k、MRR、拒答准确率、误拒率和跨课程误召回，并支持阈值扫描。
用于论文中的检索实验与 UNMATCHED_SIMILARITY 标定。

用法：
    python -m evaluation.evaluate
    python -m evaluation.evaluate --top-k 3
    python -m evaluation.evaluate --thresholds
    python -m evaluation.evaluate --json evaluation/last_run.json

标注集格式见 evaluation/golden.json：
    question          问题
    course            期望命中的课程（用于判断跨课程误召回，可为空）
    relevant_titles   期望命中的资料标题（top-k 内命中任意一个即算召回）
    expect_unmatched  true = 期望拒答（知识库未收录）
    category          answerable / paraphrase / unanswerable / near_miss
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.vector_store import search  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.json"


def load_golden(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or GOLDEN_PATH
    if not p.exists():
        print(f"未找到标注集：{p}", file=sys.stderr)
        raise SystemExit(1)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        print(f"标注集为空或格式不正确：{p}", file=sys.stderr)
        raise SystemExit(1)
    return data


def _hit_title(doc, titles: set[str]) -> bool:
    src = doc.metadata
    title = src.get("title") or src.get("filename") or ""
    return title in titles


def _top_similarity(docs: list[tuple[Any, float]]) -> float:
    return float(docs[0][1]) if docs else 0.0


def evaluate(
    golden: list[dict[str, Any]],
    top_k: int = 5,
    threshold: float | None = None,
) -> dict[str, Any]:
    """跑一遍检索，返回汇总指标和逐题明细。"""
    threshold = settings.UNMATCHED_SIMILARITY if threshold is None else threshold

    answerable_total = 0
    recall_hits = 0
    mrr_sum = 0.0
    false_reject = 0
    cross_course = 0
    unmatched_total = 0
    refusal_correct = 0
    by_category: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "total": 0,
            "recall_hits": 0,
            "mrr_sum": 0.0,
            "false_reject": 0,
            "refusal_correct": 0,
        }
    )
    details: list[dict[str, Any]] = []

    for item in golden:
        question = item["question"]
        titles = set(item.get("relevant_titles") or [])
        expect_unmatched = bool(item.get("expect_unmatched"))
        category = item.get("category") or ("unanswerable" if expect_unmatched else "answerable")
        docs = search(question, top_k=top_k)
        top_sim = _top_similarity(docs)
        stats = by_category[category]
        stats["total"] += 1

        if expect_unmatched:
            unmatched_total += 1
            refused = top_sim < threshold
            if refused:
                refusal_correct += 1
                stats["refusal_correct"] += 1
            details.append(
                {
                    "question": question,
                    "category": category,
                    "expect_unmatched": True,
                    "top_similarity": round(top_sim, 4),
                    "refused": refused,
                    "top_title": docs[0][0].metadata.get("title") if docs else "",
                }
            )
            continue

        answerable_total += 1
        hit_rank = None
        for rank, (doc, _score) in enumerate(docs, start=1):
            if _hit_title(doc, titles):
                hit_rank = rank
                break
        if hit_rank is not None:
            recall_hits += 1
            mrr_sum += 1.0 / hit_rank
            stats["recall_hits"] += 1
            stats["mrr_sum"] += 1.0 / hit_rank

        rejected = top_sim < threshold
        if rejected:
            false_reject += 1
            stats["false_reject"] += 1

        top_course = docs[0][0].metadata.get("course", "") if docs else ""
        expected_course = item.get("course") or ""
        if expected_course and top_course and top_course != expected_course:
            cross_course += 1

        details.append(
            {
                "question": question,
                "category": category,
                "expect_unmatched": False,
                "top_similarity": round(top_sim, 4),
                "hit_rank": hit_rank,
                "refused": rejected,
                "top_title": docs[0][0].metadata.get("title") if docs else "",
                "top_course": top_course,
            }
        )

    recall = recall_hits / answerable_total if answerable_total else 0.0
    mrr = mrr_sum / answerable_total if answerable_total else 0.0
    refusal_accuracy = refusal_correct / unmatched_total if unmatched_total else 0.0
    false_reject_rate = false_reject / answerable_total if answerable_total else 0.0

    category_summary: dict[str, dict[str, float]] = {}
    for name, s in by_category.items():
        total = int(s["total"])
        category_summary[name] = {
            "total": total,
            "recall@k": round(s["recall_hits"] / total, 4) if total else 0.0,
            "mrr": round(s["mrr_sum"] / total, 4) if total else 0.0,
            "false_reject_rate": round(s["false_reject"] / total, 4) if total else 0.0,
            "refusal_accuracy": round(s["refusal_correct"] / total, 4) if total else 0.0,
        }

    return {
        "total": len(golden),
        f"recall@{top_k}": round(recall, 4),
        "mrr": round(mrr, 4),
        "refusal_accuracy": round(refusal_accuracy, 4),
        "false_reject_rate": round(false_reject_rate, 4),
        "cross_course_misrecall": cross_course,
        "threshold": threshold,
        "top_k": top_k,
        "answerable_total": answerable_total,
        "unmatched_total": unmatched_total,
        "by_category": category_summary,
        "details": details,
    }


def threshold_scan(
    golden: list[dict[str, Any]], top_k: int = 5
) -> list[dict[str, float]]:
    """缓存每题 top1 相似度，再扫描 0.30~0.80 的拒答阈值。"""
    cached: list[tuple[float, bool]] = []
    for item in golden:
        docs = search(item["question"], top_k=top_k)
        cached.append((_top_similarity(docs), bool(item.get("expect_unmatched"))))

    rows: list[dict[str, float]] = []
    threshold = 0.30
    while threshold <= 0.80 + 1e-9:
        reject_caught = sum(1 for sim, expect in cached if expect and sim < threshold)
        reject_missed = sum(1 for sim, expect in cached if expect and sim >= threshold)
        hit_false_reject = sum(1 for sim, expect in cached if not expect and sim < threshold)
        accuracy = sum(1 for sim, expect in cached if (sim < threshold) == expect) / len(cached)
        rows.append(
            {
                "threshold": round(threshold, 2),
                "accuracy": round(accuracy, 4),
                "reject_caught": reject_caught,
                "reject_missed": reject_missed,
                "hit_false_reject": hit_false_reject,
            }
        )
        threshold += 0.02
    return rows


def _print_report(result: dict[str, Any], top_k: int, threshold: float) -> None:
    print("=" * 66)
    print(f"RAG 检索评测：{result['total']} 条，top_k={top_k}，阈值={threshold:.2f}")
    print("=" * 66)
    print(f"recall@{top_k}:       {result[f'recall@{top_k}']:.4f}")
    print(f"MRR:                {result['mrr']:.4f}")
    print(f"拒答准确率:          {result['refusal_accuracy']:.4f}")
    print(f"可答问题误拒率:      {result['false_reject_rate']:.4f}")
    print(f"跨课程误召回:        {result['cross_course_misrecall']} 条")
    print()
    print("按类别：")
    print(f"{'category':<14}{'total':>6}{'recall':>10}{'mrr':>10}{'误拒率':>10}{'拒答准确率':>12}")
    for name, s in result["by_category"].items():
        print(
            f"{name:<14}{s['total']:>6}{s['recall@k']:>10.4f}{s['mrr']:>10.4f}"
            f"{s['false_reject_rate']:>10.4f}{s['refusal_accuracy']:>12.4f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索质量离线评测")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--thresholds", action="store_true", help="额外输出阈值扫描")
    parser.add_argument("--golden", type=str, default=None)
    parser.add_argument("--json", type=str, default=None, help="把完整结果写入 JSON")
    args = parser.parse_args()

    golden = load_golden(Path(args.golden) if args.golden else None)
    result = evaluate(golden, top_k=args.top_k, threshold=args.threshold)
    _print_report(result, args.top_k, float(result["threshold"]))

    if args.thresholds:
        print()
        print("阈值扫描（相似度阈值 vs 拒答综合准确率）：")
        print(f"{'threshold':>10}{'accuracy':>10}{'拒答抓到':>10}{'拒答漏放':>10}{'误拒':>8}")
        for row in threshold_scan(golden, top_k=args.top_k):
            print(
                f"{row['threshold']:>10.2f}{row['accuracy']:>10.4f}"
                f"{row['reject_caught']:>10}{row['reject_missed']:>10}{row['hit_false_reject']:>8}"
            )
        print(f"\n当前 UNMATCHED_SIMILARITY = {settings.UNMATCHED_SIMILARITY}")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整结果已写入 {out}")


if __name__ == "__main__":
    main()
