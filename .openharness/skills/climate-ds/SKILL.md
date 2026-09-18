---
name: climate-ds
description: >
  ClimWorkflow climate-data workflow: map a natural-language climate goal to
  Plan-Agent / Data-Agent / Coding-Agent roles, then call the 7-tool DAG
  (optional read-only validate after report). Use for ERA5/CDS, sample/local
  CSV, disk Context recovery, and climate reports. Load this skill first.
---

# climate-ds

本 Skill 只提供 Agent 指导，不承载业务实现。工具执行、状态机与产物写入由 Climate
工具完成。提示词合同与 `openharness.climate.prompts` 对齐。

## 论文角色如何落到本系统

ClimateAgent 论文用多智能体写下载脚本和分析代码。ClimWorkflow **复用同一分工，
收窄执行面**：模型只填结构化工具参数，不生成、不执行 Python。

| 论文角色 | 本系统工具 | 你实际做什么 |
|---|---|---|
| Plan-Agent | `climate_init_workflow`、`climate_plan_steps` | 保留用户目标细节，规划四类动作 DAG |
| Data-Agent | `climate_acquire_data` | 填 sample/local/cds 参数；CDS 走静态目录 |
| Coding-Agent (Programming) | `climate_inspect_dataset`、`climate_analyze_plot` | 检查与画图，不写分析脚本 |
| Coding-Agent (Visualization) | `climate_write_report`、`climate_validate_artifacts` | 写报告并做只读规则验收 |
| Orchestrate-Agent | `climate_read_context` | 压缩/中断后读取磁盘进度 |

## 自然语言到四类动作

用户可以用自然语言描述目标。必须把目标写入 `climate_init_workflow.objective`，再规划
结构化 DAG。**保留用户目标中的变量、阈值、时段、区域，不得摘要。** Climate 包不解析
自由文本科学流程。`climate_plan_steps.action` 只能是：

- `acquire_data`
- `inspect_dataset`
- `analyze_plot`
- `write_report`

标准四步：acquire → inspect → plot → report。不得发明第五类 action，不得把 SPI / IVT / TC
等论文子步骤写成新的 plan action。`climate_query_knowledge` 不是 plan action。

### 规划示例

用户：「用 ERA5 看 2025-01-01 北京 2 米气温并出报告。」

1. `climate_init_workflow.objective` 写完整原句（含日期、区域、变量）。
2. plan titles 写成「获取 2025-01-01 北京 2m 气温」「检查该数据集」「绘制 2m 气温直方图」「撰写含图表的报告」，而不是「获取数据」。
3. `climate_plan_steps` 成功后必须用中文列出四步与拟定数据来源（CDS / sample / local），**结束本轮**，等待用户下一句。未确认不得 acquire。
4. 用户确认或改口后，再次调用 `climate_plan_steps`：`confirmed=true`；改口须提交完整新 `steps`（整表替换，不 insert 单步）。**确认后再下载。**
5. CDS 时 `cds_request.variables` 用目录内 CDS 长名（如 `2m_temperature`），不要发明变量，
   也不要用 GRIB 短名 `t2m` 当 acquire 变量。

确认是 `climate_plan_steps` 的 `confirmed` 字段，不是新工具，也不是第五类 action。`full_auto` 下硬闸门仍挡住未确认的 acquire，但不保证模型一定等人打字。

合法 `cds_request` 示例（七键；日期以用户任务为准）：

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

| 用途 | 名字 |
|---|---|
| `cds_request.variables` | `2m_temperature`（CDS 长名 / 目录） |
| `climate_analyze_plot.y`（NetCDF histogram） | `t2m`（GRIB 短名 / inspect profile） |
| 禁止出现在 `cds_request` | `product_type`、`time`、凭证字段、把 `t2m` 当作 variables |

不要向 `cds_request` 填 CDS 门户字段 `product_type` 或 `time`；系统内部固定 `reanalysis` 并展开 24 小时。画图合同不变：科学 NetCDF 的 `y=t2m` 仍正确。

## 七工具顺序

必须按依赖顺序调用，不得跳步：

