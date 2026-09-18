# Day 22：G5 跟随：CDS 工具 Schema 前置（减少 acquire 误填重试）

## 为何适合作为 Day 22

Day 20 已关闭 **G6 跟随语料评测**，默认仍不开第九工具。Day 19 日终把「开放口语槽位抽取」标成非 G6 GAP。2026-09-18 本机 TUI（`deepseek-v4-pro` / `full_auto`）用自然语言真实 CDS 任务时，观测到：

- `climate_init_workflow` / `climate_plan_steps` 一次成功（意图已对齐：北京、2 米气温、`mode=cds`、禁止 sample）；
- `climate_acquire_data` 连续 4 次 `CLIMATE_INVALID_INPUT`：`variables` 不在 allowlist、`date_start` 非法 ISO、禁止字段 `product_type`、禁止字段 `time`；
- 根因不是 QueryEngine 坏了，也不是目录闸门失效，而是 **发给模型的工具 JSON Schema 把 `cds_request` 收成任意 object**（`ClimateAcquireDataInput.cds_request: dict[str, Any]`），内层 `CdsRequestInput(extra=forbid)` 的 7 键合同要等 execute 才打回。

Schema 前置从 Day 21 顺延到本日，以便 Day 21 先修 retrieve 收尾挂起。因此本日是 **G5 跟随增量（把已冻结 CDS 合同提前暴露到工具 schema + Skill 样例）**，**不是 Phase G7**，也 **不是** Climate 包内自然语言槽位抽取、默认第九工具、静默丢弃多余字段、或自动把 `t2m` 改写成 `2m_temperature`。不合适的做法才是：把本日写成新阶段、改 QueryEngine、或把门户表单字段当合法入参。

## 今日目标

在 **不推翻 G0～G6 契约** 的前提下，按冻结顺序做完三件事：

1. 冻结 **DEC-G5-002**，把第 14D 节与需求 ID 写入 `SPEC.md`（先规格、后改工具 schema）；
2. 落地 P0：`climate_acquire_data` 的 **API schema** 暴露内层 `CdsRequestInput`（允许键、变量枚举、`additionalProperties: false`）；Skill / 字段说明补一份合法 JSON 与禁止字段；
3. 回归门闩：非法请求仍返回 `CLIMATE_INVALID_INPUT` envelope（含 `field` / `allowed`）；默认仍八工具；CDS-004/005、RAG-004、历史 baseline 不动。

- **SPEC 需求**：DEC-G5-002、SCHEMA-001、SKILL-004、TEST-010；PHASE-001 **保持** G5 / G6 阶段验收 PASS，本日不宣称新阶段
- **预计投入**：4～6 小时（SPEC 14D + schema 注入 + Skill 样例 + 离线契约测试；**默认不跑**真实 CDS / `real_agent`，除非用户显式允许）
- **完成标志**：DEC-G5-002 已关闭；上述 MUST 有实现或 pytest node ID；CI 仍禁网；未改 QueryEngine；未覆盖历史 baseline json；未默认注册第九工具
- **上一天**：[Day 21](DAY_21_G4_CDS_RETRIEVE_TIMEOUT.md)（G4 跟随：retrieve 超时与 .part 稳定发布）
- **下一天**：未排

## 今日原则

- 不修改 QueryEngine 执行语义（`query.py` / `query_engine.py` diff 必须为空）。
- Climate 包 **仍不解析** `objective` 自由文本；自然语言理解只发生在用户→Agent 边界。
- 不把第九工具并入默认 registry；不新增第五类 plan `action`；不做 `climate_ingest_*`。
- 不引入 Selenium / Playwright / GraphRAG / LangChain / Chroma / MCP Server / 联网 Embedding。
- 不执行用户或模型生成的 Python / Shell / `expr`。
- **不得**为减少重试而静默删除 `product_type` / `time` 后当成功。
- **不得**在 P0 做无界别名改写（`t2m` → `2m_temperature` 列为 P1，本日默认不做）。
- 检索命中不得当作 CDS 下载许可；acquire 仍走目录闸门。
- 不改写 `evals/baselines/climate-real-9b592ba.json` / `climate-real-g5-skill.json` / `climate-real-g6-skill.json` / `climate-real-g6-knowledge.json`。
- 不读取、不打印、不提交 `.cdsapirc` / API key。
- 不自动提交/推送；人工确认后另发提交指令。
- 历史 `real_agent` 本日 **默认不重跑** MODEL-001。

