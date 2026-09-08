# Day 20：G6 检索语料与离线召回评测（完善设计，不开第九工具）

## 为何适合作为 Day 20

Day 19 已宣称 **Phase G6 阶段验收 PASS**：混合检索、只读第九工具、3 条别名冒烟均已落地。
剩下的缺口不是「再做一个工具」，而是：

- 语料只有 `t2m` / `u10` 两段，覆盖不了静态目录的 8 个变量；
- 评测问句几乎整句出现在文档里，测的是词面重合，不是泛化；
- 干扰项偏弱（问气温时 u10 太容易分开）；金标用父段是否出现 `"t2m"` 子串，不稳；
- 真实 Embedding 仍是 GAP；哈希稠密下「只向量」没有语义解释力。

因此本日是 **G6 跟随增量（语料 + 人审评测表 + 两路召回脚本）**，**不是 Phase G7**，
也 **不是** 新开第九工具 / Agent ingest / Chroma。不合适的做法才是：把本日写成新阶段、
把 20 条问句灌进 `knowledge_alias_smoke`、或把真 Embedding / SKILL / 历史报告列为 P0。

## 今日目标

在 **不推翻 G0～G6 契约** 的前提下，按冻结顺序做完三件事：

1. 冻结 **DEC-G6-002**，把第 14C 节与需求 ID 写入 `SPEC.md`（先规格、后语料）；
2. 落地 P0：目录对齐的 8 篇变量说明书 + 与正文分离的约 20 条问句表 + 同一脚本打 BM25 / hybrid；
3. 回归 G6 门闩：默认仍八工具；`knowledge_alias_smoke` 仍为 3 条冒烟；目录闸门不被检索绕过。

- **SPEC 需求**：DEC-G6-002、CORPUS-001、EVAL-006、EVAL-007、TEST-009；PHASE-001 **保持** G6 阶段验收 PASS，本日不宣称新阶段
- **预计投入**：5～7 小时（SPEC 14C + 人审语料/问句 + 召回脚本 + 离线门闩；不跑真实 Embedding API / 真实 CDS / `real_agent`，除非用户显式允许）
- **完成标志**：DEC-G6-002 已关闭；上述 MUST 有实现或脚本/fixture 证据；CI 仍禁网；未把问句写回文档刷绿；未上 Chroma；未改 QueryEngine；未覆盖历史 baseline json
- **上一天**：[Day 19](DAY_19_G6_HUMAN_ACCEPTANCE.md)（G6 人工总验收 PASS）

## 今日原则

- 不修改 QueryEngine 执行语义。
- 不把第九工具并入默认 registry；不新增第五类 plan `action`；不做 `climate_ingest_*`。
- 不引入 Selenium / Playwright / GraphRAG / LangChain / Chroma / MCP Server / 联网 Embedding。
- 不执行用户或模型生成的 Python / Shell / `expr`。
- 检索命中不得当作 CDS 下载许可；acquire 仍走 `CLIMATE_METADATA_REJECTED`。
- 不把检索结果写入 `context.json` 步骤状态；中断后仍必须先 `climate_read_context`。
- 不改写 `evals/baselines/climate-real-9b592ba.json` / `climate-real-g5-skill.json` / `climate-real-g6-skill.json` / `climate-real-g6-knowledge.json`。
- 不把 `knowledge_alias_smoke` 扩成 20 条，不把它并入 `_REAL_OFFLINE_ORDER`，不宣称 Bench-85 / 企业知识库准确率 / 92%。
- 不为了召回表好看把评测问句写进说明书。
- 不读取、不打印、不提交 `.cdsapirc` / API key。
- 不自动提交/推送；人工确认后另发提交指令。

## 顺序（必须按此，不得颠倒）

| 步 | 做什么 | 明确后做 / 不做 |
|---|---|---|
| 0 | 冻结边界与对照表字段 | 不先改 `knowledge.py` 算法 |
| 1 | 8 变量对照表 → 8 篇说明书 | 不上 SKILL.md / 历史报告 |
| 2 | 约 20 条问句表（与正文分离） | 不灌进 Agent 冒烟 YAML |
| 3 | 同一脚本：BM25 vs hybrid（哈希稠密只作对照） | 不把 dense-only 写成语义结论 |
| 4 | **仅当第 3 步暴露机制问题** 才改切块 / RRF / 合格阈值 | 不为刷绿改评测句 |
| 5 | 真 Embedding 三路对比 | **另开日**，要书面许可；本日 P0 不做 |

