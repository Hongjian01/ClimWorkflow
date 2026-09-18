# <img src="assets/logo.png" alt="OpenHarness" width="40" style="vertical-align: middle;"> `oh` — OpenHarness 中文说明

> **本 fork 的项目说明是 [ClimWorkflow README](README.md)。** 下面是上游 OpenHarness 的简体中文介绍，不是气候智能体文档。

<p align="center">
  <a href="README.openharness.md"><strong>OpenHarness English</strong></a> ·
  <a href="README.zh-CN.md"><strong>OpenHarness 简体中文</strong></a> ·
  <a href="README.md"><strong>ClimWorkflow</strong></a>
</p>

**OpenHarness** 是一个面向开源社区的 Agent Harness。它提供轻量、可扩展、可检查的 Agent 基础设施，包括：

- Agent loop
- tools / skills / plugins
- memory / session resume
- permissions / hooks
- multi-agent coordination
- provider workflows
- React TUI
- `ohmo` personal-agent app

---

## 最新更新

### Unreleased · Dry-run 安全预览

- 新增 `oh --dry-run`，可以在**不执行模型、不执行工具、不 spawn subagent** 的前提下，预览当前会话会使用的配置、skills、commands、tools 和 MCP 配置。
- Dry-run 会给出 `ready / warning / blocked` 结论，并直接告诉你下一步该做什么，例如先修认证、先修 MCP 配置，或者可以直接运行。
- 对普通 prompt，会给出可能命中的 skills / tools；对 slash command，会展示它更偏只读还是会改本地状态。

### 2026-04-06 · v0.1.2

- 新增统一配置入口 `oh setup`
- provider 配置从“auth -> provider -> model”收敛成 workflow 视角
- Anthropic/OpenAI 兼容接口支持 profile 级凭据，不再强制共用一把全局 key
- 新增 `ohmo` personal-agent app
- `ohmo` 使用 `~/.ohmo` 作为 home workspace，支持 gateway、bootstrap prompts 和交互式 channel 配置

---

## 快速开始

### 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/OpenHarness/main/scripts/install.sh | bash
```

常用安装参数：

- `--from-source`：从源码安装，适合贡献者
- `--with-channels`：一并安装 IM channel 依赖

例如：

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/OpenHarness/main/scripts/install.sh | bash -s -- --from-source --with-channels
```

### 本地运行

```bash
git clone https://github.com/HKUDS/OpenHarness.git
cd OpenHarness
uv sync --extra dev
uv run oh
```

---

## 配置模型与 Provider

现在最推荐的入口是：

```bash
oh setup
```

`oh setup` 会按下面的顺序引导：

1. 选择一个 workflow
2. 如果需要，完成认证
3. 选择具体后端 preset
4. 确认模型
5. 保存并激活 profile

当前内置 workflow 包括：

- `Anthropic-Compatible API`
- `Claude Subscription`
- `OpenAI-Compatible API`
- `Codex Subscription`
- `GitHub Copilot`

### Anthropic-Compatible API

适合这类后端：

- Claude 官方 API
- Moonshot / Kimi
- Zhipu / GLM
- MiniMax
- 其他 Anthropic-compatible endpoint

### OpenAI-Compatible API

适合这类后端：

- OpenAI 官方 API
- OpenRouter
- DashScope
- DeepSeek
- GitHub Models
- SiliconFlow
- Google Gemini
- Groq
- Ollama
- 其他 OpenAI-compatible endpoint

### 常用命令

```bash
# 统一配置入口
oh setup

# 查看已有 workflow/profile
oh provider list

# 切换当前 workflow
oh provider use codex

# 查看认证状态
oh auth status
```

### 高级：添加自定义兼容接口

如果内置 preset 不够，可以直接新增 profile：

```bash
oh provider add my-endpoint \
  --label "My Endpoint" \
  --provider anthropic \
  --api-format anthropic \
  --auth-source anthropic_api_key \
  --model my-model \
  --base-url https://example.com/anthropic
```

这一版开始，兼容接口可以按 profile 绑定凭据。  
也就是说，`Kimi`、`GLM`、`MiniMax` 这类 Anthropic-compatible 后端，不需要再共用一把全局 `anthropic` key。

---

## 交互模式与 TUI

运行：

```bash
oh
```