## 顺序（必须按此，不得颠倒）

| 步 | 做什么 | 明确后做 / 不做 |
|---|---|---|
| 0 | 冻结 DEC-G5-002 与错误通道 | 不先改 QueryEngine |
| 1 | SCHEMA-001：API schema 注入内层 `CdsRequestInput` | 不改 cdsapi / 下载 / 多候选算法 |
| 2 | SKILL-004：合法 JSON 样例 + 禁止字段 | 不默认打开第九工具 |
| 3 | TEST-010：schema 可见性 + 非法载荷仍 `CLIMATE_INVALID_INPUT` | 不把 QueryEngine 明文校验当唯一错误通道 |
| 4 | 离线门闩 | 不跑真实 CDS，除非用户书面允许 |
| 5 | P1 别名表 / 默认知识工具 / `real_agent` | **另开日或书面许可**；本日 P0 不做 |

时间不够：停在第 3 步并在日终标 GAP；不得跳到第 5 步，也不得先做口语 NLU。

## 问题对照（2026-09-18 TUI，必读）

| 观测 | 含义 | 本日是否修 |
|---|---|---|
| 模型按 CDS 门户填 `product_type` / `time` | 预训练方言 ≠ `CdsRequestInput` 七键 | **是**：schema `additionalProperties: false` + Skill 禁止列表 |
| 变量用 `t2m` 或未登记名 | 目录要 CDS 长名 `2m_temperature`；inspect/plot 才用 GRIB 短名 `t2m` | **是**：schema `variables` 枚举长名；Skill 写明两套名字 |
| 日期非 `YYYY-MM-DD` | 内层 ISO 校验 | **是**：schema 描述/pattern；不写自由日期解析器 |
| 外层 `cds_request` 为 `dict[str, Any]` | 模型在 tool calling 里看不到内层合同 | **是**：注入 `CdsRequestInput.model_json_schema()` |
| 失败后改参再调 | ReAct + 稳定错误码，设计如此 | **保持**：不改成自动填表 |
| 第九工具默认未注册 | 别名检索帮不了本次 acquire | **保持默认关闭**；不是 P0 |

## 与已交付能力的对照（必读）

| 能力 | Day 12～20 | Day 21 是否做 | 说明 |
|---|---|---|---|
| `CdsRequestInput` 七键 + `extra=forbid` | 已有 | **保持** | 不放宽字段 |
| `parse_cds_request` → `CLIMATE_INVALID_INPUT` | 已有 | **保持** | 执行路径错误码不变 |
| 静态目录 / CDS-001～005 | 已有 | **保持** | 不多候选、不改 fallback |
| `ClimateAcquireDataInput.cds_request` 类型 | `dict[str, Any]` | **改 API 可见合同** | 运行时仍把 dict 交给 `parse_cds_request`（见冻结表方案 B） |
| `climate-ds` Skill | 已有「用 `2m_temperature`」一句 | **补完整合法 JSON** | 仍禁止暗示新 action |
| `climate_query_knowledge` | 默认不注册 | **保持** | 不开第十工具 |
| QueryEngine | 无 Climate 专用 diff | **保持为空** | 非法入参若发生在 `input_model.model_validate`，会变成明文 `Invalid input for ...`，故 **禁止** 把内层模型嵌成字段类型当 P0 |
| 口语 NLU / 从 `objective` 抽槽 | 明确非目标 | **否** | G5 边界：Climate 包不解析自由文本 |

## 硬约束（与 DEC-G5-001 / DEC-G4-001 一致，本日追加 DEC-G5-002）

