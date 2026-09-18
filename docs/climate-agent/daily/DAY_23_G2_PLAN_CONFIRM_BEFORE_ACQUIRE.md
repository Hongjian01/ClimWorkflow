# Day 23：G2 跟随：plan 确认后再 acquire（A1 最小停顿）

## 为何适合作为 Day 23

Day 21～22 已关闭 retrieve 挂起与 CDS 工具 schema；2026-09-19 本机 TUI 自然语言冷启动新 run `f11d0e28-…` 一次会话 `init → plan → acquire(cds) → inspect → plot → report → validate` 已 `completed`。主路径执行正确，但 **人机改口仍然没有入口**：`full_auto` / 默认 ReAct 下模型 plan 完立刻 `climate_acquire_data`，用户来不及说「先别下、改成 CDS」。

这不是 QueryEngine 把成功算失败，也不是 DAG 防跳步坏了。状态机已经允许「全部业务 step 仍为 `pending` 时整表替换 plan」（A2 窗口），只是 **同一次循环里窗口被立刻关掉**。本日做 **G2 跟随最小停顿**：plan 成功后必须把四步和拟定 `mode` 交给用户；未确认前 acquire 硬拒绝；改口走整表新 plan，不 insert 单个 step。

本日 **不是 Phase G7**，**不是** 工单/审批产品，**不是** 第九工具，**不是** 第五类 `action`，**不是** 改 QueryEngine。不合适的做法才是：做完整 HITL、往 DAG 补丁式加边、在 `full_auto` 里承诺「模型一定不会自己点确认」、或从 `objective` 做口语 NLU。

对照：[ClimateWorkFlow未实现点.md](../../ClimateWorkFlow未实现点.md) A1 / A2；A3（insert step）明确不做。

## 今日目标

在 **不推翻 G0～G6 契约** 的前提下，按冻结顺序做完三件事：

1. 冻结 **DEC-G2-002**，把第 14E 节与需求 ID 写入 `SPEC.md`（先规格、后改状态机）；
2. 落地 P0：未确认前 `climate_acquire_data` 必须失败；确认走现有 `climate_plan_steps`（加 `confirmed`），不新增默认工具；Skill 要求 plan 后把计划发给用户并结束本轮；
3. 回归门闩：同轮 plan+acquire 失败；确认后 acquire 成功；确认前可整表换 plan；任一 acquire 一旦 `running` 不得换 plan（已有行为保持）。

- **SPEC 需求**：DEC-G2-002、PLAN-001、PLAN-002、SKILL-005、TEST-012；PHASE-001 **保持** G4 / G5 / G6 阶段验收 PASS，本日不宣称新阶段
- **预计投入**：4～6 小时（SPEC 14E + 事件/闸门 + Skill + 离线契约测试；**默认不跑**真实 CDS / `real_agent`，除非用户显式允许）
- **完成标志**：DEC-G2-002 已关闭；上述 MUST 有实现或 pytest node ID；CI 仍禁网；未改 QueryEngine；未覆盖历史 baseline json；默认仍八工具
- **上一天**：[Day 22](DAY_22_G5_CDS_TOOL_SCHEMA.md)（G5 跟随：CDS 工具 Schema 前置）
- **下一天**：未排

## 今日原则

- 不修改 QueryEngine 执行语义（`query.py` / `query_engine.py` diff 必须为空）。
- Climate 包 **仍不解析** `objective` 自由文本；「确认 / 改口」发生在用户下一条消息 → 模型再调工具。
- 不把第九工具并入默认 registry；不新增第五类 plan `action`；不做 `climate_ingest_*`。
- 不引入 Selenium / Playwright / GraphRAG / LangChain / Chroma / MCP Server / 联网 Embedding。
- 不执行用户或模型生成的 Python / Shell / `expr`。
- **不得**做成工单、审批流、或 Permission 模式冒充业务确认（`plan` 权限模式 ≠ 本停顿）。
- **不得**在 P0 承诺 `full_auto` 下模型一定停；硬闸门挡住未确认的 acquire，Skill 负责把计划说给用户。`full_auto` 下模型仍可能连点 `confirmed=true`，面试承认这一点。
- 不改写 `evals/baselines/climate-real-9b592ba.json` / `climate-real-g5-skill.json` / `climate-real-g6-skill.json` / `climate-real-g6-knowledge.json`。
- 不读取、不打印、不提交 `.cdsapirc` / API key。
- 不自动提交/推送；人工确认后另发提交指令。
- 历史 `real_agent` / 真实 CDS 本日 **默认不重跑** MODEL-001。
- 不把 Windows TUI 子进程隔离（Day 21 跟随修复，已在 `818387e`）重开需求。

