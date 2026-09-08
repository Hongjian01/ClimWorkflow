# Day 18：G6 文档检索最小增量（ClimWorkflow 结合 RAG）

## 今日目标

在 **不推翻 G0～G5 契约** 的前提下，立项并落地 **Phase G6**：把非结构化文档检索接到现有 Agent 工具循环上。检索只解释「文档里怎么说」；**不得**替代静态 CDS 目录、磁盘 RunContext 或四类 plan action。

今日做完三件事：

1. 冻结 **DEC-G6-001**，把第 14B 节与需求 ID 写入 `SPEC.md`（先规格、后代码）；
2. 实现 P0：fixture 语料入库、BM25 + 稠密向量 + RRF 混合检索、只读第九工具 `climate_query_knowledge`（默认 **不** 注册）；
3. 离线评测：别名 → 官方变量文档的 Recall 硬断言；回归 G5 门闩。

- **SPEC 需求**：DEC-G6-001、RAG-001～RAG-006、SKILL-003、EVAL-005、TEST-008、PHASE-001（G6 需求可回填；阶段总验收今日不宣称）
- **预计投入**：6～8 小时（SPEC 写入 + 离线 RED→GREEN；不跑真实 Embedding API / 真实 CDS / `real_agent`，除非用户显式允许）
- **完成标志**：DEC-G6-001 已关闭；上述 MUST 有实现与 pytest node ID；默认 CI 仍禁网；默认 registry 仍为核心七工具 + `climate_validate_artifacts`（八工具）；索引与检索结果不写入 `context.json` 作为步骤成功
- **上一天**：[Day 17](DAY_17_G5_HUMAN_ACCEPTANCE.md)（G5 人工总验收 PASS）
- **下一天**：[Day 19](DAY_19_G6_HUMAN_ACCEPTANCE.md)（G6 人工总验收）

## 今日原则

- 不修改 QueryEngine 执行语义。
- 不新增第五类 plan `action`；`query_knowledge` 与 `validate` 一样：只读、可选、不进 DAG 硬断言。
- 不引入 Selenium / Playwright / 浏览器抓取 CDS 门户。
- 不执行用户或模型生成的 Python / Shell / `expr`。
- 不把检索命中当作 CDS 下载许可；acquire 仍走 `CLIMATE_METADATA_REJECTED`。
- 不把检索结果或向量库当作恢复权威源；压缩/重启后仍必须先 `climate_read_context`。
- 不 vendor / 不依赖 `jerry-ai-dev/MODULAR-RAG-MCP-SERVER` 或 WaLiAPI；不引入 LangChain / Chroma / 商用 Rerank API（G6 P0）。
- 不读取、不打印、不提交 `.cdsapirc` / API key；chunk 与 ToolResult 脱敏（无绝对路径、无密钥）。
- 不改写 `evals/baselines/climate-real-9b592ba.json` 与 `climate-real-g5-skill.json`。
- 不上 GraphRAG / RAPTOR / Self-RAG 微调 / CRAG 联网兜底 / 多 Agent 检索编排。
- 不自动提交/推送；人工确认后另发提交指令。

## 与 G5 / 论文边界的对照（必读）

| 能力 | G5 已有 | G6 是否做 | 说明 |
|---|---|---|---|
| 静态 CDS 目录 | 硬闸门 | **保持** | RAG 命中 ≠ 允许下载 |
| 磁盘 RunContext | 恢复权威 | **保持** | 禁止用旧报告向量猜当前步骤已成功 |
| 四类 action | 冻结 | **保持** | 检索不是 `acquire_data` 等第五类科学步骤 |
| 第八工具 validate | 默认注册 | **保持** | G6 第九工具默认不注册 |
| 文档别名 / 变量说明 | 无 | **做** | 口语「2 米气温」→ 文档中的 `t2m` 解释 |
| 写报告引用文档 | 仅模型参数 | **窄做** | 数字仍以 inspect profile 为准；文档只提供含义/单位/局限 |
| GraphRAG / 自由 PLAN | 非目标 | **否** | 目录已表达合法变量集合 |
| MCP Server 进程 | 无 | **否** | 同语义做成 Climate `BaseTool`，不另起 MCP |