时间不够：停在第 3 步并在日终标 GAP；不得跳到第 5 步，也不得先上 Chroma。

## 与 G6 已交付能力的对照（必读）

| 能力 | Day 18～19 | Day 20 是否做 | 说明 |
|---|---|---|---|
| `climate_query_knowledge` | 已有，默认不注册 | **保持** | 不开第十工具、不改注册策略 |
| 哈希 Embedding + BM25 + RRF | 已有 | **保持** | 本日评测用它打表，不宣称语义生产就绪 |
| fixture 语料 | `t2m` + `u10` 一段文件 | **扩** | 覆盖目录 8 变量；硬负例优先露点 |
| `knowledge_alias_smoke` | 3 条词面别名 | **保持 3 条** | 词面冒烟可以与正文有重叠；泛化证据走 EVAL-006 |
| 真实 Embedding API | G6 GAP | **仍 GAP** | 本日不调用 |
| Chroma / 向量库引擎 | 禁止 | **否** | 8 篇文档不必上库 |
| SKILL / 历史报告入索引 | 无 | **否（P0）** | 当前 chunk 无 `doc_type`；混仓会污染别名召回 |

## Day 19 已冻结、今日必须保持的事实

| 项 | Day 19 事实 | 今日如何证伪/确认 |
|---|---|---|
| G6 阶段验收 | PASS | 不回退 RAG-001～006 / SKILL-003 / EVAL-005 / TEST-008 |
| 默认 Climate 工具 | 8 | `create_climate_tool_registry()` 仍恰好 8 个 `climate_*` |
| QueryEngine | 无 Climate diff | `git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py` 必须为空 |
| 历史 baseline | `9b592ba` / `g5-skill` / `g6-skill` / `g6-knowledge` 不覆盖 | 对应 `git diff` 必须为空 |
| 四场景 `real_offline` | `real_pass_rate=1.0`、traces=4 | 再跑 CLI；知识评测不得并入 `_REAL_OFFLINE_ORDER` |
| 非目标 | 无 Selenium / 无第五类 action / 无 Chroma | 新脚本与 knowledge 模块 import 扫描 |

## 硬约束（与 DEC-G6-001 一致，本日追加 DEC-G6-002）

- 语料上限 = `formats.DATASET_VARIABLES["reanalysis-era5-single-levels"]` 的 **8** 个变量，禁止发明未登记 CDS 名。
- 每篇说明书必须同时给出 **CDS 长名**（目录 / acquire）与 **GRIB 短名**（inspect / 口语别名评测）。
- 问句表与说明书不得共用同一整句作为「唯一命中证据」。
- 金标用 `canonical`（CDS 长名 + GRIB 短名）和/或 `source` / `parent_id`，禁止只用父段子串 `"t2m"`。
- 召回脚本必须对 BM25 与 hybrid **共用父段去重** 后再比 Top-5，否则不可比。
- 默认 pytest / CI 不下载 Embedding 模型、不调用 Embedding HTTP API。
- 历史 `real_agent` 本日 **默认不重跑** MODEL-001。

## DEC-G6-002 冻结表（编码前写入 SPEC 第 14C 节）