你会得到 React/Ink TUI，支持：

- `/` 命令选择器
- 交互式权限确认
- `/model` 模型切换
- `/permissions` 权限模式切换
- `/resume` 会话恢复
- `/provider` workflow 选择

非交互模式也支持：

```bash
oh -p "Explain this repository"
oh -p "List all functions in main.py" --output-format json
oh -p "Fix the bug" --output-format stream-json
```

### Dry-run 安全预览

如果你想先看 OpenHarness **会怎么跑**，但又不想真的执行模型或工具，可以用：

```bash
# 预览交互会话本身
oh --dry-run

# 预览一个普通 prompt
oh --dry-run -p "Review this bug fix and grep for failing tests"

# 预览 slash command
oh --dry-run -p "/plugin list"

# 输出结构化 JSON，方便脚本或 channel 使用
oh --dry-run -p "Explain this repository" --output-format json
```

Dry-run 的边界是明确的：

- **不会**调用模型
- **不会**执行 tools
- **不会**启动 subagent
- **不会**连接 MCP server
- **会**解析 settings、auth 状态、system prompt、skills、commands、tools，以及明显错误的 MCP 配置

Readiness 结论说明：

- `ready`：当前配置基本可直接运行
- `warning`：能解析会话，但仍有重要问题需要先处理，比如 MCP 配置错误或后续模型调用缺认证
- `blocked`：按当前状态直接运行会失败，比如 slash command 不存在，或者普通 prompt 无法解析 runtime client

Dry-run 输出里的 `next actions` 会直接给出下一步建议，例如：

- 先执行 `oh auth login`
- 先修或禁用坏掉的 MCP 配置
- 直接运行 `oh -p "..."` 或进入 `oh`

---

## Provider 兼容性概览

OpenHarness 现在把 provider 视为 **workflow + profile**，而不是只暴露底层协议名。

| Workflow | 说明 |
|----------|------|
| `Anthropic-Compatible API` | Anthropic 风格接口，适合 Claude/Kimi/GLM/MiniMax 等 |
| `Claude Subscription` | 复用本地 `~/.claude/.credentials.json` |
| `OpenAI-Compatible API` | OpenAI 风格接口，适合 OpenAI/OpenRouter/各种兼容网关 |
| `Codex Subscription` | 复用本地 `~/.codex/auth.json` |
| `GitHub Copilot` | GitHub Copilot OAuth workflow |

日常推荐用法：

```bash
oh setup
oh provider list
oh provider use <profile>
```

---

## `ohmo` Personal Agent

`ohmo` 是基于 OpenHarness 的 personal-agent app，不是 core 的一个 mode。

### 初始化

```bash
ohmo init
```

这会创建：

- `~/.ohmo/soul.md`
- `~/.ohmo/identity.md`
- `~/.ohmo/user.md`
- `~/.ohmo/BOOTSTRAP.md`
- `~/.ohmo/memory/`
- `~/.ohmo/gateway.json`

其中：

- `soul.md`：长期人格与行为原则
- `identity.md`：`ohmo` 自己是谁
- `user.md`：用户画像、偏好、关系信息
- `BOOTSTRAP.md`：首轮 landing / onboarding ritual
- `memory/`：personal memory
- `gateway.json`：gateway 的 profile 和 channel 配置

### 配置

```bash
ohmo config
```

`ohmo config` 会用和 `oh setup` 一致的 workflow 语言来配置 gateway，例如：

- `Anthropic-Compatible API`
- `Claude Subscription`
- `OpenAI-Compatible API`
- `Codex Subscription`
- `GitHub Copilot`

目前 `ohmo init` / `ohmo config` 已支持引导式配置这些 channel：

- Telegram
- Slack
- Discord
- Feishu

如果 gateway 已经在运行，配置完成后也可以直接选择是否重启。

### 运行

```bash
# 运行 personal agent
ohmo

# 前台运行 gateway
ohmo gateway run

# 查看 gateway 状态
ohmo gateway status

# 重启 gateway
ohmo gateway restart
```

---

## OpenHarness 的核心能力

### Agent Loop

- streaming tool-call cycle
- tool execution / observation / loop
- retry + exponential backoff
- token counting 与成本跟踪

### Tools / Skills / Plugins

