"""ClimWorkflow 命令行：一条命令跑离线 Demo，并从磁盘恢复进度。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from openharness.climate.registry import create_climate_tool_registry
from openharness.tools.base import ToolExecutionContext

app = typer.Typer(
    name="climworkflow",
    help="ClimWorkflow：可恢复的气候数据智能体（离线 Demo 不需要 API Key / CDS）。",
    no_args_is_help=True,
    add_completion=False,
)


def _repo_root() -> Path:
    """从当前目录或源码位置向上找仓库根（需要 evals/climate 场景文件）。"""
    marker = Path("evals") / "climate" / "scenarios" / "sample_pipeline.yaml"
    candidates = [Path.cwd(), *Path.cwd().resolve().parents, Path(__file__).resolve(), *Path(__file__).resolve().parents]
    seen: set[Path] = set()
    for start in candidates:
        for parent in [start, *start.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            if (parent / marker).is_file():
                return parent
    raise typer.BadParameter("请在 ClimWorkflow 仓库根目录运行（找不到 evals/climate/scenarios）。")


def _run_sample_pipeline(workspace: Path) -> tuple[str, str]:
    from evals.climate.assertions import evaluate_hard_assertions
    from evals.climate.models import load_scenario
    from evals.climate.real_offline import run_real_offline

    root = _repo_root()
    workspace.mkdir(parents=True, exist_ok=True)
    scenario = load_scenario(root / "evals" / "climate" / "scenarios" / "sample_pipeline.yaml")
    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    if not all(item.passed for item in results):
        raise typer.Exit(code=1)
    if not trace.run_id or not trace.final_run_status:
        raise typer.Exit(code=1)
    return trace.run_id, trace.final_run_status


def _artifact_paths(workspace: Path, run_id: str) -> tuple[Path, Path | None]:
    output = workspace / ".climate" / "output" / run_id
    report = output / "report.md"
    plots = sorted(output.glob("*.png")) + sorted(output.glob("*.svg"))
    return report, plots[0] if plots else None


@app.command()
def demo(
    workspace: Path = typer.Option(
        Path("climworkflow-demo"),
        "--workspace",
        "-w",
        help="空工作区目录；将写入 .climate/ 数据、图和报告。",
    ),
) -> None:
    """空目录跑 sample_pipeline：获取 → 检查 → 绘图 → 报告。无需密钥。"""
    workspace = workspace.expanduser().resolve()
    run_id, status = _run_sample_pipeline(workspace)
    report, plot = _artifact_paths(workspace, run_id)
    typer.echo(f"status={status}")
    typer.echo(f"run_id={run_id}")
    typer.echo(f"workspace={workspace}")
    typer.echo(f"report={report}")
    if plot is not None:
        typer.echo(f"plot={plot}")
    typer.echo(f"下一步: climworkflow resume --workspace {workspace}")


@app.command()
def resume(
    workspace: Path = typer.Option(
        Path("climworkflow-demo"),
        "--workspace",
        "-w",
        help="已有 .climate/ 的工作区；只读磁盘进度，不猜聊天记录。",
    ),
) -> None:
    """模拟新会话：只调用 climate_read_context。"""
    workspace = workspace.expanduser().resolve()
    index = workspace / ".climate" / "index.json"
    if not index.is_file():
        raise typer.BadParameter(f"未找到 {index}，请先运行 climworkflow demo")

    async def _read() -> dict:
        tool = create_climate_tool_registry().get("climate_read_context")
        if tool is None:
            raise typer.Exit(code=1)
        result = await tool.execute(
            tool.input_model.model_validate({"include_events": True, "event_limit": 20}),
            ToolExecutionContext(cwd=workspace),
        )
        return json.loads(result.output)

    payload = asyncio.run(_read())
    data = payload.get("data") or {}
    typer.echo(f"ok={payload.get('ok')}")
    typer.echo(f"status={data.get('status') or payload.get('status')}")
    typer.echo(f"run_id={payload.get('run_id') or data.get('run_id')}")
    typer.echo(f"active_run_id={data.get('active_run_id')}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