| 决策 | 冻结值 | 理由 |
|---|---|---|
| 本日性质 | G6 跟随：语料 + 离线召回表；**不是** G7、不是新工具 | G6 工具面已验收 |
| 语料范围 | 仅目录 8 变量，一篇一文件或一文八个 `##` 父段 | 小而可核对 |
| 命名 | 文档同时写 CDS 长名与 GRIB 短名 | 避免 `2m_temperature` 与 `t2m` 两套体系打架 |
| 硬负例 | 问 2m 气温时优先 `2m_dewpoint_temperature` / `d2m`；u10 可留作弱干扰 | 现有 u10 测不出近义混淆 |
| 评测集 | `evals/climate/knowledge_queries.yaml` 约 20 条；类型含短名精确 / 英近义 / 中近义 / 应 miss | 与索引正文分离 |
| 冒烟 | `knowledge_alias_smoke` 仍 3 条，不扩、不进四场景 | 避免把实验当成 Agent 回归 |
| 对比实验 | 同一脚本打 BM25 与 hybrid；哈希 `dense_search` 可跑但不得当语义基线 | 真 Embedding 前只稠密无意义 |
| 指标 | Recall@5、误召排序、应 miss → `CLIMATE_KNOWLEDGE_MISS`；禁止抄 92% / Ragas 全套 | 样本小，只报方向 |
| 目录闸门 | 保持 RAG-004；应 miss 题另测，不为 20 条各跑 acquire | 契约已有，避免评测膨胀 |
| 入库仍禁止 | Agent ingest、SKILL.md、历史报告、Selenium 抓门户 | 无 `doc_type` 过滤 |
| 向量库 | 仍 JSONL + 内存余弦 | 8 篇不上 Chroma |
| 真 Embedding | 仍 GAP，另开日 | 不阻塞 P0 |
| QueryEngine / 默认 registry | 不改 | 与 Day 19 一致 |

无评审不得把上表改成「先引入 Chroma / 先上 Embedding 再说」。

建议 CDS ↔ GRIB 对照（写入对照表，人审后才能进 fixture；短名以 ERA5 常用表为准，不得与目录长名冲突）：

| CDS 长名（目录） | GRIB 短名 | 本日硬负例优先 |
|---|---|---|
| `2m_temperature` | `t2m` | `d2m` |
| `2m_dewpoint_temperature` | `d2m` | `t2m` |
| `10m_u_component_of_wind` | `u10` | `v10` |
| `10m_v_component_of_wind` | `v10` | `u10` |
| `mean_sea_level_pressure` | `msl` | `sp` |
| `surface_pressure` | `sp` | `msl` |
| `total_precipitation` | `tp` | 应 miss 或 sst（按问句） |
| `sea_surface_temperature` | `sst` | `t2m` |

## 写入 SPEC 的 MUST（状态先 GAP，回填前不得标 PASS）

- **CORPUS-001（MUST，G6 跟随）**：`tests/fixtures/climate_knowledge/` 覆盖目录 8 变量；每段含 CDS 长名、GRIB 短名、2～5 个别名、单位、一句话局限、来源（手写 / 官方摘录 URL+`retrieved` / `llm_paraphrase` 且短名已核对）。禁止未登记变量。不得把 SKILL.md 或历史报告当 P0 语料。
- **EVAL-006（MUST，G6 跟随）**：独立问句表约 20 条（允许 16～24），字段至少 `id`、`query`、`type`、`canonical_cds`、`canonical_grib`、`gold_source`、`must_not_grib`。`type` ∈ `exact_short` / `en_near` / `zh_near` / `should_miss`。问句整句不得出现在对应说明书正文。应 miss 至少 3 条（含未登记名或 SPI 类；须对照已入库正文出题，避免局限句误召）。YAML 声明不是 Bench-85 / 不是企业知识库准确率。
- **EVAL-007（MUST，G6 跟随）**：可复现脚本（建议 `scripts/climate_knowledge_recall.py`）对 EVAL-006 打 BM25 与 hybrid 的 Recall@5 与误召；父段去重规则与 `hybrid_search` 一致。哈希 `dense_search` 若输出，列名必须标明 `hash_dense`，日终不得写成语义 Embedding 结果。脚本禁网。不把该表并入 `_REAL_OFFLINE_ORDER`。
- **TEST-009（MUST，G6 跟随）**：pytest 覆盖：8 变量 fixture 与 `DATASET_VARIABLES` 对齐；问句整句不在正文；至少 2 条近义问句 hybrid Top-5 命中 gold；至少 1 条应 miss → `CLIMATE_KNOWLEDGE_MISS`；RAG-004 仍成立。默认 pytest 禁网。不得删除或放宽 Day 18 的 registry / 错误码契约。

错误码不新增。应 miss 使用已有 `CLIMATE_KNOWLEDGE_MISS`。

## 预期文件