- `cds_request` 允许键仍仅为：`dataset`、`variables`、`area`、`date_start`、`date_end`、`format`、`allow_sample_fallback`。
- `variables` 枚举 = `formats.DATASET_VARIABLES["reanalysis-era5-single-levels"]`，禁止发明未登记 CDS 长名。
- `product_type` / `time` / 凭证字段继续禁止；系统内部仍固定 `reanalysis` 并展开 24 小时。
- 画图合同不变：科学 NetCDF 的 `climate_analyze_plot.y` 仍为 **`t2m`**（GRIB 短名）。Skill 必须把「acquire 长名 / plot 短名」写成对照，避免把 schema 枚举误改成 `t2m`。
- 默认 pytest / CI 禁网；不读 `~/.cdsapirc`。
- 错误码不新增。非法 schema 继续 `CLIMATE_INVALID_INPUT`；目录拒绝继续可走 `CLIMATE_METADATA_REJECTED`（若载荷已通过七键形状）。

## DEC-G5-002 冻结表（编码前写入 SPEC 第 14D 节）

| 决策 | 冻结值 | 理由 |
|---|---|---|
| 本日性质 | G5 跟随：工具 schema 前置 + Skill 样例；**不是** G7、不是 NLU 阶段 | 意图已通，槽位方言不一致 |
| P0 方案 | **方案 B**：`ClimateAcquireDataTool.to_api_schema()`（或等价）把 `cds_request` 的 JSON Schema 换成 `CdsRequestInput.model_json_schema()`；运行时字段仍接受 dict，execute 仍 `parse_cds_request` | 模型看见合同；稳定错误码 envelope 不丢；**不改 QueryEngine** |
| 明确不做的方案 A（P0） | 把 `cds_request: CdsRequestInput` 嵌进 `ClimateAcquireDataInput` | QueryEngine 在 execute 前 `model_validate` 失败会变成明文 `Invalid input for climate_acquire_data`，削弱「稳定错误码改参」 |
| `additionalProperties` | schema 对 `cds_request` 必须为 `false`（与 `extra=forbid` 一致） | 挡住门户多余键 |
| 变量枚举 | schema `variables.items.enum`（或等价）列出 8 个 CDS 长名 | 挡住 `t2m` 当 acquire 变量 |
| Skill 样例 | 一份合法 `cds_request` JSON + 禁止 `product_type`/`time`/`t2m`（作为 variables） | 描述一句不够 |
| 别名改写 | P0 **不做**；P1 才允许冻小表（仅 `t2m`/`2 metre temperature` 等已核对别名 → 长名），仍须目录校验 | 避免静默改科学意图 |
| 丢弃多余键 | **禁止** | 与 `extra=forbid` 相反 |
| 第九工具 | 默认仍不注册 | 检索≠下载许可；且 `t2m` 仍不能当 CDS 变量 |
| QueryEngine / 默认 registry | 不改 | 与 Day 19/20 一致 |
| 真实 CDS / `real_agent` | 默认不重跑 | 离线契约足够证明 schema 前置 |

无评审不得把上表改成「先做 NLU / 先默认九工具 / 先改 QueryEngine」。

建议写入 Skill 的合法样例（日期以用户任务为准；测试可用 `2025-01-01`）：

```json
{
  "dataset": "reanalysis-era5-single-levels",
  "variables": ["2m_temperature"],
  "area": [40.5, 116.0, 39.5, 117.0],
  "date_start": "2025-01-01",
  "date_end": "2025-01-01",
  "format": "netcdf",
  "allow_sample_fallback": false
}
```

对照（必须同时出现在 Skill，禁止只写其中一套）：

| 用途 | 名字 |
|---|---|
| `cds_request.variables` | `2m_temperature`（CDS 长名 / 目录） |
| `climate_analyze_plot.y`（NetCDF histogram） | `t2m`（GRIB 短名 / inspect profile） |
| 禁止出现在 `cds_request` | `product_type`、`time`、凭证字段、`t2m` 当作 variables |

## 写入 SPEC 的 MUST（状态先 GAP，回填前不得标 PASS）

