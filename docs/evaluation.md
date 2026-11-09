# 检索评测

> 本脚本只评「检索层 + 距离门控」，不调用对话模型。`ANSWERABILITY_CHECK` 需要真实 LLM，应在应用层用人工标注或端到端测试单独评估。

## 两套评测口径

加入文档分块后，同一问题的正确知识点可能排在第 2 位。若仍把知识点和文档分块混在一起按标题算 MRR，分数会被语料规模和分块策略影响，不能代表知识点库本身的质量。

| 作用域 | `--scope` | 检索范围 | 用途 |
| --- | --- | --- | --- |
| 知识点库评测 | `knowledge_points` | 只检索 `source_type=knowledge_point` | 论文主表、知识点召回质量 |
| 完整混合语料评测 | `full` | 同时检索知识点和文档分块 | 真实问答语料下的端到端检索压力 |

两套结果必须分别报告，不能用一个数字混称“RAG 检索准确率”。

## 标注字段

`evaluation/golden.json` 共 40 条：

| 类别 | 数量 | 用途 |
| --- | --- | --- |
| `answerable` | 20 | 每个知识点一条标准问法 |
| `paraphrase` | 10 | 换个说法问同一知识点 |
| `unanswerable` | 6 | 完全无关问题 |
| `near_miss` | 4 | 资料相近但未直接覆盖 |

另有 `evaluation/student_questions.json`，共 21 条去重后的真实学生提问，字段包含 `source_message_id` 和对应的知识点 ID。该集合没有负样本，因此只用于观察真实问法下的 recall/MRR/误拒率，不单独解释拒答准确率。

推荐使用稳定 ID 标注：

```json
{
  "question": "进程和线程有什么区别？",
  "course": "操作系统",
  "relevant_knowledge_base_ids": ["kb_..."],
  "relevant_point_ids": ["11"],
  "relevant_document_ids": ["42"],
  "relevant_chunk_ids": ["42:0"],
  "relevant_titles": ["进程与线程"],
  "expect_unmatched": false,
  "category": "answerable"
}
```

匹配顺序为知识库 ID、文档 ID、chunk ID、知识点 ID；旧标注仍可用 `relevant_titles` 回退。

## 指标

- **recall@k**：top-k 内命中任一期望资料的比例。
- **MRR**：第一个正确结果的排名倒数均值。
- **拒答准确率**：`expect_unmatched=true` 的问题中，top1 相似度低于阈值的比例。
- **误拒率**：可答问题中，top1 相似度低于阈值的比例。
- **跨课程误召回**：top1 命中的课程与期望课程不一致的条数。

## 运行

确保 `.env` 已配置，知识库和文档已入库：

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama pull bge-m3
python demo/seed_demo.py
```

分别运行两套口径：

```bash
python -m evaluation.evaluate --scope knowledge_points
python -m evaluation.evaluate --scope full
python -m evaluation.evaluate --scope knowledge_points --thresholds
python -m evaluation.evaluate --scope full --thresholds
python -m evaluation.evaluate --scope knowledge_points \
  --golden evaluation/student_questions.json
python -m evaluation.evaluate --scope full \
  --golden evaluation/student_questions.json
python -m evaluation.evaluate --scope full --json evaluation/results/full_corpus.json
```

## 当前结果

环境：`bge-m3`，`top_k=5`，`UNMATCHED_SIMILARITY=0.56`，混合 reranker 权重 `0.95/0.05`。

| 作用域 | recall@5 | MRR | 拒答准确率 | 误拒率 | 跨课程误召回 |
| --- | --- | --- | --- | --- | --- |
| 知识点库 `knowledge_points` | 1.0000 | 1.0000 | 0.8000 | 0.0667 | 0 |
| 完整混合语料 `full` | 1.0000 | 0.8778 | 0.8000 | 0.0667 | 1 |

真实学生问题：

| 作用域 | 条数 | recall@5 | MRR | 误拒率 | 跨课程误召回 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 知识点库 `knowledge_points` | 21 | 1.0000 | 0.9524 | 0.2381 | 1 |
| 完整混合语料 `full` | 21 | 1.0000 | 0.8071 | 0.1429 | 0 |

关键结论：

- 知识点库在 20 条标准问法和 10 条改写问法上均为 top1 命中；该作用域 MRR 为 `1.0000`。
- 完整混合语料中仍会有正确资料排到第 2/3 位，因此 MRR 为 `0.8778`；加入 `0.95/0.05` 混合重排前为 `0.8500`，知识点库则从 `0.9833` 恢复到 `1.0000`，没有牺牲主表质量。
- 0.56 阈值下拒答 8/10，误拒 2/30。`near_miss` 仍有 2 条未被距离门控拦截，应由 `ANSWERABILITY_CHECK` 兜底。
- 完整语料有 1 条 top1 跨课程误召回，论文中应单列，不能隐藏在总体 recall 内。

可复现结果 JSON：

- `evaluation/results/knowledge_points.json`
- `evaluation/results/full_corpus.json`
- `evaluation/results/student_questions_knowledge_points.json`
- `evaluation/results/student_questions_full.json`
- `evaluation/results/pdf_extraction.json`
- `evaluation/results/default.json` 及 `top*_*.json` 中的参数对比结果

## PDF 页码抽取质量

```bash
python -m evaluation.pdf_extraction
```

脚本统计真实 PDF 的页数、可提取文本页覆盖率、短页比例，以及分块后能定位页码的 chunk 比例。当前 `data/uploads` 中没有 PDF，因此结果 JSON 明确为 `status=no_samples`。加入真实 PDF 后应重新运行，不能把 Markdown 章节 metadata 的覆盖情况当作 PDF 页码质量。

## 消融建议

1. **纯 LLM**：不检索，统计编造率与拒答率。
2. **普通 RAG**：向量检索 + 生成，不开门控。
3. **完整系统**：距离门控 + 可答性三态 + 未收录标记。
4. **消融**：分别关闭距离门控、可答性判定，观察拒答准确率与误拒率。
5. **重排消融**：`RERANK_ENABLED=false` vs `true`，并比较 `RERANK_LEXICAL_WEIGHT` 的 0.05 / 0.10 / 0.30。
6. **参数**：`top_k` 取 3 / 5 / 8，chunk size 取 500 / 800 / 1000。

## 局限

- 40 条种子标注与 21 条真实学生问题必须分开报告，不能混成一个总体分数。
- 本脚本不包含 `ANSWERABILITY_CHECK` 的 LLM 判定，不能把离线检索指标当作端到端回答准确率。
- 阈值按 `bge-m3` 标定；换 embedding 模型后必须重新跑 `--thresholds`。
- 当前真实学生问题仍只有 21 条，论文中应写明规模，并继续扩充到 100 条以上。
- 当前上传目录没有真实 PDF，因此页码抽取质量暂为 `no_samples`；该缺口不影响 Markdown/CSV 的章节 metadata。