## 顺序（必须按此，不得颠倒）

| 步 | 做什么 | 明确后做 / 不做 |
|---|---|---|
| 0 | 冻结 DEC-G2-002 与错误通道 | 不先改 QueryEngine、不加第九工具 |
| 1 | PLAN-001：未确认 acquire 硬拒绝 | 不改 CDS 下载 / 不改 inspect 子进程 |
| 2 | PLAN-002：`climate_plan_steps.confirmed` 写确认事件；全 pending 可换 plan | 不 insert 单步；不 bump `schema_version` |
| 3 | SKILL-005：plan 后展示四步与 mode，结束本轮 | 不把确认做成 TUI 专用协议 |
| 4 | TEST-012：同轮拒绝、确认后通过、换 plan、已 start 拒绝换 plan | 不把 TUI 人工点确认当 MUST |
| 5 | 离线门闩 | 不跑真实 CDS，除非用户书面允许 |
| 6 | P1 第九工具 / TUI checkpoint UI / `full_auto` 强制停 | **另开日**；本日 P0 不做 |

时间不够：停在第 4 步并在日终标 GAP；不得跳到第 6 步，也不得先做工单。

## 问题对照（2026-09-19 TUI 冷启动，必读）

| 观测 | 含义 | 本日是否修 |
|---|---|---|
| 一句自然语言后模型连续 tool-call 直到 report | 主路径可跑通，但没有审查点 | **是**：未确认不得 acquire |
| `accept_plan` 后 run 已是 `running`，step 全 `pending` | A2 窗口存在，只是没人用 | **是**：确认前窗口保持可换 plan |
| 用户想改 sample→CDS 只能新开 run 或杀进程 | 产品缺口，不是状态机不会换 plan | **是**：确认前整表替换 |
| acquire 一旦 `step_started` | 已有「不得替换 plan」 | **保持** |
| `full_auto` 模型可能自己 `confirmed=true` | 框架不会等人打字 | **承认**；硬闸门仍挡住「没调用确认」的 acquire |
| 第八工具 `climate_validate_artifacts` | 只读验收，不是第五类 action | **保持**；确认不是第九工具 |
| 冷启动 `f11d0e28` 已 completed | 执行层已通 | **不重跑** 真实 CDS 作为本日 MUST |

## 与已交付能力的对照（必读）

| 能力 | Day 05～22 | Day 23 是否做 | 说明 |
|---|---|---|---|
| 四类 action + 8 默认工具 | 已有 | **保持** | 确认是 `climate_plan_steps` 字段，不是新工具 |
| `accept_plan`：initialized→running | 已有 | **保持** | 不把 run 卡在 `initialized` 等人（避免和 resume/read 语义纠缠） |
| 全 pending 可整表换 plan | 已有 | **用起来** | A2 不再只是死代码窗口 |
| 已 start 不得换 plan | 已有 | **保持** | acquire `running`/`succeeded`/`failed` 后仍拒绝 |
| Permission `plan` 模式 | 已有 | **不复用** | 那是写工具确认，不是业务改口 |
| DAG 防跳步 / 幂等 / WAL | 已有 | **保持** | 管执行正确，不管人机改需求——本日只补后半句的入口 |
| QueryEngine | 无 Climate 专用 diff | **保持为空** | 停顿靠工具错误 + Skill，不靠框架 checkpoint |
| 口语 NLU / 从用户「确认」抽槽 | 明确非目标 | **否** | 模型把下一条用户消息变成 `confirmed=true` 或新 `steps` |

