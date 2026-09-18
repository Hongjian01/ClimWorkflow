# Day 21：G4 跟随：CDS retrieve 超时、线程卸载与 `.part` 稳定发布

## 今日目标

**停止把「网页 Successful」当成 Agent 成功。** 针对 2026-09-18 TUI 观测：CDS 任务 25 秒已成功、`.part` 已是合法 NetCDF，但 `climate_acquire_data` 一直 Running。本日做 **G4 跟随最小修复**，核对 CDS-002/003 是否被「永不返回的 retrieve」架空。

- **SPEC 需求**：DEC-G4-002、CDS-006、CDS-007、CDS-008、TEST-011；CDS-002/003 **保持**；PHASE-001 **保持** G4/G5/G6 阶段验收 PASS，不宣称 G7
- **预计投入**：5～7 小时（SPEC 14 节跟随段 + adapter/工具卸载 + 假客户端挂起测试 + 离线门闩；**默认不跑**真实 CDS / `real_agent`，除非用户显式允许）
- **完成标志**：DEC-G4-002 已关闭；MUST 有实现或 pytest node ID；假 `retrieve` 永不返回时仍能发布或稳定失败；`query.py` / `query_engine.py` diff 为空；未覆盖历史 baseline
- **上一天**：[Day 20](DAY_20_G6_RAG_CORPUS_EVAL.md)（G6 跟随语料评测 PASS）
- **下一天**：[Day 22](DAY_22_G5_CDS_TOOL_SCHEMA.md)（G5 跟随：工具 Schema 前置，管填表方言，不在本日做）

背景综述：[项目开发遇到的难题.md](../项目开发遇到的难题.md)。

## 今日原则

- 不修改 QueryEngine 执行语义（禁止在 `query.py` 里给所有工具套 `to_thread`）。
- 不以 mock/synthetic 冒充真实 CDS 已通；本日用 **假客户端挂起** 证明超时与稳定发布。
- 不读取、不打印、不提交 `.cdsapirc` / API key / 真实 `.nc`。
- 不改写 `evals/baselines/climate-real-9b592ba.json` / `g5-skill` / `g6-*`。
- 不自动提交/推送。
- 不把第九工具并入默认 registry；不新增第五类 action。
- **不做** Day 22 的 schema 注入（填表问题另日）。
- **不得**把网页 Download 或手改 `.part` 写成 step `succeeded`。
- MODEL-001 / `climate_integration` **默认不重跑**。

## 2026-09-18 已观测、今日必须钉住的事实

| 项 | 观测 | 今日如何证伪/确认 |
|---|---|---|
| pytest 真实 CDS | `2 passed` / 1770s，非 TUI | 本日默认不重跑；承认非 TUI 路径可通 |
| 网页 Successful | 25s、25.26 KB | 与本地 `.part` 大小一致，说明裁切+传输已完成 |
| 本地 `.part` | 25261 B，21:37:47，HDF5 magic | 完成条件应能认磁盘，而不是只认 `retrieve()` 返回 |
| Context | `acquire-era5-t2m` 仍 `running` | 未 `os.replace`、无 `result` |
| 填表四次红 | `t2m` / 日期 / `product_type` / `time` | **本日不修**；Day 22 |
| `resume` | 只读，默认 workspace 不是仓库根 | 不把 resume 改成自动发布 `.part` |
| QueryEngine | `await tool.execute` | **不改**；卸载放在 Climate 工具内 |

## 硬约束（与 DEC-G4-001 一致，本日追加 DEC-G4-002）

- 不修改 QueryEngine 执行语义。
- 下载层仍不 fallback；CDS-004 显式开关不变。
- `.part` + magic + `os.replace` 仍是唯一发布路径（CDS-002）。
- 错误脱敏：超时/挂起日志不得含 URL 查询串、token、home 路径。
- 默认 pytest / CI 禁网。
- 线程卸载仅包裹 **CDS retrieve/下载**，不把整个 QueryEngine 改成线程池。

## 责任边界（编码前必须同意）

| 说法 | 结论 |
|---|---|
| OpenHarness QueryEngine 算错了成功/失败 | **否**。工具没有返回，引擎一直 `await execute` |
| OpenHarness 有缺口 | **有**：`await execute` 不自动卸载阻塞 IO；TUI 与工具共用事件循环 |
| ClimWorkflow 有缺陷 | **有**：`async execute` 内同步无界 `retrieve()`；完成条件绑在客户端返回而非合法 `.part` |
| cdsapi / multiurl | **有**：`iter_content` 在连接不关时可不结束 |