## Day 17 已冻结、今日必须保持的事实

| 项 | Day 17 事实 | 今日如何证伪/确认 |
|---|---|---|
| G5 阶段验收 | PASS | 不回退 META-001 / CDS-005 / VAL-001 等 |
| 默认 Climate 工具 | 8（七核心 + validate） | `create_climate_tool_registry()` 仍恰好 8 个 `climate_*`；知识工具仅 `include_knowledge=True` |
| QueryEngine | 无 Climate diff | `git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py` 必须为空 |
| G4 baseline | `9b592ba` 未改写 | `git diff -- evals/baselines/climate-real-9b592ba.json` 必须为空 |
| 四场景 `real_offline` | `real_pass_rate=1.0`、traces=4 | 再跑 CLI；知识场景不得并入 `_REAL_OFFLINE_ORDER` |
| 非目标 | 无 Selenium / 无自由 PLAN / 无代码执行 | 新模块 import 扫描 + Skill 禁令测试 |

## 硬约束（与 DEC-G6-001 一致）

- 知识索引只允许工作区内相对路径：`.climate/knowledge/`（清单、chunk JSONL、稀疏倒排、稠密向量）；禁止 `~`、盘符、workspace 外路径。
- 入库语料仅限仓库 fixture 与测试临时目录；G6 **不**做 Agent 侧 `climate_ingest_*` 工具（避免变成第五类写操作与任意文件吸入）。
- 查询无命中或分数过低必须返回明确 miss（见 RAG-006），不得塞入无关 Top-K 冒充依据。
- 默认 pytest / CI 不下载 Embedding 模型、不调用 Embedding HTTP API。
- 历史 G5 `real_agent` 顺序未因第九工具默认暴露而失效；今日 **默认不重跑** MODEL-001。

## 预期文件

```text
docs/climate-agent/SPEC.md                              （EXTEND：第 14B 节 G6、第 9/10/15/16 节需求）
docs/climate-agent/daily/DAY_18_G6_RAG_KNOWLEDGE_TOOL.md
src/openharness/climate/knowledge.py                    （NEW：切块、BM25、稠密、RRF、索引读写）
src/openharness/climate/tools.py                        （EXTEND：ClimateQueryKnowledgeTool）
src/openharness/climate/registry.py                     （EXTEND：include_knowledge 可选第九工具）
src/openharness/climate/models.py                       （EXTEND：QueryKnowledge 输入/命中结构）
src/openharness/climate/errors.py                       （EXTEND：知识库错误码）
src/openharness/climate/paths.py                        （EXTEND：knowledge 目录）
src/openharness/climate/prompts.py                      （EXTEND：query 工具描述；非第五类 action）
.openharness/skills/climate-ds/SKILL.md                 （EXTEND：何时 query；禁止用检索替代 read_context/目录）
tests/fixtures/climate_knowledge/                       （NEW：ERA5 变量别名 Markdown fixture）
tests/test_climate/test_knowledge.py                    （NEW）
tests/test_climate/test_registry.py                     （EXTEND：默认仍八工具）
tests/test_skills/test_climate_skill.py                 （EXTEND：SKILL-003）
evals/climate/scenarios/knowledge_alias_smoke.yaml      （NEW：离线别名检索）
evals/climate/assertions.py                            （EXTEND：命中官方变量名/source）
```

先写 SPEC 与失败测试，再实现。不得先改 QueryEngine。

## 完整操作流程

### 1. 工作区分类（20 分钟）

```powershell
git status --short --branch
git diff --stat
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json
```

分类：G6 拟改文件 / 会话前脏文件 / 禁止提交（凭证、真实 NetCDF、`.part`、`evals/reports/`、简历草稿）。