- **SCHEMA-001（MUST，G5 跟随）**：`climate_acquire_data` 经 `BaseTool.to_api_schema()`（或子类覆盖后仍被 registry 使用的那份 schema）暴露的 `cds_request` 必须与 `CdsRequestInput` 合同一致：七键、`variables` 仅目录长名、`additionalProperties: false`。运行时 execute 仍将 dict 交给现有 `parse_cds_request` / 目录校验 / 多候选，不得另写一套下载逻辑。禁止为 schema 放宽 `extra=forbid`。禁止修改 QueryEngine。
- **SKILL-004（MUST，G5 跟随）**：`climate-ds` 与 `FIELD_DESCRIPTIONS["cds_request"]` 必须包含完整合法 `cds_request` JSON 样例，并写明禁止 `product_type` / `time` / 用 `t2m` 当 CDS 变量；同时保留 acquire 长名 vs plot 短名对照。不得暗示第五类 action、任意 Python、Selenium。G5+ 正文仍须包含 `prompts.SKILL_CONTRACT_PHRASES`。
- **TEST-010（MUST，G5 跟随）**：pytest 覆盖：（1）API schema 含 `2m_temperature` 且不含把 `t2m` 列为 `cds_request.variables` 枚举；（2）`cds_request` schema `additionalProperties` 为 false；（3）调用 `climate_acquire_data` 时 `product_type` / `time` / `variables=["t2m"]` 仍得到 `ok=false` 且 `error.code=CLIMATE_INVALID_INPUT`（或已有等价断言），`details.field` 可核对；（4）合法七键 dict 仍能进入现有 mock acquire 路径（不触网）。默认 pytest 禁网。不得删除或放宽 CDS-001～005 / TEST-004 / TEST-007。

错误码不新增。

## 预期文件

```text
docs/climate-agent/SPEC.md                                      （EXTEND：第 14D 节 DEC-G5-002 与 SCHEMA/SKILL/TEST）
docs/climate-agent/daily/DAY_22_G5_CDS_TOOL_SCHEMA.md           （本文件）
src/openharness/climate/tools.py                                （EXTEND：to_api_schema 注入；字段类型默认仍 dict）
src/openharness/climate/prompts.py                              （EXTEND：FIELD_DESCRIPTIONS 样例与禁止字段）
.openharness/skills/climate-ds/SKILL.md                         （EXTEND：合法 JSON + 长名/短名对照）
tests/test_climate/test_tools.py 或 test_cds.py / test_registry.py （EXTEND：TEST-010）
tests/test_skills/test_climate_skill.py                         （EXTEND：SKILL-004 合同短语/样例）
```

先写 SPEC 14D 与冻结表，再改 `to_api_schema`。不得先改 QueryEngine，不得先做别名改写。

## 完整操作流程

### 1. 工作区分类（15 分钟）

```powershell
git status --short --branch
git diff --stat
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json evals/baselines/climate-real-g5-skill.json evals/baselines/climate-real-g6-skill.json evals/baselines/climate-real-g6-knowledge.json
```

分类：Day 22 拟改文件 / 会话前脏文件 / 禁止提交（凭证、真实 NetCDF、`.part`、`evals/reports/`、简历与面试草稿）。

`QueryEngine` 与历史 baseline diff 必须为空。

### 2. 冻结 DEC-G5-002 并写入 SPEC 第 14D 节（30～45 分钟）

编码前必须把冻结表与 SCHEMA-001 / SKILL-004 / TEST-010 写入 `SPEC.md`，状态先标 **GAP**。
第 10.4 节 `cds_request` 说明改为「见第 14 节与第 14D 节」；**不得**把第 14 节七键放宽。
第 15 节只追加「Day 22 G5 跟随：CDS 工具 Schema 前置」，**不得**改成 Phase G7。
PHASE-001 保持 G5 / G6 阶段验收 PASS。

无 14D 不得开始改 `tools.py`。

### 3. SCHEMA-001：API schema 注入（1～1.5 小时）