- 43+ tools
- Markdown skills 按需加载
- 插件生态
- 兼容 `anthropics/skills`
- 兼容 Claude-style plugins

### Memory / Session

- `CLAUDE.md` 自动发现与注入
- `MEMORY.md` 持久记忆
- session resume
- auto-compact

### Governance

- 多级 permission mode
- path rules
- denied commands
- hooks
- interactive approval

### Multi-Agent

- subagent spawning
- team registry
- task lifecycle
- background task execution

---

## 常见命令

### `oh`

```bash
oh setup
oh provider list
oh provider use codex
oh auth status
oh -p "Explain this codebase"
oh
```

### `ohmo`

```bash
ohmo init
ohmo config
ohmo
ohmo gateway run
ohmo gateway status
ohmo gateway restart
```

---

## 测试

```bash
uv run pytest -q
python scripts/test_harness_features.py
python scripts/test_real_skills_plugins.py
```

---

## 贡献

欢迎贡献：

- tools
- skills
- plugins
- providers
- multi-agent coordination
- tests
- 文档与中文翻译

开发环境：

```bash
git clone https://github.com/HKUDS/OpenHarness.git
cd OpenHarness
uv sync --extra dev
uv run pytest -q
```

更多信息：

- [贡献指南](CONTRIBUTING.md)
- [更新日志](CHANGELOG.md)
- [Showcase](docs/SHOWCASE.md)

---

## ClimWorkflow 离线 Demo（Offline Engineering MVP）