`QueryEngine` 与 `9b592ba` diff 必须为空。若 G5 未提交，先由用户决定是否单独提交 G5，再叠 G6。

### 2. 冻结 DEC-G6-001 并写入 SPEC 第 14B 节（45～60 分钟）

编码前必须把下列冻结表写入 `SPEC.md`（建议新节 **14B. G6：文档检索最小增量契约**），需求状态先标 **GAP**。开放决策不得带着「未冻结 Embedding」直接写生产路径。

#### DEC-G6-001 冻结表（复制进 SPEC）

| 决策 | 冻结值 | 理由 |
|---|---|---|
| 检索在工作流中的位置 | 只读工具 `climate_query_knowledge`；**不是** plan action | 保持四类动作面；与 `climate_validate_artifacts` 同类 |
| 默认 registry | 仍为八工具；`include_knowledge=False` 为默认 | 避免默认 DAG/Agent 顺序与 G5 路径 C 漂移 |
| 索引位置 | workspace 相对 `.climate/knowledge/` | 与 RunContext 分离；崩溃不影响续跑 |
| 是否写入 Context | 查询 **不得** 把命中写入 `context.json` 步骤状态 | 防止用文档记忆冒充过程记忆 |
| 召回 | BM25 + 稠密向量，RRF 融合（k=60） | 短名 `t2m` 靠稀疏，近义靠稠密；2026 生产默认 |
| 稠密实现（测试/CI） | `DeterministicHashEmbedding`（无网、无模型文件） | 默认 pytest 禁网；数字可复现 |
| 稠密实现（真实） | G6 **GAP**：OpenAI 兼容 Embedding API 仅用户显式允许时另开日 | 不阻塞 P0 |
| 向量库 | 仓库内 JSONL + 内存余弦；不上 Chroma/Qdrant/HNSW 引擎 | 语料为 fixture 级，避免新基础设施 |
| 分块 | 小块召回 + 返回父段（Parent-Child）；固定窗口即可 | 变量定义句命中，报告需要上下文 |
| 语料 | `tests/fixtures/climate_knowledge/` 静态 Markdown（变量别名、单位、来源 URL+检索日） | 禁止 Selenium 抓门户 |
| 入库 API | Python 函数 `rebuild_knowledge_index(workspace, source_dir)`，仅测试/脚本调用 | G6 不做 Agent ingest 工具 |
| 空结果 | miss + `CLIMATE_KNOWLEDGE_MISS`，`retryable=true` | 与禁止静默 fallback 一致 |
| 目录关系 | acquire 前仍 `validate_cds_request_against_catalog` | RAG-004 |
| QueryEngine / MCP | 不改执行语义；不新增 MCP Server | 知识库以 Climate Tool 暴露 |
| 非目标 | GraphRAG、RAPTOR、Self-RAG、CRAG 外网、ColBERT、Image Caption、LangChain | 超出最小增量 |

无评审不得把上表改成「先引入 Chroma 再说」。

#### 写入 SPEC 的 MUST（状态 GAP，回填前不得标 PASS）