```text
docs/climate-agent/SPEC.md                                 （EXTEND：第 14C 节 DEC-G6-002 与 CORPUS/EVAL/TEST）
docs/climate-agent/daily/DAY_20_G6_RAG_CORPUS_EVAL.md     （本文件）
tests/fixtures/climate_knowledge/                         （REPLACE/EXTEND：8 变量说明书；可拆文件）
evals/climate/knowledge_queries.yaml                      （NEW：约 20 条人审问句）
scripts/climate_knowledge_recall.py                       （NEW：BM25 vs hybrid 表；禁网）
tests/test_climate/test_knowledge.py                      （EXTEND：TEST-009）
evals/climate/scenarios/knowledge_alias_smoke.yaml        （保持 3 条；禁止扩成评测全集）
```

先写 SPEC 14C 与对照表，再改 fixture。不得先改 QueryEngine，不得先接 Embedding API。

## 完整操作流程

### 1. 工作区分类（15 分钟）

```powershell
git status --short --branch
git diff --stat
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json evals/baselines/climate-real-g5-skill.json evals/baselines/climate-real-g6-skill.json evals/baselines/climate-real-g6-knowledge.json
```

分类：Day 20 拟改文件 / 会话前脏文件 / 禁止提交（凭证、真实 NetCDF、`.part`、`evals/reports/`、简历与面试草稿）。

`QueryEngine` 与历史 baseline diff 必须为空。

### 2. 冻结 DEC-G6-002 并写入 SPEC 第 14C 节（30～45 分钟）

编码前必须把冻结表与 CORPUS-001 / EVAL-006 / EVAL-007 / TEST-009 写入 `SPEC.md`，状态先标 **GAP**。
第 15 节 Phase G6 段只追加「Day 20 语料与召回评测」，**不得**改成 Phase G7。
PHASE-001 保持 G6 阶段验收 PASS。

无 14C 不得开始改 fixture。

### 3. CORPUS-001：对照表 + 8 篇说明书（1.5～2 小时）

1. 按上表写出人审对照（可放在日终附件或 fixture 文件头注释，但问句表本身不入库索引）。
2. 每个目录变量一段 `##` 父段：官方长名、短名加粗、别名、单位、局限、来源。
3. 别名用官方或已核对说法；**评测用的中文近义（如「近地面气温」「青岛…叫什么」）不要写进正文**。
4. 硬负例成对出现（t2m↔d2m、u10↔v10、msl↔sp）。
5. 重建索引仍走 `rebuild_knowledge_index`；不新增 ingest 工具。

现有 `era5_variable_aliases.md` 可以拆文件或扩写，但不得残留「评测句 = 文档句」的唯一证据。

### 4. EVAL-006：20 条问句表（1 小时）

独立 YAML，不要复制说明书句子。建议规模：

- 短名/长名精确：6～8
- 英文近义：4～6
- 中文近义：4～6
- 应 miss：3～5

抽检：对每条 `query` 在 `tests/fixtures/climate_knowledge/` 做全文搜索，**不得**命中整句。

应 miss 出题前先读局限句，避免文档里写了「不是 SPI」却被 BM25 拖上来。

### 5. EVAL-007：召回脚本（45～60 分钟）

脚本职责：

- 临时 workspace 复制 fixture、重建索引；
- 对每条问句跑 `bm25_search` 与 `hybrid_search`（父段去重后取 Top-5）；
- 判定是否命中 `gold_source` / canonical 短名或长名；
- 打印误召（gold 未进 Top-5，或 `must_not_grib` 排在 gold 前）；
- 应 miss：期望 hybrid 空结果 / `CLIMATE_KNOWLEDGE_MISS`；
- 退出码：脚本可返回 0 并打印表；**契约失败由 pytest 断言**，避免把实验脚本当成 CI 红线唯一入口。

禁止：网络、写 Context、调用 CDS、读取凭证。

哈希 dense 列可选；有则标题写 `hash_dense`。

### 6. TEST-009 与回归门闩（1～1.5 小时）

RED→GREEN：先加失败测试（语料对齐、问句不泄漏、近义召回、应 miss、RAG-004），再改 fixture/脚本。

若去掉文档中的「2 米气温」导致旧 `test_hybrid_search_recalls_t2m_parent` 失败：允许把该 parametrize **改为官方词面**（文档仍应含 `2m temperature` / `2 metre temperature` / `t2m`），泛化改由 EVAL-006 问句覆盖。禁止为保旧测试把近义问句写回文档。