本日修 **ClimWorkflow 气候下载适配**，不修上游循环、不 fork cdsapi。

## DEC-G4-002 冻结表（编码前写入 SPEC 第 14 节跟随段）

| 决策 | 冻结值 | 理由 |
|---|---|---|
| 本日性质 | G4 跟随：超时 + 卸载 + 稳定发布；**不是** G7、不是 Schema 日 | 填对之后仍挂起 |
| 完成条件 | `.part` 非空、magic/扩展名与声称 format 一致、大小在观察窗口内稳定，则校验并 `os.replace` | 网页成功应对齐磁盘 |
| retrieve 墙钟 | 有界（建议默认 180s，可配置上限 ≤ eval `timeout_seconds`）；超时 → `CLIMATE_EXTERNAL_TIMEOUT`，可走 CDS-003 | 永不返回必须变成错误 |
| 卸载 | `ClimateAcquireDataTool.execute`（及直接 `download_cds_dataset` 的同步调用方）用 `asyncio.to_thread` / 等价线程，**禁止改 QueryEngine** | 解开 TUI 事件循环 |
| 假挂起测试 | mock `retrieve`：写满合法 fixture 字节后 `sleep` 超过墙钟 | 不依赖真实 CDS |
| 稳定窗口 | 大小连续不变 ≥ 短窗口（建议 2s）且 magic 合法才发布 | 避免半文件 |
| 中断恢复 | 仍删除未发布 `.part`；**不得**把残留 `.part` 在 `read_context` 里标 succeeded | resume 保持只读 |
| Schema / NLU | 本日不做 | Day 22 |
| 真实 CDS TUI | 默认不作为 MUST | 离线假挂起足够 |

无评审不得改成「改 QueryEngine 给所有工具 to_thread」或「手改 `.part` 当成功」。

## 写入 SPEC 的 MUST（状态先 GAP，回填前不得标 PASS）

- **CDS-006（MUST，G4 跟随）**：真实 `cdsapi.retrieve`（及 adapter）必须有墙钟超时；超时映射 `CLIMATE_EXTERNAL_TIMEOUT`，细节仅 `reason=timeout`，触发 CDS-003 有界重试。不得无限 `iter_content`。默认 pytest 不触网。
- **CDS-007（MUST，G4 跟随）**：`climate_acquire_data` 的 async `execute` 不得在事件循环线程上调用无界同步 retrieve。使用 `asyncio.to_thread`（或文档化的等价物）仅卸载下载。禁止修改 `query.py` / `query_engine.py`。
- **CDS-008（MUST，G4 跟随）**：若目标 `.part` 已满足 CDS-002 的非空/magic/扩展名，且大小在冻结窗口内稳定，即使 retrieve 未返回，也必须校验并原子发布，然后结束本次 acquire 成功路径（或在超时路径上发布后返回成功，不得双发）。半文件不得发布。
- **TEST-011（MUST，G4 跟随）**：pytest 用假客户端：写入 `minimal_t2m.nc` 字节后永不返回或超长 sleep。断言：（1）最终存在正式 `.nc` 或稳定 `CLIMATE_EXTERNAL_TIMEOUT`；（2）超时不得留下半发布 artifact；（3）成功路径无 `.part` 残留。`CLIMATE_INTEGRATION=0`。不得删除 CDS-002/003 既有测试。

错误码不新增。超时复用 `CLIMATE_EXTERNAL_TIMEOUT`。

## 预期文件