1. 在 `ClimateAcquireDataTool`（或共享 helper）覆盖 `to_api_schema`：复制 `ClimateAcquireDataInput.model_json_schema()`，将 `properties.cds_request` 替换为 `CdsRequestInput.model_json_schema()`（处理 `$defs` / `$ref`，避免悬空引用）。
2. 断言生成结果：`cds_request.additionalProperties === false`；`variables` 枚举含 `2m_temperature`、**不含** `t2m`。
3. `ClimateAcquireDataInput.cds_request` **保持** `dict[str, Any] | None`，mode 互斥校验不变。
4. `acquire_data` → `parse_cds_request` → 目录 / 多候选路径零逻辑分叉。

禁止：为图省事把字段改成嵌套模型却不处理 QueryEngine 错误通道。

### 4. SKILL-004：样例与禁止列表（30～45 分钟）

1. Skill「规划示例」下追加完整 `cds_request` JSON（可用 `2025-01-01` 与北京小框）。
2. 明确：不要 `product_type`、`time`；variables 不要 `t2m`；plot 的 `y=t2m` 仍正确。
3. `FIELD_DESCRIPTIONS["cds_request"]` 同步，避免工具 description 与 Skill 漂移。
4. `tests/test_skills/test_climate_skill.py` 增加对样例键名 / 禁止字段句子的抽检（不要把整份 JSON 当唯一脆弱匹配，但必须能证明样例存在）。

### 5. TEST-010（1～1.5 小时）

RED→GREEN：先加失败测试（当前 schema 里 `cds_request` 几乎是空 object / 无 enum），再注入 schema。

建议用例（名称以落地 node ID 为准）：

- API schema 暴露目录长名枚举；
- `additionalProperties` 为 false；
- 工具 execute：`product_type` 多余键 → `CLIMATE_INVALID_INPUT` / `field=product_type`；
- `variables=["t2m"]` → `CLIMATE_INVALID_INPUT` / `field=variables` 且 `allowed` 含 `2m_temperature`；
- 合法七键 + mock client 仍成功（复用现有 fake retrieve，不触网）。

不得把「QueryEngine 明文 Invalid input」写成 TEST-010 的成功标准。

### 6. 离线门闩（45～60 分钟）

```powershell
uv run pytest tests/test_climate/test_tools.py tests/test_climate/test_cds.py tests/test_climate/test_registry.py tests/test_skills/test_climate_skill.py -q
uv run pytest tests/test_climate tests/test_skills/test_climate_skill.py -q
uv run ruff check src tests scripts evals
uv run python -m evals --suite climate --mode real_offline
uv run python -m evals --suite climate --mode synthetic_dry_run
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json
```

记录：Climate collect 增量、passed/skipped、四场景仍 4 条、Ruff、engine / baseline 空 diff。

全量 `uv run pytest -q` **建议跑**；Windows 上游失败不计入 Climate。时间不够须在日终标明「全量未跑」。

### 7. P1 / 真实路径（默认跳过）

| 选择 | 条件 | 动作 |
|---|---|---|
| **A. 只做 schema + Skill（默认）** | 本日 P0 | 日终写明：未做别名改写、未默认九工具、未重跑 Agent |
| **B. 冻小别名表** | 用户本回合书面允许 | **另开日**；仅已核对 CDS↔GRIB 对照；仍目录校验；禁止门户多余键改写 |
| **C. 默认注册第九工具** | 用户明确允许 | **另开日**；须重开 registry 契约与 G6 验收；不能替代 SCHEMA-001 |
| **D. 另开 `real_agent` / 真实 CDS TUI** | 用户明确允许网络与费用 | 新 baseline 文件名；禁止覆盖历史 json |

本清单 **不把 B/C/D 列为 MUST**。

### 8. 回填 SPEC 与日终（30 分钟）

仅当第 6 步门闩通过、无未修 blocker：

- SCHEMA-001 / SKILL-004 / TEST-010：填真实路径或 pytest node ID，GAP→PASS。
- PHASE-001：**保持** G5 / G6 阶段验收 PASS；不得写 G7。
- 页首可加「G5 跟随：CDS 工具 Schema 前置（Day 21 需求 PASS/GAP）」须与证据一致。

无当日命令结果不得标 PASS。不得把「TUI 少重试几次」写成无对照的准确率数字。