- **RAG-001（MUST，G6）**：必须能从 sandbox 内 `source_dir` 重建索引：切块、父段映射、BM25 倒排、稠密向量落在 `.climate/knowledge/`；幂等重建（清旧索引再写）；路径非法返回 `CLIMATE_INVALID_PATH`。不得 import selenium/playwright/cdsapi。
- **RAG-002（MUST，G6）**：`hybrid_search(query, top_k)` 必须同时跑稀疏与稠密，RRF 合并；测试语料上「2m temperature / 2 米气温 / 2 metre temperature」至少一路能把含 **`t2m`** 的父段召回进融合 Top-5（具体 k 以测试为准）。禁止只实现纯向量。
- **RAG-003（MUST，G6）**：`climate_query_knowledge` 输入 `extra=forbid`，字段仅 `query`（必填）与可选 `top_k`（有上界，建议 1～10，默认 5）；成功 `ok=true` 返回脱敏 hits（`chunk_id`、`parent_text`、`source` 相对路径、`score`）；只读；不创建 run、不改 Context version。
- **RAG-004（MUST，G6）**：契约测试：检索返回 `t2m` 文档 **不能** 使目录外 dataset/非法变量的 `cds_request` 通过；`CLIMATE_METADATA_REJECTED` 仍为 acquire 闸门。
- **RAG-005（MUST，G6）**：Skill + 测试禁止「用 query_knowledge 代替 read_context / 猜测步骤已成功」；`climate_query_knowledge` 发现 WAL 时行为与其它只读工具一致（报 `CLIMATE_RECOVERY_REQUIRED` 或 SPEC 写明「知识工具不修 WAL、不读 Context」——**冻结为：不调用 `_prepare_mutation`，不修 WAL；不读 run Context**，以免只读查询触发恢复副作用）。
- **RAG-006（MUST，G6）**：索引缺失 → `CLIMATE_KNOWLEDGE_NOT_FOUND`（`retryable=false`）；索引在但无命中 → `CLIMATE_KNOWLEDGE_MISS`（`retryable=true`）；不得编造 hits。
- **SKILL-003（MUST，G6）**：`climate-ds` 增加：acquire/report 前**可以**查询知识库以解析别名与写解释；**必须**先目录校验才能下载；中断后**必须**先 `climate_read_context`；禁止把检索写成第五类 action。
- **EVAL-005（MUST，G6）**：离线 scenario `knowledge_alias_smoke`：对 fixture 索引跑至少 3 条别名查询硬断言；YAML 声明不是 Bench-85 / 不是企业知识库准确率；**不**加入 `_REAL_OFFLINE_ORDER`。
- **TEST-008（MUST，G6）**：`test_knowledge.py` 覆盖重建、混合检索、工具契约、默认 registry 不含第九工具、模块不 import 禁名单；默认 pytest 禁网。

错误码（写入 SPEC 第 9 节，均 `retryable` 如下）：

| 码 | retryable | 含义 |
|---|---|---|
| `CLIMATE_KNOWLEDGE_NOT_FOUND` | false | 未建索引或索引损坏且无法读取 |
| `CLIMATE_KNOWLEDGE_MISS` | true | 索引存在但无合格命中 |
| `CLIMATE_KNOWLEDGE_CORRUPT` | false | 清单/向量与 chunk 不一致 |

`details` 允许键可增 `chunk_id`、`source`（相对路径）、`hit_count`，仍禁止绝对路径与密钥。

### 3. 需求追踪抽查（20 分钟）

对照 SPEC 第 14B / 16 节列出今日 MUST → 预定测试文件。无 14B 不得开始 GREEN。

```text
RAG-001 … TEST-008 / DEC-G6-001
→ 实现文件
→ 预定 pytest node ID
→ 当日命令结果（回填时）
```

### 4. RED：失败测试与 fixture（1～1.5 小时）

Fixture 至少包含一篇 UTF-8 Markdown，例如：

- 明确写出 `t2m` 是 ERA5 2 metre temperature / 2m temperature / 2 米气温；
- `source_url` + `retrieved` 日期（静态，不抓网）；
- 另含一个干扰变量（如 `u10`），用于证明不是「任意查询都返回第一段」。

测试至少：

- 非法路径 / workspace 外 `source_dir` 失败；
- 重建后目录存在且可重复重建；
- 纯 BM25 或纯向量单独可测，但 **契约断言打在 hybrid**；
- 工具 `extra=forbid`、无 `code`/`shell`；
- `create_climate_tool_registry()` 名称集合不含 `climate_query_knowledge`；
- `include_knowledge=True` 时第九工具可注册且不覆盖同名；
- 目录闸门不被检索绕过（RAG-004）；
- Skill 禁令字符串。

### 5. GREEN：最小实现（2～3 小时）

`knowledge.py` 建议边界：