```powershell
uv run pytest tests/test_climate/test_knowledge.py tests/test_climate/test_registry.py tests/test_skills/test_climate_skill.py -q
uv run pytest tests/test_climate tests/test_skills/test_climate_skill.py -q
uv run python scripts/climate_knowledge_recall.py
uv run ruff check src tests scripts evals
uv run python -m evals --suite climate --mode real_offline
uv run python -m evals --suite climate --mode synthetic_dry_run
uv run python -m evals --suite climate --mode real_offline --scenario knowledge_alias_smoke
uv run python -m evals --suite climate --mode real_offline --scenario report_quality_smoke
git diff --check
git diff -- evals/baselines/climate-real-9b592ba.json
```

记录：Climate collect 增量、passed/skipped、四场景仍 4 条、`knowledge_alias_smoke` 仍 3 次工具调用、召回表路径、Ruff、baseline 空 diff。

全量 `uv run pytest -q` **建议跑**；Windows 上游失败不计入 Climate / G6。时间不够须在日终标明「全量未跑」。

### 7. 机制改动门闩（默认跳过）

仅当召回表出现 **可复现机制失败**（例如 dewpoint 稳定压过 t2m、中文近义 0 召回且不是问句泄漏）才允许改 `knowledge.py` 的窗口 / RRF / `COSINE_QUALIFY`。

改动必须：

- 仍同时跑 BM25 与稠密，禁止改成「只测 BM25 却宣称 hybrid」；
- 回归 Day 18 全部 `test_knowledge` 契约；
- 日终写明改了什么阈值、为什么。

无机制失败则 **不改检索代码**。

### 8. MODEL-001 与真 Embedding（默认跳过）

| 选择 | 条件 | 动作 |
|---|---|---|
| **A. 不重跑、不上 Embedding（默认）** | 本日只动语料/评测 | 日终写明哈希对照 ≠ 语义基线 |
| **B. 另开真 Embedding 三路** | 用户本回合书面允许网络与费用 | **另开日**，不是 Day 20 MUST |
| **C. 另开 `real_agent`** | 用户明确允许 | 新 baseline 文件名；禁止覆盖历史 json |

本清单 **不把 B/C 列为 MUST**。

### 9. 回填 SPEC 与日终（30 分钟）

仅当第 6 步门闩通过、无未修 blocker：

- CORPUS-001 / EVAL-006 / EVAL-007 / TEST-009：填真实路径或 pytest node ID，GAP→PASS。
- PHASE-001：**保持** G6 阶段验收 PASS；不得写 G7。
- 页首可加「G6 跟随：语料与召回评测（Day 20 需求 PASS/GAP）」须与证据一致。

无当日命令结果不得标 PASS。召回表数字写入日终时必须标注「n≈20，非生产准确率」。

## 今日主 Prompt

```text
执行 ClimWorkflow Day 20：G6 检索语料与离线召回评测（SPEC 14C + P0）。

先阅读 docs/climate-agent/SPEC.md 第 14B、15、16、18 节与
docs/climate-agent/daily/DAY_19_G6_HUMAN_ACCEPTANCE.md、
docs/climate-agent/daily/DAY_20_G6_RAG_CORPUS_EVAL.md。

硬约束：
- 不修改 QueryEngine；不把第九工具并入默认 registry；
- 不新增第五类 plan action；不执行任意 Python/Shell；
- 不引入 Selenium / GraphRAG / LangChain / Chroma / MCP Server / 联网 Embedding；
- 检索不得绕过 CDS 目录，不得替代 climate_read_context；
- 不改写 9b592ba / g5-skill / g6-skill / g6-knowledge；
- 不把 knowledge_alias_smoke 扩成 20 条；不把问句写回文档刷绿；
- 不重跑 real_agent，不上真 Embedding，除非用户本回合明确要求。

顺序：
1. 分类 git status/diff；确认 engine 与历史 baseline 无 diff；
2. 把 DEC-G6-002 与 CORPUS/EVAL/TEST 需求写入 SPEC 第 14C 节（先 GAP）；
3. 8 变量说明书（CDS 长名 + GRIB 短名；硬负例优先 dewpoint）；
4. 约 20 条问句表，整句不得出现在正文；
5. 召回脚本打 BM25 vs hybrid；哈希 dense 不作语义结论；
6. TEST-009 + 离线门闩；四场景仍为 4 条；
7. 无机制失败则不改 knowledge 算法；按证据回填 SPEC；不宣称 G7；
8. 输出日终报告；不提交、不推送，除非用户明确要求。
```