ClimWorkflow 是运行在 OpenHarness 上的可恢复气候数据工作流。Day 10（2026-08-28）
总验收后称谓为 **ClimWorkflow Offline Engineering MVP**。Day 15（2026-09-01）本机
验收 G4（真实 CDS smoke + 固定配置 `real_agent` 3/3）。2026-09-02 fork PR
[#1](https://github.com/Hongjian01/OpenHarness/pull/1) 的 GitHub Actions CI #3
（`52fa338`，[run 33604624255](https://github.com/Hongjian01/OpenHarness/actions/runs/33604624255)）
Python 3.10/3.11 测试、Ruff、Frontend typecheck 全绿。这不是合入上游 HKUDS。

本节先给**离线演示**：真实 Climate 工具，不接入 CDS，不调用真实模型，不要求密钥。
G4 命令单独可选。

架构：

```text
Agent loop（QueryEngine，不改语义）
  → 7 个类型化 Climate 工具（默认 ToolRegistry）
      → pipeline + 版本化状态机
          → ContextRepository（原子写、双层文件锁、WAL）
              → .climate/（index、runs、data、output、locks、事务、备份）
  → Eval：real_offline | synthetic_dry_run | real_agent
  → PRE_TOOL_USE Hook 守卫（execute 之前）
  → G4 可选：cdsapi（外部凭证）+ NetCDF/GRIB reader
Memory / compact 只作导航；磁盘 Context 才是权威状态。
```

### 安装与测试前置

在仓库根目录：

```powershell
uv sync --extra dev
uv run pytest tests/test_climate -q
```

离线 Demo 不需要 API key。除非显式跑 marked CDS 测试，保持 `CLIMATE_INTEGRATION=0`。

### 从空 workspace 执行 sample Demo

不要发明新 CLI。使用已有 `evals.climate.real_offline.run_real_offline`：

```powershell
$ws = Join-Path $env:TEMP "climworkflow-offline-demo"
if (Test-Path $ws) { Remove-Item -Recurse -Force $ws }
New-Item -ItemType Directory -Path $ws | Out-Null

uv run python -c @"
from pathlib import Path
from evals.climate.assertions import evaluate_hard_assertions
from evals.climate.models import load_scenario
from evals.climate.real_offline import run_real_offline

workspace = Path(r'$ws')
scenario = load_scenario(Path('evals/climate/scenarios/sample_pipeline.yaml'))
trace = run_real_offline(scenario, workspace=workspace)
results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
assert all(item.passed for item in results), results
print('status=', trace.final_run_status)
print('run_id=', trace.run_id)
print('version=', trace.final_context_version)
"@
```

预期 `$ws/.climate/`：

```text
.climate/index.json
.climate/runs/<run_id>/context.json
.climate/data/<run_id>/          # sample 数据集
.climate/output/<run_id>/*.png 或 *.svg
.climate/output/<run_id>/report.md
```

`report.md` 含相对图链接，不含绝对 workspace 路径。

local CSV Demo（复制 fixture，不修改源文件）：

```powershell
uv run python -c @"
from pathlib import Path
from evals.climate.assertions import evaluate_hard_assertions
from evals.climate.models import load_scenario
from evals.climate.real_offline import run_real_offline

workspace = Path(r'$ws') / 'local'
workspace.mkdir(parents=True, exist_ok=True)
scenario = load_scenario(Path('evals/climate/scenarios/cached_inspect.yaml'))
trace = run_real_offline(scenario, workspace=workspace)
results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
assert all(item.passed for item in results), results
print('local status=', trace.final_run_status)
"@
```

`cached_inspect` 只执行 local acquire + inspect，最终状态为 `running`（无 plot/report）。
`inputs/` 下的源 CSV 被复制，不被修改。

### 模拟新会话恢复

销毁内存对象后，只凭磁盘 Context 恢复。必须先 `climate_read_context`，不得根据
compact summary 猜测 run/step 已成功：

```powershell
uv run python -c @"
import asyncio, json
from pathlib import Path
from openharness.climate.registry import create_climate_tool_registry
from openharness.tools.base import ToolExecutionContext

ws = Path(r'$ws')

async def main():
    tool = create_climate_tool_registry().get('climate_read_context')
    result = await tool.execute(
        tool.input_model.model_validate({'include_events': True, 'event_limit': 20}),
        ToolExecutionContext(cwd=ws),
    )
    payload = json.loads(result.output)
    data = payload.get('data') or {}
    print('ok=', payload.get('ok'))
    print('status=', data.get('status') or payload.get('status'))
    print('run_id=', payload.get('run_id') or data.get('run_id'))
    print('active_run_id=', data.get('active_run_id'))

asyncio.run(main())
"@
```

### `real_offline` 与 `synthetic_dry_run` 与 `real_agent`

```powershell
uv run python -m evals --suite climate --mode real_offline
uv run python -m evals --suite climate --mode synthetic_dry_run
```

| 模式 | 证明什么 | 计入真实通过率 |
|------|----------|----------------|
| `real_offline` | 真实 Climate 工具，禁网，无模型 | 是 |
| `synthetic_dry_run` | 只验证 scenario 解析、断言 wiring 和报告格式 | 否 |
| `real_agent` | 固定模型 + 真实 Climate 工具 + CDS；需要 `--agent-config` | 仅 3 次运行且 ≥2 次硬断言通过后计入 |

G3 在缺少 `--agent-config` 时仍以 `CLIMATE_DEPENDENCY_MISSING` 拒绝 `real_agent`。
G4 入口（凭证不进仓库）：

```powershell
uv run python -m evals --suite climate --mode real_agent `
  --agent-config evals/configs/climate-real.json `
  --runs 3 `
  --baseline-out evals/baselines/climate-real-<commit>.json
```

### Day 15 实测（2026-09-01，本机 Windows）

下表数字来自本次总验收命令输出，不是估算。Day 10（2026-08-28）G3 历史数字：Climate
198 passed；`real_offline` 4/4，墙钟 10850 ms。

| 命令 | 结果 | 来源 |
|------|------|------|
| `uv run pytest tests/test_climate --collect-only -q` | **258 tests** | Climate collect |
| `uv run pytest tests/test_climate -q`（`CLIMATE_INTEGRATION=0`） | **257 passed, 1 skipped in 124.29s** | Climate 套件 |
| `uv run pytest -m climate_integration tests/test_climate/test_cds.py -q` | **1 passed in 47.18s** | marked CDS smoke |
| `climate-ds` Skill 测试 | 2 passed（含于全量 pytest） | `tests/test_skills/test_climate_skill.py` |
| `uv run pytest -q`（`CLIMATE_INTEGRATION=0`） | 1388 passed, **23 failed**, 12 skipped in 215.36s | 失败均为 OpenHarness Windows POSIX/时区/符号链接/cmd；**0 个 Climate 失败** |
| `uv run ruff check src tests scripts evals` | All checks passed | Ruff |
| `uv run python -m evals --suite climate --mode real_offline` | 4/4 场景，`real_pass_rate=1.0`，墙钟 **47102 ms** | CLI |
| `uv run python -m evals --suite climate --mode synthetic_dry_run` | 标记 SYNTHETIC DRY-RUN；`counts_toward_real_pass_rate=false` | CLI |
| `evals/baselines/climate-real-9b592ba.json` | **passes=3/3**，`min_passes=2`，三次独立 workspace | Day 14 固定配置 baseline |

`real_offline` 场景 Trace（本次运行）：

| 场景 | duration_ms | 最终状态 | 说明 |
|------|------------:|----------|------|
| `sample_pipeline` | 777 | `completed` | 7 工具序列；4 个 artifact（dataset/profile/plot/report） |
| `cached_inspect` | 215 | `running` | local CSV 复制 + inspect 重放；源文件未改 |
| `multiturn_recovery` | 476 | `completed` | 销毁会话后只从磁盘 Context 恢复 |
| `pre_tool_output_guard` | 4641 | `running` | `PRE_TOOL_USE` 阻断 `climate_write_report`；execute=0；`CLIMATE_HOOK_BLOCKED` |

sample 流水线工具耗时（ms）：init 18，plan 48，acquire 54，inspect 56，plot 472，report 84，read 37。

确定性 sample CSV sha256（本次）：`sha256:e85354e49b204f4c45d056a17eb24b9415fdbea2e3ca2a4a762fcf1558e06f22`。

G4 `real_agent`（Day 14，commit `9b592ba`，脏工作区已记录）：模型 `deepseek-v4-pro`，
profile `openai-compatible`，`max_turns=200`，1 天小区域 ERA5 NetCDF，
`allow_sample_fallback=false`。三次耗时 114042 / 78628 / 71389 ms。全部
`requested_mode=cds` / `effective_mode=cds`。修改代码/config/scenario/skill/commit
后三次计数必须重计。

### 已知限制

- G0～G3 离线 Demo 不要求 CDS 或真实模型。不得把 `synthetic_dry_run` 当成真实执行。
- G4 CDS 使用冻结 allowlist（`reanalysis-era5-single-levels` 与冻结变量）。默认
  pytest/CI 禁网（`CLIMATE_INTEGRATION=0`）。
- 不是通用 DAG 调度器、集群或任意 NetCDF/GRIB 科学计算栈。
- workspace 外路径禁止。
- `PRE_TOOL_USE` 可在 `climate_write_report.execute` 前阻断。
- Windows 全量 `pytest -q` 仍有上游 OpenHarness 环境失败；Climate 回归以 `tests/test_climate` 为准。
- 2026-09-02 fork GitHub Actions Python 3.10/3.11、Ruff、Frontend typecheck 已绿（`52fa338`，[run 33604624255](https://github.com/Hongjian01/OpenHarness/actions/runs/33604624255)）。未合入上游 HKUDS。
- 不要提交凭证、`.cdsapirc`、真实 ERA5 下载、`.part`、缓存或 `evals/reports/*.json`。

### 测试命令与常见错误码

```powershell
uv run pytest tests/test_climate -q
uv run pytest tests/test_hooks/test_executor.py tests/test_skills/test_loader.py tests/test_skills/test_climate_skill.py -q
uv run python -m evals --suite climate --mode real_offline
uv run ruff check src tests scripts evals
```

| 错误码 | 含义 |
|--------|------|
| `CLIMATE_INVALID_PATH` | 路径逃逸或写区违规 |
| `CLIMATE_INVALID_INPUT` | schema / 字段错误 |
| `CLIMATE_DEPENDENCY_NOT_READY` | 非法工具顺序 |
| `CLIMATE_HOOK_BLOCKED` | `PRE_TOOL_USE` 已阻断 execute |
| `CLIMATE_DEPENDENCY_MISSING` | 可选依赖缺失，或缺少 `--agent-config` 的 `real_agent` |
| `CLIMATE_IDEMPOTENCY_CONFLICT` | 同 step 不同输入 |
| `CLIMATE_EXTERNAL_TIMEOUT` | 可重试 CDS 超时（最多 3 次） |
| `CLIMATE_EXTERNAL_RATE_LIMIT` | 可重试 CDS 429（最多 3 次） |

Agent 指导见 `.openharness/skills/climate-ds/SKILL.md`。

---

## License

MIT，见 [LICENSE](LICENSE)。