- `split_parent_child(markdown) -> list[Chunk]`；
- `rebuild_knowledge_index(...)`；
- `hybrid_search(...)`；
- 哈希 Embedding：对 token 稳定哈希到固定维（如 64/128），仅用于测试可分性，**日终不得宣称「语义 Embedding 已生产就绪」**。

工具层只做参数校验 + 调 `hybrid_search` + 错误码包装。不创建 run。

Skill / `prompts.TOOL_DESCRIPTIONS` 同步；`SKILL_CONTRACT_PHRASES` 如需增加短语必须测试同步，避免 G5 合同漂移。

### 6. EVAL-005 与回归门闩（1 小时）

```powershell
uv run pytest tests/test_climate/test_knowledge.py tests/test_climate/test_registry.py tests/test_skills/test_climate_skill.py -q
uv run pytest tests/test_climate tests/test_skills/test_climate_skill.py -q
uv run ruff check src tests scripts evals
uv run python -m evals --suite climate --mode real_offline
uv run python -m evals --suite climate --mode synthetic_dry_run
uv run python -m evals --suite climate --mode real_offline --scenario knowledge_alias_smoke
uv run python -m evals --suite climate --mode real_offline --scenario report_quality_smoke
git diff --check
git diff -- evals/baselines/climate-real-9b592ba.json
```

记录：Climate collect 增量、passed/skipped/failed、四场景仍为 4 条、`knowledge_alias_smoke` 单独通过、Ruff、baseline 空 diff。

全量 `uv run pytest -q` **建议跑**；Windows 上游失败不计入 Climate / G6。时间不够须在日终标明「全量未跑」。

### 7. G6 边界只读审查（30 分钟）

按 blocker / high / medium / low 输出，至少覆盖：

- `knowledge.py` 未 import selenium、playwright、cdsapi、langchain、chromadb。
- 工具输入无 `code` / `shell` / `expr` / 绝对路径字段。
- 默认 registry 仍八工具。
- 未把 hits 写入 Context；未把 query 登记为 `StepAction`。
- Skill 仍禁止第五类 action、任意 Python、用检索代替 read_context。
- Eval YAML 声明不是 Bench-85 / 不是 Hit Rate 92% 一类外部模板数字。

只修 **blocker / high**。禁止借 G6 加 GraphRAG 或 MCP。

### 8. MODEL-001 决策（默认跳过）

| 选择 | 条件 | 动作 |
|---|---|---|
| **A. 不重跑（默认）** | 第九工具未进默认 registry；不对外宣称「G5 3/3 在 G6 后仍成立于新 fingerprint」 | 日终写明：`9b592ba` / `g5-skill` 仍有效于当时工具集 |
| **B. 另开 3× `real_agent`** | 用户明确允许，且已把知识工具加入 Agent 可见 registry | 新 baseline 文件名；禁止覆盖历史 json |

本清单 **不把 B 列为 MUST**。

### 9. 回填 SPEC 与日终（30 分钟）

仅当第 6 步门闩通过、第 7 步无未修 blocker：

- RAG-001～006 / SKILL-003 / EVAL-005 / TEST-008：填真实 node ID，GAP→PASS。
- PHASE-001：可写「G6 需求 PASS，阶段人工总验收未宣称」；**不得**写 G6 阶段验收 PASS。
- 页首版本行增加「G6 最小增量（需求 PASS / 阶段未宣称）」类表述，须与证据一致。

无当日命令结果不得标 PASS。

## 今日主 Prompt