## 今日主 Prompt

```text
执行 ClimWorkflow Day 22：G5 跟随 CDS 工具 Schema 前置（SPEC 14D + P0）。

先阅读 docs/climate-agent/SPEC.md 第 10.4、14、14A、15、16 节与
docs/climate-agent/daily/DAY_22_G5_CDS_TOOL_SCHEMA.md。

硬约束：
- 不修改 QueryEngine；不把第九工具并入默认 registry；
- 不新增第五类 plan action；不执行任意 Python/Shell；
- 不引入 Selenium / GraphRAG / LangChain / Chroma / MCP Server；
- Climate 包不解析 objective 自由文本；不做无界 NLU；
- 不静默丢弃 product_type/time；P0 不做 t2m→长名改写；
- 不改写 9b592ba / g5-skill / g6-skill / g6-knowledge；
- 不重跑 real_agent / 真实 CDS，除非用户本回合明确要求。

顺序：
1. 分类 git status/diff；确认 engine 与历史 baseline 无 diff；
2. 把 DEC-G5-002 与 SCHEMA/SKILL/TEST 需求写入 SPEC 第 14D 节（先 GAP）；
3. to_api_schema 注入 CdsRequestInput；运行时仍 parse_cds_request；
4. Skill + FIELD_DESCRIPTIONS 合法 JSON 与禁止字段；长名/短名对照；
5. TEST-010 + 离线门闩；四场景仍为 4 条；
6. 按证据回填 SPEC；不宣称 G7；
7. 输出日终报告；不提交、不推送，除非用户明确要求。
```

## 分步骤 Prompt

```text
只做工作区分类与敏感产物扫描；列出拟提交/禁止提交文件，不跑测试、不改代码。
```

```text
只把 DEC-G5-002 与第 14D 节 MUST 写入 SPEC.md，状态 GAP；不改 tools.py、不改 Skill。
```

```text
只实现 SCHEMA-001（to_api_schema 注入）；不改 QueryEngine、不改下载逻辑。
```

```text
只做 SKILL-004（Skill + FIELD_DESCRIPTIONS 样例与禁止字段）；不加别名改写。
```

```text
只加 TEST-010 失败测试并 GREEN；非法载荷必须仍是 CLIMATE_INVALID_INPUT envelope。
```

```text
跑 Day 22 离线门闩，按真实证据回填 SPEC；无证据保持 GAP；不宣称 G7；不跑真实 CDS。
```

## 验收清单

- [x] 当日 `git status` 已分类；无凭证/真实数据/`.part`/`evals/reports`/简历面试稿进入拟提交集。
- [x] `query.py` / `query_engine.py` 无 Climate diff。
- [x] 历史 baseline json（`9b592ba` / `g5-skill` / `g6-skill` / `g6-knowledge`）无拟提交 diff。
- [x] DEC-G5-002 已写入 SPEC 第 14D 节并与实现一致。
- [x] SCHEMA-001：API schema 中 `cds_request` 与 `CdsRequestInput` 一致；`additionalProperties: false`；variables 枚举为 CDS 长名。
- [x] 运行时仍 `parse_cds_request`；未把字段改成嵌套模型而导致 QueryEngine 明文成为唯一错误通道。
- [x] SKILL-004：合法 JSON 样例 + 禁止 `product_type`/`time`/`t2m`-as-variable；acquire 长名 vs plot 短名对照。
- [x] TEST-010：schema 可见性 + 非法载荷 `CLIMATE_INVALID_INPUT` + 合法 mock 成功。
- [x] 默认 registry 仍八工具；第九工具仅 `include_knowledge=True`。
- [x] 无 Selenium / 第五类 action / 代码执行 / 别名静默改写 / 丢弃多余键。
- [x] `CLIMATE_INTEGRATION=0` 下 Climate + Skill pytest 全绿（skip 仅 integration）。
- [x] Ruff PASS；`git diff --check` 干净。
- [x] 四场景 `real_offline` `real_pass_rate=1.0`（恰好 4 条核心 traces）。
- [x] PHASE-001 仍为 G5 / G6 阶段验收 PASS；**未**宣称 G7。
- [x] 未提交、未推送，除非用户另发指令。

