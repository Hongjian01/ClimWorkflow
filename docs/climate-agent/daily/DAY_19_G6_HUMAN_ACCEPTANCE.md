# Day 19：G6 人工总验收（停止功能开发）

## 今日目标

**停止增加 G6 功能。** 对 Day 18 已回填 PASS 的 RAG-001～006 / SKILL-003 / EVAL-005 / TEST-008 做本机人工总验收，核对 Definition of Done 第 8 条与 G6 额外 DoD，再决定是否宣称 **Phase G6 阶段验收 PASS**。

- **SPEC 需求**：PHASE-001（G6 阶段总验收）、G6 额外 DoD、DEC-G6-001 一致性、历史 G4/G5 baseline 完整性
- **预计投入**：3～5 小时（离线复跑 + 只读审查；真实 CDS / `real_agent` 仅记录既有用户许可证据，本验收默认不新开跑）
- **完成标志**：离线门闩有当日命令结果；G6 边界未被突破；`climate-real-9b592ba.json` 未被改写；SPEC PHASE-001 要么回填「G6 人工验收 PASS」，要么诚实保留「阶段总验收未宣称」
- **上一天**：[Day 18](DAY_18_G6_RAG_KNOWLEDGE_TOOL.md)
- **下一天**：[Day 20](DAY_20_G6_RAG_CORPUS_EVAL.md)（G6 跟随：语料与离线召回评测，不开第九工具）

## 今日原则

- 不新增工具、action、依赖、Selenium、GraphRAG、Chroma、MCP Server、Bench-85、联网 Embedding。
- 不以 mock/synthetic 冒充真实 CDS 或知识检索命中。
- 不读取、不打印、不提交 `.cdsapirc` / API key。
- 不改写历史 `evals/baselines/climate-real-9b592ba.json` / `climate-real-g5-skill.json`。
- 不自动提交/推送；人工确认后另发提交指令。
- 默认 registry 仍为八工具：MODEL-001 **默认不重跑**；既有 `climate-real-g6-skill.json` / `climate-real-g6-knowledge.json` 仅作为用户已许可的旁路证据，不得覆盖历史 json。

## Day 18 已声称、今日必须复核的事实

| 项 | Day 18 声称 | 今日如何证伪/确认 |
|---|---|---|
| Climate collect | 312 tests | `uv run pytest tests/test_climate --collect-only -q`（会话后若增评测测试，以当日数字为准并回填） |
| 默认 pytest | 314 passed / 2 skipped（含 Skill） | `CLIMATE_INTEGRATION=0` 再跑 |
| Ruff | PASS | `uv run ruff check src tests scripts evals` |
| 四场景 `real_offline` | `real_pass_rate=1.0` | 再跑 CLI，确认仍为 4 个核心场景 |
| EVAL-005 场景 | `knowledge_alias_smoke` 未加入默认四场景顺序 | 核 `_REAL_OFFLINE_ORDER` 与 CLI 报告 traces 条数 |
| 第九工具 | 仅 `include_knowledge=True`；默认八工具 | `test_default_registry_excludes_knowledge_tool` + `test_optional_knowledge_tool_does_not_replace_core_eight` |
| 失败码 | `CLIMATE_KNOWLEDGE_NOT_FOUND` / `MISS` / `CORRUPT` | 对应失败测试 |
| 非目标 | 无 Selenium / 无第五类 action / 无代码执行 / 无 Chroma | 源码 import 扫描 + Skill 禁令测试 |
| G4 baseline | 未改写 `climate-real-9b592ba.json` | `git diff` 必须为空 |

## 硬约束（与 DEC-G6-001 一致）

- 不修改 QueryEngine 执行语义。
- 不把第九工具静默并入默认 registry。
- 检索不得绕过 CDS 目录、不得替代 `climate_read_context`。
- 不上 GraphRAG / LangChain / 联网 Embedding；不把 `knowledge_alias_smoke` 写成 Bench-85。
- 真实网络仅用户书面允许后运行 `climate_integration` / `real_agent`；本验收清单 **不把重跑列为 MUST**。

## 完整操作流程

### 1. 工作区分类（20 分钟）

```powershell
git status --short --branch
git diff --stat
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json evals/baselines/climate-real-g5-skill.json
```

分类：G6 拟提交 / 会话评测增量 / 禁止提交（凭证、真实 NetCDF、`.part`、`evals/reports/`、简历草稿）。

### 2. 需求追踪抽查（40 分钟）

只抽 G6 MUST 与 PHASE-001，对照 SPEC 第 16 节 **当场 collect 的 node ID**：

```text
RAG-001～006 / SKILL-003 / EVAL-005 / TEST-008 / PHASE-001
```

无当日命令结果不得把 PHASE-001 写成「G6 阶段验收 PASS」。

### 3. 离线验收矩阵（1.5～2 小时）

必跑：

```powershell
uv run pytest tests/test_climate --collect-only -q
uv run pytest tests/test_climate tests/test_skills/test_climate_skill.py -q
uv run ruff check src tests scripts evals
uv run python -m evals --suite climate --mode real_offline
uv run python -m evals --suite climate --mode synthetic_dry_run
uv run python -m evals --suite climate --mode real_offline --scenario knowledge_alias_smoke
uv run python -m evals --suite climate --mode real_offline --scenario report_quality_smoke
git diff --check
git diff -- evals/baselines/climate-real-9b592ba.json
```