## 分步骤 Prompt

```text
只做工作区分类与敏感产物扫描；列出拟提交/禁止提交文件，不跑测试、不改代码。
```

```text
只把 DEC-G6-002 与第 14C 节 MUST 写入 SPEC.md，状态 GAP；不改 fixture、不写业务代码。
```

```text
只写 8 变量对照表与说明书 fixture（CORPUS-001）；不问句表、不改检索算法。
```

```text
只写 evals/climate/knowledge_queries.yaml（EVAL-006）；抽检问句不在正文；不跑 Agent。
```

```text
只实现 scripts/climate_knowledge_recall.py（EVAL-007）；禁网；不调用 Embedding API。
```

```text
只加 TEST-009 失败测试并 GREEN；保持 knowledge_alias_smoke 为 3 条。
```

```text
跑 Day 20 离线门闩，按真实证据回填 SPEC；无证据保持 GAP；不宣称 G7；不上 Embedding。
```

## 验收清单

- [x] 当日 `git status` 已分类；无凭证/真实数据/`.part`/`evals/reports`/简历面试稿进入拟提交集。
- [x] `query.py` / `query_engine.py` 无 Climate diff。
- [x] 历史 baseline json（`9b592ba` / `g5-skill` / `g6-skill` / `g6-knowledge`）无拟提交 diff。
- [x] DEC-G6-002 已写入 SPEC 第 14C 节并与实现一致。
- [x] CORPUS-001：8 变量说明书与目录对齐；含 CDS 长名与 GRIB 短名；无 SKILL/报告混仓。
- [x] EVAL-006：约 20 条问句独立成表；整句不在正文；含应 miss；声明不是 Bench-85。
- [x] EVAL-007：同一脚本输出 BM25 vs hybrid 表；父段去重可比；哈希 dense 未当语义结论。
- [x] TEST-009：pytest 覆盖对齐 / 不泄漏 / 近义命中 / miss / RAG-004。
- [x] `knowledge_alias_smoke` 仍 3 次 `climate_query_knowledge`，未并入四场景。
- [x] 默认 registry 仍八工具；第九工具仅 `include_knowledge=True`。
- [x] 无 Selenium / 第五类 action / 代码执行 / Chroma / 真 Embedding 主路径。
- [x] `CLIMATE_INTEGRATION=0` 下 Climate + Skill pytest 全绿（skip 仅 integration）。
- [x] Ruff PASS；`git diff --check` 干净。
- [x] 四场景 `real_offline` `real_pass_rate=1.0`（恰好 4 条核心 traces）。
- [x] PHASE-001 仍为 G6 阶段验收 PASS；**未**宣称 G7。
- [x] 未提交、未推送，除非用户另发指令。

## 风险与止损

- 问句泄漏：发现评测句出现在文档中 → 改问句或改文档，**禁止**两者保留同一句还宣称泛化。
- 为保旧测试把「2 米气温」留在文档且同时当 EVAL-006 近义题 → 停止；旧冒烟与泛化表职责分开。
- 用哈希 dense Recall 写「向量检索有效」→ 停止，改回对照列。
- dewpoint 与 t2m 对打全乱：先查文档是否互相抄别名；仍乱且可复现才允许进第 7 步改算法。
- 把 20 条问句写进 `knowledge_alias_smoke` 或 `_REAL_OFFLINE_ORDER` → 停止并撤回。
- 范围滑向 Chroma / 真 Embedding / GraphRAG / Agent ingest / SKILL 入索引 → 退回 DEC-G6-002。
- 时间不够：优先 14C + CORPUS-001 + EVAL-006；EVAL-007 / TEST-009 可标部分 GAP，不得用未跑脚本的数字填日终。
- 离线门闩失败：先定级再修；禁止放宽「检索可绕过目录」或默认九工具。

## 日终报告模板

