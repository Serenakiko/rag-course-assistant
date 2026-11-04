# 检索评测

> 本脚本只评「检索层 + 距离门控」，不调用对话模型。`ANSWERABILITY_CHECK`（可答性判定）需要真实 LLM，适合在应用层用人工标注或端到端测试单独评估。

## 数据集

标注集在 [`evaluation/golden.json`](../evaluation/golden.json)，共 40 条：

| 类别 | 数量 | 用途 |
| --- | --- | --- |
| `answerable` | 20 | 每个知识点一条标准问法，测 recall@k / MRR |
| `paraphrase` | 10 | 换个说法问同一知识点，测语义检索鲁棒性 |
| `unanswerable` | 6 | 完全无关问题，测拒答准确率 |
| `near_miss` | 4 | 资料相近但未直接覆盖，测“相近但不可答”的边界 |

字段：

```json
{
  "question": "TCP 为什么需要三次握手？",
  "course": "计算机网络",
  "relevant_titles": ["TCP 三次握手与四次挥手"],
  "expect_unmatched": false,
  "category": "answerable"
}
```

`relevant_titles` 使用知识点的 `title`，与向量 metadata 中的 `title` 对应。

## 指标

- **recall@k**：top-k 内命中任意 `relevant_titles` 的比例。
- **MRR**：第一个正确结果排名的倒数均值。
- **拒答准确率**：`expect_unmatched=true` 的问题中，top1 相似度低于阈值的比例。
- **误拒率**：可答问题中，top1 相似度低于阈值的比例。
- **跨课程误召回**：top1 命中的课程与期望课程不一致的条数。

## 运行

先确保 `.env` 已配置（至少 `JWT_SECRET`），并且知识库已入库：

```bash
ollama pull qwen3:8b
ollama pull bge-m3
python demo/seed_demo.py        # 或直接启动一次应用让 backend.seed 写入
```

跑评测：

```bash
python -m evaluation.evaluate
python -m evaluation.evaluate --top-k 3
python -m evaluation.evaluate --thresholds
python -m evaluation.evaluate --thresholds --json evaluation/last_run.json
```

## 论文里建议做的对比

1. **纯 LLM**：不检索，直接回答；统计编造率与拒答率。
2. **普通 RAG**：只做向量检索 + 生成，不开三重门控。
3. **完整系统**：距离门控 + 未收录标记 + 可答性判定。
4. **消融**：分别关闭距离门控、可答性判定，观察拒答准确率与误拒率。
5. **参数**：`top_k` 取 3 / 5 / 8，chunk size 取 500 / 800 / 1000。

建议把每次运行的 `--json` 结果保存到 `evaluation/results/`，论文表格直接引用。

## 结果记录（40 条标注集，bge-m3）

| 配置 | recall@k | MRR | 拒答准确率 | 误拒率 | 跨课程误召回 |
| --- | --- | --- | --- | --- | --- |
| 无门控（top5，threshold=0.0） | 1.000 | 1.000 | 0.000 | 0.000 | 0 |
| 旧默认（top5，0.40） | 1.000 | 1.000 | 0.000 | 0.000 | 0 |
| top3，0.40 | 1.000 | 1.000 | 0.000 | 0.000 | 0 |
| top8，0.40 | 1.000 | 1.000 | 0.000 | 0.000 | 0 |
| 保守推荐（top5，0.54） | 1.000 | 1.000 | 0.800 | 0.0333 | 0 |
| **当前默认（top5，0.56）** | **1.000** | **1.000** | **0.900** | **0.0667** | **0** |
| 实验最优（top5，0.58） | 1.000 | 1.000 | 1.000 | 0.0667 | 0 |

结论：

- 20 条标准问法和 10 条改写问法全部在 top1 命中，recall@3/5/8 都是 1.0，说明当前数据规模下检索不是瓶颈。
- 0.40 的旧默认会放行全部 10 条无关/相近未覆盖问题；提高到 0.56 后拒答 9/10，只误拒 2/30 条改写问法。
- 0.58 能把 10/10 全部拒掉，误拒率与 0.56 相同；但更接近部分可答问题的相似度，泛化风险略高，所以运行默认取 0.56。
- 0.56 下 6 条完全无关全部拒掉，4 条 near_miss 拒掉 3 条；剩下 1 条（TCP 慢启动，top1=0.5719）需要 `ANSWERABILITY_CHECK` 兜底。
- 这组结果说明：**距离门控负责拦明显无关，near_miss 仍应交给 ANSWERABILITY_CHECK**。论文里应同时报告两道门控的消融结果。

原始 JSON 在 `evaluation/results/`：
`default.json`（当前默认 0.56）、`top5_nogate.json`、`top5_thr040.json`、`top5_thr054.json`、`top5_thr056.json`、`top5_thr058.json`、`top3_thr040.json`、`top8_thr040.json`。

> 换 embedding 模型或增加课程资料后，先重新跑 `python -m evaluation.evaluate --thresholds`，再决定 `.env` 里的 `UNMATCHED_SIMILARITY`。