1. `climate_init_workflow` — 创建或显式 resume run，并切换 active run。
2. `climate_plan_steps` — 校验并持久化 DAG；先展示四步与拟定 mode，等待用户确认。
3. `climate_acquire_data` — 确认后再下载。离线用 sample/local；G4 真实场景用 `mode=cds` 且 `allow_sample_fallback=false`。未确认不得 acquire。
4. `climate_inspect_dataset` — 有界检查，不修改源数据集。
5. `climate_analyze_plot` — 先 PNG，必要时真实 SVG。科学 NetCDF 用 histogram，y=t2m。
6. `climate_write_report` — inspect 与 plot 成功后再写报告。
7. `climate_read_context` — 只读、脱敏、有界的权威 Context 视图。

默认已注册 `climate_validate_artifacts`。建议在 `climate_write_report` 成功后调用，做只读
规则校验。它不是 plan action，不得当成第五类科学步骤；DAG 硬断言不强制调用。

## 文档检索（可选第九工具）

acquire / 写报告前**可以**调用 `climate_query_knowledge`，用文档解释别名、单位与局限
（例如口语「2 米气温」对应文档中的 `t2m`）。默认未注册，不是 DAG 硬步骤。

- 下载前**必须**先通过静态 CDS 元数据目录；检索命中 ≠ 允许下载。
- 中断、压缩或报错后**必须**先 `climate_read_context`；**不得用检索替代 climate_read_context**，
  不得用旧文档猜测当前步骤已成功。
- 禁止把检索写成第五类 action。
- 报告中的数字仍以 inspect profile 为准；文档只提供含义、单位与局限。

## 目录约定（对应论文 data/ 与 code_output/）

- 只有 acquire 写入 `.climate/data/<run_id>/`。
- inspect/plot/report 写入 `.climate/output/<run_id>/`。
- 进度权威源是 `.climate/runs/<run_id>/context.json`。路径一律相对 workspace，禁止本机绝对路径。

## 数据模式选择

- `sample`：无密钥、离线演示 CSV。
- `local`：workspace 内已有 CSV。
- `cds`：真实 ERA5。请求必须落在 **静态 CDS 元数据目录**；系统对合法参数 **最多 3 个候选** 顺序尝试。禁止静默 fallback 到 sample。禁止生成下载脚本。

## Context 合同与遇错先读 Context

磁盘 Context 是权威恢复来源。会话压缩、重启或工具报错后，必须先调用 `climate_read_context`。
不得依赖 compact summary、对话记忆或猜测 run/step 已经成功。同参数重放返回旧结果；已成功
步骤换参数会冲突。失败时根据稳定错误码改参，不要跳步。

## 报告四维（对应论文 Report Score，此处只指导写作）

`climate_write_report.summary` 应覆盖：

- **可读性**：结构清楚，术语正确。
- **科学严谨**：来源、时段、区域、单位、检查结论。
- **完整性**：回应用户目标中的关键要求。
- **可视化质量**：说明图表含义，不要只写「见图」。

这不是 Climate-Agent-Bench-85 的联网打分，只是报告写作合同。

## 凭证安全

禁止把 CDS 凭证、API key、token 或 `.cdsapirc` 写入工具输入、日志或 Context。
错误信息必须脱敏。不要读取 `~/.cdsapirc` 或 `~/.openharness/credentials.json`。

## 禁止事项

- 禁止声称可以增加 SPI / IVT / TC 或其他自由科学 action。
- 禁止建议执行任意 Python、Shell 或生成代码沙箱。
- 禁止 Selenium / 浏览器自动化抓取 CDS 门户。
- 禁止用 `climate_query_knowledge` 代替目录闸门或 `climate_read_context`。
- 禁止把确认做成第九工具或第五类 plan action；未确认不得 acquire，确认后再下载。

## 范围

G0～G3 不调用 CDS，不要求真实模型，只使用离线 sample/local。
G4 真实 Agent baseline 必须走 CDS，禁止静默 fallback 到 sample。
workspace 外路径禁止。本 Skill 不是通用 DAG 调度器。