```text
执行 ClimWorkflow Day 18：G6 文档检索最小增量（SPEC + P0）。

先阅读 docs/climate-agent/SPEC.md 第 3、9、10、14A、15、16、18 节与
docs/climate-agent/daily/DAY_17_G5_HUMAN_ACCEPTANCE.md、
docs/climate-agent/daily/DAY_18_G6_RAG_KNOWLEDGE_TOOL.md。

硬约束：
- 不修改 QueryEngine；不把第九工具并入默认 registry；
- 不新增第五类 plan action；不执行任意 Python/Shell；
- 不引入 Selenium / GraphRAG / CRAG 外网 / LangChain / Chroma / MCP Server；
- 检索不得绕过 CDS 目录，不得替代 climate_read_context；
- 不改写 climate-real-9b592ba.json；不读取/打印/提交凭证；
- 不重跑 real_agent，除非用户本回合明确要求。

顺序：
1. 分类 git status/diff；确认 engine 与 9b592ba 无 diff；
2. 把 DEC-G6-001 与 RAG/SKILL/EVAL/TEST 需求写入 SPEC 第 14B 节（先 GAP）；
3. RED：knowledge / registry / Skill / eval 失败测试与 fixture；
4. GREEN：最小混合检索 + climate_query_knowledge；
5. 跑离线门闩与 knowledge_alias_smoke；四场景仍为 4 条；
6. blocker/high 审查后按证据回填 SPEC；G6 阶段验收不宣称；
7. 输出日终报告；不提交、不推送，除非用户明确要求。
```

## 分步骤 Prompt

```text
只做工作区分类与敏感产物扫描；列出拟提交/禁止提交文件，不跑测试、不改代码。
```

```text
只把 DEC-G6-001 与第 14B 节 MUST 写入 SPEC.md，状态 GAP；不写业务代码。
```

```text
只写 RAG-001/002 失败测试与 climate_knowledge fixture，不实现检索。
```

```text
只实现 knowledge 索引与 hybrid_search，使 test_knowledge 中非工具用例 GREEN。
```

```text
只实现 climate_query_knowledge 与 include_knowledge 注册；确认默认仍八工具。
```

```text
只更新 Skill/prompts（SKILL-003）与 RAG-004/005/006 契约测试。
```

```text
只加 EVAL-005 knowledge_alias_smoke；不得加入默认四场景顺序。
```

```text
跑 Day 18 离线门闩，按真实 node ID 回填 SPEC；无证据保持 GAP；不宣称 G6 阶段验收。
```

## 验收清单

- [x] 当日 `git status` 已分类；无凭证/真实数据/`.part`/`evals/reports` 进入拟提交集。
- [x] `query.py` / `query_engine.py` 无 Climate diff。
- [x] `evals/baselines/climate-real-9b592ba.json` 无 diff。
- [x] DEC-G6-001 已写入 SPEC 并与实现一致。
- [x] RAG-001：索引重建 PASS；路径沙箱；无 Selenium。
- [x] RAG-002：混合检索（BM25+稠密+RRF）PASS；非纯向量。
- [x] RAG-003：只读第九工具契约 PASS；默认未注册。
- [x] RAG-004：检索不能放行非法 CDS 请求。
- [x] RAG-005：Skill/测试禁止用检索替代 read_context；查询不修 WAL、不写 Context。
- [x] RAG-006：缺索引 / 无命中错误码正确，无伪造 hits。
- [x] SKILL-003：指导「可查询、不可当闸门/进度」。
- [x] EVAL-005：`knowledge_alias_smoke` 单独通过；未并入四场景。
- [x] 默认 registry 仍八工具；`include_knowledge=True` 可装第九工具。
- [x] `CLIMATE_INTEGRATION=0` 下 Climate + Skill pytest 全绿（skip 仅 integration）。
- [x] Ruff PASS；`git diff --check` 干净。
- [x] 四场景 `real_offline` `real_pass_rate=1.0`（恰好 4 条核心 traces）。
- [x] PHASE-001：G6 需求已按证据 PASS 或诚实 GAP；**未**宣称 G6 阶段验收。
- [x] 未提交、未推送，除非用户另发指令。

## 风险与止损