## 风险与止损

- 把 `cds_request` 改成嵌套 Pydantic 字段，非法键只在 QueryEngine 明文失败 → 停止，改回方案 B。
- schema 枚举误写成 `t2m` 导致 plot 合同与 acquire 合同对调 → 停止；对照表必须同时出现。
- 为减少重试静默 drop `product_type` → 停止并撤回，与 `extra=forbid` 冲突。
- P0 做无界中文槽位抽取或改 `objective` 解析 → 退回 DEC-G5-001 自然语言边界。
- 默认打开第九工具当「修 acquire」→ 停止；`t2m` 别名检索不能代替目录长名。
- 范围滑向 G7 / Chroma / 真 Embedding / 改 QueryEngine → 退回 DEC-G5-002。
- 时间不够：优先 14D + SCHEMA-001 + TEST-010 的 schema 断言；SKILL-004 可标部分 GAP，不得用未跑 pytest 的「TUI 体感」填日终。
- 离线门闩失败：先定级再修；禁止放宽七键或默认九工具。

## 日终报告模板

```text
Day 22：
- 分支 / HEAD / dirty：
- DEC-G5-002：
- SPEC 14D：已写入 / 未写入
- SCHEMA-001（schema 注入方式；additionalProperties；variables 枚举）：
- SKILL-004：
- TEST-010 / Climate collect：
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：
- Ruff / git diff --check：
- real_offline 四场景：
- 默认工具数量：
- 是否改 QueryEngine：否 / 是（禁止，须说明）
- 是否做别名改写 / 默认九工具 / 真实 CDS：否（默认）
- baseline 9b592ba / g5-skill / g6-* diff：
- QueryEngine diff：
- blocker/high：
- PHASE-001：保持 G5/G6 阶段验收 PASS；未宣称 G7
- 全量 pytest：
- 剩余 GAP：
- 是否建议提交：
```

## 日终报告（执行日填写）

```text
Day 22：
- 分支 / HEAD / dirty：feat/climworkflow-mvp / edb3c14 / dirty（Day 22 拟改文件 + 会话前 package-lock + .climate/ + 简历面试稿）
- DEC-G5-002：已写入第 14D 节并按方案 B 关闭
- SPEC 14D：已写入
- SCHEMA-001（schema 注入方式；additionalProperties；variables 枚举）：ClimateAcquireDataTool.to_api_schema() 注入 CdsRequestInput.model_json_schema() 并补目录 8 个 CDS 长名；cds_request.additionalProperties=false；运行时字段仍 dict[str, Any] | None，execute 仍 parse_cds_request
- SKILL-004：climate-ds 含合法七键 JSON + 禁止 product_type/time/t2m-as-variable；acquire 长名 vs plot 短名对照；FIELD_DESCRIPTIONS["cds_request"] 同步
- TEST-010 / Climate collect：338（Day 21 为 331，+7）
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：341 passed, 2 skipped
- Ruff / git diff --check：PASS / 干净
- real_offline 四场景：real_pass_rate=1.0；traces=4（sample_pipeline / cached_inspect / multiturn_recovery / pre_tool_output_guard）
- 默认工具数量：8（第九工具仅 include_knowledge=True）
- 是否改 QueryEngine：否
- 是否做别名改写 / 默认九工具 / 真实 CDS：否（默认）
- baseline 9b592ba / g5-skill / g6-* diff：空
- QueryEngine diff：空
- blocker/high：无
- PHASE-001：保持 G5/G6 阶段验收 PASS；未宣称 G7
- 全量 pytest：1467 passed, 26 failed, 13 skipped；失败均为 OpenHarness Windows POSIX/时区/shell/swarm，不含 tests/test_climate
- 剩余 GAP：P1 别名表、默认第九工具、real_agent / 真实 CDS TUI（均非本日 MUST）
- 是否建议提交：是（仅 Day 22 拟改文件；排除 package-lock、.climate/、evals/reports、简历面试稿）
```