全量 `uv run pytest -q` **建议跑**；Windows 上游失败不计入 Climate / G6。时间不够则日终标明「全量未跑」。

### 4. G6 边界只读审查（45 分钟）

按 blocker / high / medium / low 输出，至少覆盖：

- `knowledge.py` / `ClimateQueryKnowledgeTool` 未 import selenium、playwright、langchain、chromadb。
- `climate_query_knowledge` 输入 `extra=forbid`，仅 `query` + 可选 `top_k`；成功不写 Context。
- 检索命中不能使非法 `cds_request` 通过目录。
- Skill 禁止用检索替代 `climate_read_context` / 目录闸门；不是第五类 action。
- Eval YAML 声明不是 Bench-85 / 不是企业知识库准确率。
- 默认 registry 恰好 8 个 `climate_*` 工具。

只修 **blocker / high**。禁止借验收加功能（含槽位抽取、非气候拒 init）。

### 5. MODEL-001 决策（默认记录既有旁路，不新开跑）

| 选择 | 条件 | 动作 |
|---|---|---|
| **A. 不新开跑（本验收默认）** | 默认仍八工具、不含第九工具 | 记录历史 `9b592ba` / `g5-skill`；若工作区已有用户许可的 `g6-skill` / `g6-knowledge` 则作为旁路证据，不覆盖历史 json |
| **B. 另开 3× `real_agent`** | 用户本回合明确允许网络与模型费用 | 新文件名；禁止覆盖 `9b592ba` |

### 6. 回填 SPEC 与日终（30 分钟）

仅当第 3 步离线矩阵全部通过、第 4 步无未修 blocker：

- PHASE-001：补「Day 19 G6 本机人工总验收 PASS」
- 页首去掉「G6 阶段人工总验收未宣称」
- 无证据则保持 Day 18 表述

## 验收清单

- [x] 当日 `git status` 已分类；无凭证/真实数据/`.part`/`evals/reports` 进入拟提交集。
- [x] `query.py` / `query_engine.py` 无 Climate diff。
- [x] `evals/baselines/climate-real-9b592ba.json` 无 diff。
- [x] Climate collect 与 SPEC TEST-008 当日数字一致（或文档已改正）。
- [x] `CLIMATE_INTEGRATION=0` 下 Climate + Skill pytest 全绿。
- [x] Ruff PASS；`git diff --check` 干净。
- [x] 四场景 `real_offline` `real_pass_rate=1.0`（恰好 4 条核心 traces）。
- [x] `--scenario knowledge_alias_smoke` 通过，且未并入四场景、未冒充 Bench-85。
- [x] 默认 registry 仍八工具；知识工具仅可选注册。
- [x] 无 Selenium / 第五类 action / 代码执行 / 检索绕过目录。
- [x] PHASE-001 已按证据升级或诚实未宣称。
- [x] 未提交、未推送，除非用户另发指令。

## 日终报告（2026-09-08）

```text
Day 19：
- 分支 / HEAD / dirty：feat/climworkflow-mvp @ 8cb1ad6；ahead 1；working tree dirty（G6 实现 + 知识 real_agent 评测增量；未提交）
- Climate collect：317 tests
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：319 passed, 2 skipped（skip = climate_integration）
- Ruff / git diff --check：PASS / 干净
- real_offline 四场景：real_pass_rate=1.0；traces=4（sample_pipeline / cached_inspect / multiturn_recovery / pre_tool_output_guard）；knowledge_alias_smoke 不在 _REAL_OFFLINE_ORDER
- knowledge_alias_smoke：单独 CLI 通过；real_pass_rate=1.0；YAML 声明不是 Bench-85 / 不是企业知识库准确率
- report_quality_smoke：单独 CLI 通过
- synthetic_dry_run：已跑；不计入真实通过率
- baseline 9b592ba / g5-skill diff：空
- QueryEngine diff：空（query.py / query_engine.py）
- 默认工具数量：8；climate_query_knowledge 仅 include_knowledge=True
- blocker/high：无（未借验收加功能）
- medium：开放自然语言仍无「非气候拒 init」（PLAN_PROMPT 要求必须 init）；不在 G6 范围
- PHASE-001：Day 19 G6 本机人工总验收 PASS
- MODEL-001：本验收未新开跑；历史 9b592ba / g5-skill 未覆盖。旁路：g6-skill.json 3/3（cds_minimal_smoke）；g6-knowledge.json 3/3（cds_knowledge_smoke）
- 全量 pytest：未跑（Windows 上游失败不计入 Climate / G6）
- 剩余 GAP：真实 Embedding API（刻意不做）；开放口语槽位抽取 / 非气候拒 init（非 G6）
- 是否建议提交：建议用户审阅后另发提交指令。拟提交 G6 源码/测试/Skill/Eval/SPEC/DAY_18–19，以及 g6-skill / g6-knowledge baseline 与 cds_knowledge_smoke。禁止提交：简历草稿、面试稿、evals/reports、凭证、真实 NetCDF、.part
```
