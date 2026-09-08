# ClimWorkflow 知识检索：精简可观测性（对照「监控三阶段」）

本目录把图中的评测/监控思路落到 **现有 G6 检索**，不另起 Langfuse/LangSmith 项目，也不改 QueryEngine。

检索评测 **没有 LLM 生成步**：不记 prompt/response/token 费用；那些属于 `real_agent` Trace，见 `evals/climate/real_agent.py`。

## 已有基础

| 能力 | 位置 |
|---|---|
| 3 条词面冒烟 | `scenarios/knowledge_alias_smoke.yaml`（不进四场景） |
| 20 条人审问句 | `knowledge_queries.yaml` |
| BM25 vs hybrid 表 | `scripts/climate_knowledge_recall.py` |

## 与图中三阶段的对应

### 第一阶段：追踪每一步

对每条问句写 JSONL（`--out-dir`）：

- 召回了哪些父段（`source` / `chunk_id` / `score`）
- BM25、hash_dense、hybrid 三路排序如何变化
- 查询耗时 `latency_ms`
- `llm_prompt` / `token_cost` 固定为 `null`（本评测无生成）

不上 Langfuse：默认禁网、可进 CI、无额外账号。需要云追踪时另开日，不得把密钥写入 Context。

### 第二阶段：质量指标

`summary.json` / 报告摘要：

- **Recall@5**（BM25 / hybrid / hash_dense）
- **误召**：gold 前出现 `must_not_grib`
- **应 miss 泄漏率**：对应图中「无依据仍给出内容」的检索版
- **P50 / P95 `latency_ms`**：单机离线查询，不是线上用户 SLO
- 费用：哈希向量为 0；未接 Embedding/LLM API

### 第三阶段：回归门禁

`knowledge_gates.yaml` + pytest（已由 GitHub Actions `uv run pytest` 执行）：

- hybrid Recall@5 ≥ 0.85
- 全部 `should_miss` 必须 `CLIMATE_KNOWLEDGE_MISS`
- 近义问句至少 2 条 hybrid 命中

Prompt/配置与代码同库：`knowledge_queries.yaml`、`knowledge_gates.yaml`、fixture Markdown。

## 怎么跑

```powershell
uv run python scripts/climate_knowledge_recall.py --out-dir evals/reports/knowledge-observe
uv run pytest tests/test_climate/test_knowledge.py::test_knowledge_observe_gate -q
```

`evals/reports/knowledge-observe/` 不提交。