## 硬约束（与 G2/G4/G5/G6 DEC 一致，本日追加 DEC-G2-002）

- 不修改 QueryEngine 执行语义。
- 默认 registry 仍恰好 8 个 `climate_*` 工具。
- 不新增第五类 plan `action`。
- 不 bump RunContext `schema_version`（保持 2）：确认用 **事件** 表达，不新增必填字段。
- 错误脱敏：计划摘要不得含 home 路径、token。
- 默认 pytest / CI 禁网。
- acquire 的 CDS 目录闸门、超时、子进程隔离 **不动**。

## 责任边界（编码前必须同意）

| 说法 | 结论 |
|---|---|
| 状态机不会换 plan | **否**。全 pending 时已经可以整表替换 |
| 用户没法改口 | **是产品缺口**。模型 plan 完立刻 acquire，窗口关掉 |
| 应改 QueryEngine 做通用 checkpoint | **否**。ARCH：Climate 不改循环；最小修复在 plan/acquire 闸门 + Skill |
| 应加第九工具 `climate_confirm_plan` | **P0 否**。避免默认工具数膨胀；确认是第二次 `climate_plan_steps` |
| `full_auto` 等于自动替用户确认 | **不得**把这写成功能。硬闸门只保证「没确认事件就不能下」 |
| 改口 = insert 一个 acquire-cds | **否**。A3 明确不做 |

本日修 **ClimWorkflow 计划闸门与 Skill**，不修上游循环、不做工单 UI。

## DEC-G2-002 冻结表（编码前写入 SPEC 第 14E 节）

| 决策 | 冻结值 | 理由 |
|---|---|---|
| 本日性质 | G2 跟随：plan 与 acquire 之间最小停顿；**不是** G7、不是工单日 | A1 主路径交互 |
| run 状态 | plan 后仍为 `running` | 少改 resume/`_require_running`；用事件区分「已计划未确认」 |
| 确认载体 | 事件 `plan_confirmed`，其 `sequence` 必须 **大于** 当前有效的 `plan_created` | 换 plan 后旧确认作废，无需 schema v3 |
| 确认 API | `ClimatePlanStepsInput.confirmed: bool = False`（`extra=forbid` 仍成立） | 不新增工具 |
| 第一次 plan | `confirmed` 缺省或 `false`：写入/替换 DAG + `plan_created`，**不**写 `plan_confirmed`；返回 `step_ids`、各步 `title`/`action`、acquire 步的拟定 `mode`（若有） | 给用户看的摘要来自工具结果，不是 NLU |
| 确认调用 | `confirmed=true`：若 `steps` 与当前 pending plan 拓扑+action 一致，只追加 `plan_confirmed`；若 `steps` 不同且全 pending，先整表替换再确认（一次调用允许「改口并确认」） | 少一轮工具 |
| acquire 闸门 | 当前有效 `plan_created` 之后不存在 `plan_confirmed` → `CLIMATE_INVALID_TRANSITION`，`details.reason=plan_unconfirmed`，`retryable=true` | 不新增错误码；模型可确认后重试 |
| 换 plan | 仅当所有业务 step 仍为 `pending`（已有）；确认前、确认后但尚未 start acquire 都可以换 | A2 入口 |
| 已 start | 保持「不得替换 plan」 | 下载已开始不能改数据源 |
| Skill | plan 成功且未确认：必须用中文列出四步与数据来源（CDS/sample/local），**结束本轮**；禁止同一轮 acquire | 软约束 + 硬闸门 |
| 第九工具 / TUI 按钮 | 本日不做 | P1 |
| 真实 CDS TUI | 默认不作为 MUST | 离线契约足够 |
| 错误码 | 不新增；复用 `CLIMATE_INVALID_TRANSITION` | 少改 envelope 测试面 |

无评审不得改成「第九工具」「改 QueryEngine 等用户 stdin」「insert step」。

## 写入 SPEC 的 MUST（状态先 GAP，回填前不得标 PASS）