- 哈希 Embedding 导致别名召不回：先保证 **BM25 对 `t2m` / `2m temperature` 可命中**，RRF 仍融合两路；不得为绿灯改成「只测 BM25 却宣称 hybrid」。
- 第九工具若被默认注册：停止，改回 `include_knowledge=False`，更新 REG 测试；不得默默扩成九工具默认集。
- 有人把 `query` 加进 `StepAction`：停止并开 SPEC 变更，不在 Day 18 内实现。
- 范围滑向 GraphRAG / Chroma / MCP / 外网 CRAG / 克隆 Modular RAG 仓库：停止并退回 DEC-G6-001。
- 时间不够：优先 SPEC 14B + RAG-001/002/003/004 + 默认八工具回归；EVAL-005 / SKILL-003 可标部分 GAP 并在日终列出。
- 离线门闩失败：先定级再修；禁止删测试或放宽「检索可绕过目录」。

## 日终报告模板

```text
Day 18：
- 分支 / HEAD / dirty：
- DEC-G6-001：
- SPEC 14B：已写入 / 未写入
- RAG-001：
- RAG-002：
- RAG-003：
- RAG-004：
- RAG-005：
- RAG-006：
- SKILL-003：
- EVAL-005：
- TEST-008 / Climate collect：
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：
- Ruff / git diff --check：
- real_offline 四场景：
- knowledge_alias_smoke：
- 默认工具数量：
- baseline 9b592ba diff：
- QueryEngine diff：
- blocker/high：
- PHASE-001：
- MODEL-001：未重跑 / 已另写新 baseline
- 剩余 GAP：
- 是否建议提交：
```

## 日终报告（2026-09-08）

```text
Day 18：
- 分支 / HEAD / dirty：feat/climworkflow-mvp @ 8cb1ad6；ahead 1；working tree dirty（G6 源码/测试/Skill/Eval/SPEC；未提交）
- DEC-G6-001：已写入 SPEC 第 14B 节并按冻结值实现；同日关闭
- SPEC 14B：已写入；RAG/SKILL/EVAL/TEST 已按当日命令回填 PASS
- RAG-001：PASS（重建索引、路径沙箱、无 Selenium）
- RAG-002：PASS（BM25+稠密+RRF；别名召回 t2m；非纯向量）
- RAG-003：PASS（只读第九工具契约；默认未注册）
- RAG-004：PASS（检索不能放行非法 CDS 请求）
- RAG-005：PASS（禁止用检索替代 read_context；查询不修 WAL、不写 Context）
- RAG-006：PASS（缺索引 NOT_FOUND / 无命中 MISS；无伪造 hits）
- SKILL-003：PASS（可查询、不可当闸门/进度；不是第五类 action）
- EVAL-005：PASS（knowledge_alias_smoke 单独通过；未并入四场景）
- TEST-008 / Climate collect：312 tests
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：314 passed, 2 skipped（skip = climate_integration）
- Ruff / git diff --check：PASS / 干净
- real_offline 四场景：real_pass_rate=1.0；traces=4（sample_pipeline / cached_inspect / multiturn_recovery / pre_tool_output_guard）
- knowledge_alias_smoke：单独 CLI 通过；1 trace；3 次 climate_query_knowledge；YAML 声明不是 Bench-85 / 不是企业知识库准确率
- 默认工具数量：8；climate_query_knowledge 仅 include_knowledge=True
- baseline 9b592ba diff：空
- QueryEngine diff：空（query.py / query_engine.py）
- blocker/high：无（曾发现非 G6 的 runner observe sidecar 残缺调用，已还原 runner.py）
- PHASE-001：G6 需求 PASS；G6 阶段验收未宣称
- MODEL-001：未重跑（选择 A）；历史 9b592ba / g5-skill 仍有效于当时工具集
- 全量 pytest：未跑（时间不够；Windows 上游失败不计入 Climate / G6）
- 剩余 GAP：真实 Embedding API（刻意不做）；G6 阶段人工总验收；Skill 变更后 Agent 顺序未重新取证
- 是否建议提交：建议用户审阅后另发提交指令。拟提交 G6 源码/测试/Skill/Eval/SPEC/日计划。禁止提交：简历草稿、面试稿、evals/reports、凭证、真实 NetCDF、.part、observe.py（非 G6）
```