```text
docs/climate-agent/SPEC.md                                         （EXTEND：第 14 节 DEC-G4-002 与 CDS-006～008 / TEST-011）
docs/climate-agent/daily/DAY_21_G4_CDS_RETRIEVE_TIMEOUT.md         （本文件）
docs/climate-agent/项目开发遇到的难题.md                           （对照；本日不把填表当已修）
src/openharness/climate/cds.py                                     （EXTEND：超时、稳定发布）
src/openharness/climate/tools.py                                   （EXTEND：to_thread 仅 acquire 下载）
tests/test_climate/test_cds.py                                     （EXTEND：TEST-011 假挂起）
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

分类：Day 21 拟改 / 会话脏文件 / 禁止提交（凭证、真实 NetCDF、`.part`、`evals/reports/`、简历面试稿）。
QueryEngine 与历史 baseline diff 必须为空。

### 2. 需求追踪抽查（30 分钟）

编码前把 DEC-G4-002 与 CDS-006/007/008、TEST-011 写入 SPEC，状态 **GAP**。
对照 CDS-002/003 不得被放宽。
无 14 节跟随段不得改 `cds.py`。

### 3. 实现顺序（2.5～3.5 小时）

1. 假客户端：写 fixture 后 `Event.wait()` / 超长 sleep。  
2. RED：现实现会卡住或测超时失败。  
3. GREEN：墙钟超时；稳定 `.part` 则 CDS-002 发布；`execute` 用 `to_thread`。  
4. 日志脱敏。  

禁止先改 QueryEngine。禁止先做 schema。

### 4. 离线验收矩阵（1.5～2 小时）

```powershell
uv run pytest tests/test_climate/test_cds.py tests/test_climate/test_tools.py -q
uv run pytest tests/test_climate tests/test_skills/test_climate_skill.py -q
uv run ruff check src tests scripts evals
uv run python -m evals --suite climate --mode real_offline
uv run python -m evals --suite climate --mode synthetic_dry_run
git diff --check
git diff -- src/openharness/engine/query.py src/openharness/engine/query_engine.py
git diff -- evals/baselines/climate-real-9b592ba.json
```

全量 `uv run pytest -q` **建议跑**；Windows 上游失败不计入 Climate。时间不够日终标明「全量未跑」。

### 5. 边界只读审查（30 分钟）

blocker / high / medium / low，至少覆盖：

- `query.py` 无 diff。  
- 卸载范围仅 CDS 下载，不是全局线程池。  
- 半文件不发布；resume 仍只读。  
- 未做 schema / 第九工具 / 静默 fallback。  

只修 blocker / high。

### 6. MODEL-001 与真实 TUI（默认不新开跑）

| 选择 | 条件 | 动作 |
|---|---|---|
| **A. 不新开跑（默认）** | 假挂起测试 PASS | 日终写明：未用 TUI 证明死锁消失 |
| **B. 真实 CDS 同步 `acquire_data`** | 用户书面允许 | 可作旁路；禁止覆盖 `9b592ba` |
| **C. 真实 CDS TUI** | 用户允许且 A/B 已过 | 另记；非 MUST |

### 7. 回填 SPEC 与日终（30 分钟）

仅当第 4 步离线矩阵通过、第 5 步无未修 blocker：GAP→PASS，填 node ID。无证据保持 GAP。不得宣称 G7。

## 今日主 Prompt

```text
执行 ClimWorkflow Day 21：G4 跟随 CDS retrieve 超时与 .part 稳定发布。

先阅读 docs/climate-agent/SPEC.md 第 14 节、
docs/climate-agent/项目开发遇到的难题.md、
docs/climate-agent/daily/DAY_21_G4_CDS_RETRIEVE_TIMEOUT.md。

硬约束：
- 不修改 QueryEngine；不改默认 registry；
- 不做 Day 22 schema；不做 NLU；不静默 fallback；
- 不读取凭证；不提交真实 NetCDF / .part；
- 不覆盖 9b592ba / g5-skill / g6-*；
- 默认不跑真实 CDS / real_agent。