- **PLAN-001（MUST，G2 跟随）**：`climate_acquire_data` 在当前 run 上，若最新 `plan_created` 之后没有 `plan_confirmed` 事件，必须失败，不得开始下载、不得写 `.part`。错误码 `CLIMATE_INVALID_TRANSITION`，`details.reason` 必须为 `plan_unconfirmed`，`retryable` 为 true。消息不得含绝对路径或秘密。默认 pytest 不触网。
- **PLAN-002（MUST，G2 跟随）**：`climate_plan_steps` 增加可选布尔 `confirmed`，默认 false。`false`：行为与今日 `accept_plan` 相同，且不得写 `plan_confirmed`。`true`：在「全 pending」前提下，可替换或保持 plan，并追加 `plan_confirmed`。任一 acquire（或其它业务 step）已非 `pending` 时，`confirmed=true` 与换 plan 仍走既有 `CLIMATE_INVALID_TRANSITION`。禁止 bump `schema_version`。禁止第九工具。
- **SKILL-005（MUST，G2 跟随）**：`climate-ds` Skill 与 `TOOL_DESCRIPTIONS["climate_plan_steps"]` 必须写明：先 plan 并展示步骤与 mode，等待用户下一句；未确认不得 acquire。禁止暗示新 action / 新工具名。合同短语测试须覆盖「确认后再下载」或等价冻结用语（写入 `SKILL_CONTRACT_PHRASES` 一条即可）。
- **TEST-012（MUST，G2 跟随）**：离线 pytest（`CLIMATE_INTEGRATION=0`）至少覆盖：（1）plan 后立即 acquire → `plan_unconfirmed`；（2）`confirmed=true` 后 acquire（sample 或假 CDS）可进入既有成功/校验路径；（3）确认前换一组 pending steps 成功，旧确认无效，须再确认；（4）acquire 已 start 后换 plan 仍失败。不得删除既有 plan 替换测试。

## 预期文件

```text
docs/climate-agent/SPEC.md                                              （EXTEND：第 14E 节 DEC-G2-002 与 PLAN-001/002、SKILL-005、TEST-012）
docs/climate-agent/daily/DAY_23_G2_PLAN_CONFIRM_BEFORE_ACQUIRE.md      （本文件）
docs/ClimateWorkFlow未实现点.md                                         （对照 A1/A2；实现后回填「规格已立」即可，不把 A1 标完成除非代码 PASS）
src/openharness/climate/models.py                                       （若需 Event.type 字面量；优先不改 schema_version）
src/openharness/climate/state.py                                        （accept_plan 保持；确认事件；acquire 前检查）
src/openharness/climate/pipeline.py                                     （plan_steps 读 confirmed；acquire_data 闸门）
src/openharness/climate/tools.py                                        （ClimatePlanStepsInput.confirmed）
src/openharness/climate/prompts.py                                      （TOOL_DESCRIPTIONS + SKILL_CONTRACT_PHRASES）
src/openharness/skills/bundled/climate-ds/SKILL.md                      （若仓库路径不同则以实际 Skill 为准）
tests/test_climate/test_state.py / test_pipeline.py / test_tools.py      （EXTEND：TEST-012）
tests/test_skills/test_climate_skill.py                                 （SKILL-005 短语）
```

## 完整操作流程

### 1. 工作区分类（20 分钟）

```powershell
git status --short --branch
git diff --stat
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json evals/baselines/climate-real-g5-skill.json
```

分类：Day 23 拟提交 / 禁止提交（凭证、真实 NetCDF、`.part`、`.climate/`、`evals/reports/`、简历草稿、`package-lock.json`）。

### 2. 需求追踪抽查（40 分钟）

只抽本日 MUST 与 PHASE-001，对照 SPEC 第 16 节 **当场 collect 的 node ID**：

```text
PLAN-001 / PLAN-002 / SKILL-005 / TEST-012 / PHASE-001
```

无当日命令结果不得把上述 MUST 写成 PASS。PHASE-001 保持 G6 阶段验收，不得写 G7。

### 3. 实现（2～3 小时）