```text
Day 20：
- 分支 / HEAD / dirty：
- DEC-G6-002：
- SPEC 14C：已写入 / 未写入
- CORPUS-001：
- EVAL-006（问句条数 / 不泄漏抽检）：
- EVAL-007（脚本路径；BM25 vs hybrid 方向结论；n=；hash_dense 是否当结论）：
- TEST-009 / Climate collect：
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：
- Ruff / git diff --check：
- real_offline 四场景：
- knowledge_alias_smoke（必须仍 3 次工具）：
- 默认工具数量：
- 是否改 knowledge 算法：否 / 是（阈值与理由）
- 真 Embedding：未做（默认）
- baseline 9b592ba / g5-skill / g6-* diff：
- QueryEngine diff：
- blocker/high：
- PHASE-001：保持 G6 阶段验收 PASS；未宣称 G7
- 全量 pytest：
- 剩余 GAP：
- 是否建议提交：
```

## 日终报告（执行日填写）

```text
Day 20：
- 分支 / HEAD / dirty：feat/climworkflow-mvp @ 01007f8；ahead origin；working tree dirty（Day 20 语料/问句/脚本/SPEC/DAY_20；另有简历面试稿未纳入拟提交）
- DEC-G6-002：已写入第 14C 节并关闭；与实现一致
- SPEC 14C：已写入；CORPUS-001 / EVAL-006 / EVAL-007 / TEST-009 按命令证据 PASS
- CORPUS-001：tests/fixtures/climate_knowledge/ 8 篇，与 DATASET_VARIABLES 对齐；CDS 长名 + GRIB 短名；硬负例 t2m↔d2m；无 SKILL/报告混仓；已删除旧 era5_variable_aliases.md
- EVAL-006（问句条数 / 不泄漏抽检）：knowledge_queries.yaml 20 条（8 exact_short / 5 en_near / 4 zh_near / 3 should_miss）；pytest 整句不在正文 PASS；声明不是 Bench-85 / 不是企业知识库准确率
- EVAL-007（脚本路径；BM25 vs hybrid 方向结论；n=；hash_dense 是否当结论）：scripts/climate_knowledge_recall.py；父段去重后 Top-5；n=20 scored=17 miss=3；BM25@5=1.00；hybrid@5=0.94（q04 v10 exact 被哈希 RRF 挤出 Top-5）；hash_dense@5=0.76 仅为哈希对照，不当语义结论；should_miss_ok=3/3；misorder=4 记入方向、未改算法
- TEST-009 / Climate collect：324 tests；::test_corpus_covers_catalog_eight_variables / ::test_eval_queries_do_not_leak_into_corpus / ::test_near_synonym_hybrid_hits_gold（q09/q14）/ ::test_should_miss_returns_knowledge_miss / ::test_retrieval_does_not_bypass_cds_catalog
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：326 passed, 2 skipped（skip = climate_integration）
- Ruff / git diff --check：PASS / 干净
- real_offline 四场景：real_pass_rate=1.0；traces=4（sample_pipeline / cached_inspect / multiturn_recovery / pre_tool_output_guard）；knowledge_queries 与 knowledge_alias_smoke 均不在 _REAL_OFFLINE_ORDER
- knowledge_alias_smoke（必须仍 3 次工具）：单独 CLI 通过；real_pass_rate=1.0；1 trace / 3 次 climate_query_knowledge
- 默认工具数量：8；climate_query_knowledge 仅 include_knowledge=True
- 是否改 knowledge 算法：否（无 dewpoint 稳定压过 t2m / 中文近义 0 召回的机制失败；仅把 canonical 名提前到父段首窗，属语料）
- 真 Embedding：未做（默认）
- baseline 9b592ba / g5-skill / g6-* diff：空
- QueryEngine diff：空（query.py / query_engine.py）
- blocker/high：无
- PHASE-001：保持 G6 阶段验收 PASS；未宣称 G7
- 全量 pytest：未跑（Windows 上游失败不计入 Climate / G6）
- 剩余 GAP：真实 Embedding API（刻意不做）；Chroma / G7 非本日
- 是否建议提交：建议用户审阅后另发提交指令。拟提交：SPEC 14C、DAY_19 次日链接、DAY_20、8 篇 fixture、knowledge_queries.yaml、climate_knowledge_recall.py、test_knowledge.py。禁止提交：简历草稿、面试稿、evals/reports、凭证、真实 NetCDF、.part
```