顺序：
1. 分类 git status/diff；engine 与 baseline 必须无 diff；
2. DEC-G4-002 与 CDS-006～008 / TEST-011 写入 SPEC（先 GAP）；
3. 假挂起客户端 RED→GREEN：超时或稳定发布；
4. acquire execute 仅卸载下载；
5. 离线门闩；按证据回填；不宣称 G7；
6. 日终报告；不提交除非用户明确要求。
```

## 分步骤 Prompt

```text
只做工作区分类与敏感产物扫描；不改代码、不跑测试。
```

```text
只把 DEC-G4-002 与 CDS-006～008 / TEST-011 写入 SPEC.md，状态 GAP。
```

```text
只加 TEST-011 假挂起失败测试；先不要改 cds.py。
```

```text
只实现 CDS-006/008（超时 + 稳定发布）；不改 QueryEngine、不做 schema。
```

```text
只给 climate_acquire_data.execute 加 to_thread（CDS-007）。
```

```text
跑 Day 21 离线门闩，按证据回填 SPEC；无证据保持 GAP；不跑真实 CDS。
```

## 验收清单

- [x] 当日 `git status` 已分类；无凭证/真实数据/`.part`/`evals/reports` 进入拟提交集。
- [x] `query.py` / `query_engine.py` 无 Climate diff。
- [x] `evals/baselines/climate-real-9b592ba.json` 无 diff。
- [x] DEC-G4-002 已写入 SPEC 并与实现一致。
- [x] CDS-006：retrieve 有墙钟超时 → `CLIMATE_EXTERNAL_TIMEOUT`。
- [x] CDS-007：acquire 下载不在事件循环线程无界阻塞；未改 QueryEngine。
- [x] CDS-008：稳定合法 `.part` 可发布；半文件不发布。
- [x] TEST-011：假挂起 pytest 覆盖成功发布或稳定超时。
- [x] 未做 schema 注入 / 第九工具 / 静默 drop 门户字段。
- [x] `CLIMATE_INTEGRATION=0` 下 Climate + Skill pytest 全绿（skip 仅 integration）。
- [x] Ruff PASS；`git diff --check` 干净。
- [x] 四场景 `real_offline` `real_pass_rate=1.0`（恰好 4 条核心 traces）。
- [x] PHASE-001 仍为既有阶段验收 PASS；**未**宣称 G7。
- [x] 未提交、未推送，除非用户另发指令。

## 风险与止损

- 在 QueryEngine 给全部工具 `to_thread` → 停止，缩回 Climate acquire。  
- 稳定窗口过短发布半文件 → 停止，加长窗口并补测试。  
- 超时后既发布又重试导致双文件/冲突 → 停止，明确成功与超时互斥。  
- 把 Day 22 schema 塞进本日 → 停止。  
- 用一次 TUI 体感当 PASS、无 pytest → 禁止回填。  
- 离线门闩失败：先定级；禁止放宽 CDS-002 或默认九工具。

## 日终报告模板

```text
Day 21：
- 分支 / HEAD / dirty：
- DEC-G4-002：
- SPEC 跟随段：已写入 / 未写入
- CDS-006 / 007 / 008：
- TEST-011 / Climate collect：
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：
- Ruff / git diff --check：
- real_offline 四场景：
- QueryEngine diff：必须为空
- 是否做 schema / 真实 CDS TUI：否（默认）
- baseline 9b592ba diff：
- blocker/high：
- PHASE-001：保持既有阶段 PASS；未宣称 G7
- 全量 pytest：
- 剩余 GAP：
- 是否建议提交：
```

## 日终报告（执行日填写）

```text
Day 21：
- 分支 / HEAD / dirty：feat/climworkflow-mvp / 5c30313；dirty 含本日 SPEC/cds.py/tools.py/test_cds.py/DAY_21，以及会话脏文件（简历、面试稿、.climate/、package-lock、DAY_20 一行、DAY_22 计划）
- DEC-G4-002：已写入第 14 节并关闭；与实现一致
- SPEC 跟随段：已写入
- CDS-006 / 007 / 008：PASS（假挂起 pytest node ID 已回填）
- TEST-011 / Climate collect：331 tests；::test_hanging_retrieve_publishes_stable_valid_part / ::test_hanging_retrieve_without_part_is_stable_timeout / ::test_hanging_retrieve_half_file_is_not_published / ::test_acquire_execute_offloads_hanging_cds_download
- Climate+Skill pytest（CLIMATE_INTEGRATION=0）：333 passed / 2 skipped（仅 climate_integration）
- Ruff / git diff --check：PASS / 干净
- real_offline 四场景：real_pass_rate=1.0，traces=4（sample_pipeline / cached_inspect / multiturn_recovery / pre_tool_output_guard）；synthetic_dry_run 已跑、不计入 real_pass_rate
- QueryEngine diff：空
- 是否做 schema / 真实 CDS TUI：否（默认；选择 A，未用 TUI 证明死锁消失）
- baseline 9b592ba / g5-skill / g6-* diff：空
- blocker/high：无。medium：真实 TUI 路径未新开跑。low：全量 pytest 既有 Windows 上游失败
- PHASE-001：保持既有阶段 PASS；未宣称 G7
- 全量 pytest：1459 passed, 26 failed, 13 skipped；失败均为 OpenHarness Windows POSIX/时区/符号链接/cmd/swarm，不含 Climate
- 剩余 GAP：Day 22 Schema / NLU；真实 Embedding；真实 CDS TUI 非 MUST
- 是否建议提交：建议用户审阅后另发提交指令。拟提交：SPEC 第 14 节跟随段、DAY_21、cds.py、tools.py、test_cds.py。禁止提交：凭证、真实 NetCDF/.part、.climate/、evals/reports、简历面试稿、package-lock、DAY_22 未实现计划可另议
```