按冻结表编码。建议顺序：SPEC 14E（GAP）→ `confirmed` 字段与事件 → acquire 闸门 → Skill 短语 → TEST-012。

acquire 闸门必须在写 `.part` / 调 CDS **之前**。确认事件写入必须走 Repository 锁序，不得绕过 WAL。

### 4. 离线验收矩阵（1～1.5 小时）

必跑：

```powershell
uv run pytest tests/test_climate --collect-only -q
uv run pytest tests/test_climate tests/test_skills/test_climate_skill.py -q
uv run ruff check src tests scripts evals
uv run python -m evals --suite climate --mode real_offline
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json
```

全量 `uv run pytest -q` **建议跑**；Windows 上游失败不计入 Climate。时间不够则日终标明「全量未跑」。

`real_offline` 四场景若因「同轮 plan+acquire」失败：只改 **Eval fixture / 脚本里的工具顺序**（先 confirmed 再 acquire），**禁止**为了保绿而关掉 PLAN-001。改 eval 须在日终写明。历史 baseline json 仍不得覆盖。

### 5. 边界只读审查（30 分钟）

按 blocker / high / medium / low 输出，至少覆盖：

- 默认 registry 仍 8 个 `climate_*`。
- 无 `climate_confirm_plan` 新工具。
- 无第五类 action。
- `query.py` / `query_engine.py` 无 diff。
- `schema_version` 仍为 2。
- Skill 未要求 Selenium / 自写下载脚本。
- `full_auto` 未写成「已保证人工停顿」。

只修 **blocker / high**。禁止借本日加工单 UI。

### 6. MODEL-001 决策（默认不新开跑）

| 选择 | 条件 | 动作 |
|---|---|---|
| **A. 不新开跑（本验收默认）** | 契约测试已覆盖闸门 | 记录历史 baseline 未覆盖 |
| **B. TUI 非 full_auto 点一次** | 用户本回合明确允许模型费用 | 新 run；证明 plan 后停、下一句才 acquire；不覆盖 `9b592ba` |

### 7. 回填 SPEC 与日终（30 分钟）

仅当第 4 步离线矩阵全部通过、第 5 步无未修 blocker：

- PLAN-001 / PLAN-002 / SKILL-005 / TEST-012：填真实路径或 pytest node ID，GAP→PASS
- PHASE-001：**保持** G4 / G5 / G6 阶段验收 PASS；不得写 G7
- 未实现点 A1：可改为「规格 Day 23；实现 PASS 后改建议动作」

无当日命令结果不得标 PASS。

## 验收清单

- [ ] 当日 `git status` 已分类；无凭证/真实数据/`.part`/`evals/reports` 进入拟提交集。
- [ ] `query.py` / `query_engine.py` 无 Climate diff。
- [ ] `evals/baselines/climate-real-9b592ba.json` 无 diff。
- [ ] 默认 registry 仍八工具；无第九确认工具。
- [ ] `schema_version` 仍为 2；确认仅事件。
- [ ] plan 后立即 acquire → `CLIMATE_INVALID_TRANSITION` / `plan_unconfirmed`。
- [ ] `confirmed=true` 后 acquire 可走既有路径。
- [ ] 确认前整表换 plan 成功；旧确认无效。
- [ ] acquire 已 start 后换 plan 仍失败。
- [ ] Skill / 合同短语含「确认后再下载」（或冻结等价句）。
- [ ] `CLIMATE_INTEGRATION=0` 下 Climate + Skill pytest 全绿。
- [ ] Ruff PASS；`git diff --check` 干净。
- [ ] 四场景 `real_offline` 仍能解释通过（若改顺序须写入日终）。
- [ ] PHASE-001 未宣称 G7。
- [ ] 未提交、未推送，除非用户另发指令。

## 今日主 Prompt

```text
执行 ClimWorkflow Day 23：G2 跟随 plan 确认后再 acquire（SPEC 14E + P0）。

先阅读 docs/climate-agent/SPEC.md 第 10、14、15、16 节与
docs/climate-agent/daily/DAY_23_G2_PLAN_CONFIRM_BEFORE_ACQUIRE.md。

硬约束：
- 不修改 QueryEngine；不把第九工具并入默认 registry；
- 不新增第五类 plan action；不 bump schema_version；
- 不引入 Selenium / GraphRAG / LangChain / Chroma / MCP Server；
- Climate 包不解析 objective 自由文本；不做工单 UI；
- 错误码复用 CLIMATE_INVALID_TRANSITION，reason=plan_unconfirmed；
- 不改写 9b592ba / g5-skill / g6-skill / g6-knowledge；
- 不重跑 real_agent / 真实 CDS，除非用户本回合明确要求。

顺序：
1. 分类 git status/diff；确认 engine 与历史 baseline 无 diff；
2. 把 DEC-G2-002 与第 14E 节 MUST 写入 SPEC.md（先 GAP）；
3. plan_steps.confirmed + plan_confirmed 事件；acquire 闸门在写盘/下载之前；
4. Skill + TOOL_DESCRIPTIONS + 合同短语；
5. TEST-012 + 离线门闩；四场景若失败只改 eval 顺序，不关闸门；
6. 按证据回填 SPEC；不宣称 G7；
7. 输出日终报告；不提交、不推送，除非用户明确要求。
```

## 分步骤 Prompt

```text
只做工作区分类与敏感产物扫描；列出拟提交/禁止提交文件，不跑测试、不改代码。
```

```text
只把 DEC-G2-002 与第 14E 节 MUST 写入 SPEC.md，状态 GAP；不改 state.py / tools.py。
```

```text
只实现 PLAN-001 闸门与 PLAN-002 confirmed 事件；不改 Skill、不加第九工具。
```

```text
只做 SKILL-005（Skill + TOOL_DESCRIPTIONS + 合同短语）；不改状态机。
```

```text
只加 TEST-012 并 GREEN；不得删除既有 plan 替换测试。
```

```text
只跑离线门闩并写日终报告；不提交、不推送、不宣称 G7。
```

## 日终报告（模板，实施当日填写）

```text
Day 23：
- 分支 / HEAD / dirty：feat/climworkflow-mvp @ 818387e；ahead 1；dirty=Day 23 实现 + 既有未跟踪（见分类）
- Climate collect：347
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：351 passed / 2 skipped
- Ruff / git diff --check：PASS / 干净
- real_offline 四场景：real_pass_rate=1.0，traces=4（sample_pipeline / cached_inspect / multiturn_recovery / pre_tool_output_guard）。plan 输入加 confirmed: true，未改工具名顺序，未关 PLAN-001 闸门
- TEST-012 node ID：test_tools.py::test_acquire_without_plan_confirm_is_plan_unconfirmed；::test_acquire_after_plan_confirm_sample_succeeds；::test_replace_pending_plan_invalidates_old_confirmation；::test_cannot_replace_plan_after_acquire_started（既有 ::test_plan_cannot_replace_after_business_step_started 保留）
- baseline 9b592ba / g5-skill diff：无
- QueryEngine diff：无
- 默认工具数量：8
- schema_version：2
- blocker/high：无
- PHASE-001：保持 G6；未宣称 G7
- MODEL-001：本验收是否新开跑：否（选择 A）
- 全量 pytest：26 failed, 1477 passed, 13 skipped（失败均为 OpenHarness Windows 上游，不含 Climate / Skill）
- 剩余 GAP：full_auto 自确认；TUI checkpoint UI；第九工具（均非本日 MUST）
- 是否建议提交：拟提交 SPEC 14E、DAY_23、未实现点 A1/A2、state/pipeline/tools/prompts/Skill、TEST-012、eval fixture confirmed。禁止提交：简历、凭证、.climate/、真实 NetCDF、package-lock、evals/reports
```

## 未做时的面试口径（实施前即可用）

> 防跳步、幂等、WAL 管的是执行正确，不管人机改需求。现在 plan 完模型会立刻下载。改数据源要新开 run，或在 acquire 尚未 start 时杀进程再恢复。Day 23 规格是：没确认事件就不能 acquire；改口整表换 plan，不往 DAG 里插一步。
