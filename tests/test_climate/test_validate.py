"""VAL-001 / TEST-007：产物规则校验。只读、不改源数据、不执行用户代码。"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openharness.climate.errors import ERROR_RETRYABLE
from openharness.climate.registry import create_climate_tool_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

ROOT = Path(__file__).resolve().parents[2]
VALIDATE_PATH = ROOT / "src" / "openharness" / "climate" / "validate.py"
RUN_ID = "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314"
OBJECTIVE = "分析示例温度序列并生成报告"
STANDARD_STEPS = [
    {"step_id": "acquire", "action": "acquire_data", "title": "获取数据", "depends_on": []},
    {"step_id": "inspect", "action": "inspect_dataset", "title": "检查数据", "depends_on": ["acquire"]},
    {"step_id": "plot", "action": "analyze_plot", "title": "绘制图表", "depends_on": ["inspect"]},
    {
        "step_id": "report",
        "action": "write_report",
        "title": "撰写报告",
        "depends_on": ["inspect", "plot"],
    },
]


def _workspace(tmp_path: Path) -> Path:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


async def _invoke(tool: BaseTool, workspace: Path, **kwargs: object) -> tuple[ToolResult, dict]:
    arguments = tool.input_model.model_validate(kwargs)
    result = await tool.execute(arguments, ToolExecutionContext(cwd=workspace))
    payload = json.loads(result.output)
    assert payload["ok"] is (not result.is_error)
    return result, payload


async def _complete_sample(tmp_path: Path):
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    assert init and plan and acquire and inspect and plot and report
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    await _invoke(inspect, workspace, step_id="inspect")
    await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
        title="示例温度",
    )
    await _invoke(
        report,
        workspace,
        step_id="report",
        title="示例气候报告",
        summary="离线 sample 流水线完成。",
    )
    return workspace, registry


@pytest.mark.asyncio
async def test_validate_sample_pipeline_passes_and_does_not_modify_source(
    tmp_path: Path,
) -> None:
    from openharness.climate.validate import validate_run_artifacts

    workspace, _registry = await _complete_sample(tmp_path)
    data_root = workspace / ".climate" / "data" / RUN_ID
    before = {path.relative_to(data_root).as_posix(): path.read_bytes() for path in data_root.rglob("*") if path.is_file()}
    result = validate_run_artifacts(workspace, run_id=RUN_ID)
    assert result["ok"] is True
    assert result["score"] >= 6
    assert result["score"] <= 10
    after = {path.relative_to(data_root).as_posix(): path.read_bytes() for path in data_root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.asyncio
async def test_validate_tool_is_read_only_and_rejects_code_fields(tmp_path: Path) -> None:
    from openharness.climate.tools import ClimateValidateArtifactsTool

    workspace, _registry = await _complete_sample(tmp_path)
    tool = ClimateValidateArtifactsTool()
    parsed = tool.input_model.model_validate({"run_id": RUN_ID})
    assert tool.is_read_only(parsed) is True
    with pytest.raises(ValidationError):
        tool.input_model.model_validate({"run_id": RUN_ID, "code": "print(1)"})
    with pytest.raises(ValidationError):
        tool.input_model.model_validate({"run_id": RUN_ID, "shell": "rm -rf /"})
    with pytest.raises(ValidationError):
        tool.input_model.model_validate({"run_id": RUN_ID, "expr": "1+1"})
    result, payload = await _invoke(tool, workspace, run_id=RUN_ID)
    assert result.is_error is False
    assert payload["ok"] is True
    assert payload["data"]["ok"] is True
    assert "code" not in tool.input_model.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_validate_missing_report_returns_validation_failed(tmp_path: Path) -> None:
    from openharness.climate.tools import ClimateValidateArtifactsTool

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    assert init and plan and acquire and inspect and plot
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    await _invoke(inspect, workspace, step_id="inspect")
    await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
    )
    tool = ClimateValidateArtifactsTool()
    result, payload = await _invoke(tool, workspace, run_id=RUN_ID)
    assert result.is_error is True
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CLIMATE_VALIDATION_FAILED"
    assert payload["error"]["retryable"] is False
    assert ERROR_RETRYABLE["CLIMATE_VALIDATION_FAILED"] is False
    assert "Traceback" not in payload["error"]["message"]
    assert str(workspace) not in payload["error"]["message"]


@pytest.mark.asyncio
async def test_validate_secret_in_report_fails_and_redacts(tmp_path: Path) -> None:
    from openharness.climate.validate import validate_run_artifacts

    workspace, _registry = await _complete_sample(tmp_path)
    report = workspace / ".climate" / "output" / RUN_ID / "report.md"
    original = report.read_bytes()
    report.write_text(
        report.read_text(encoding="utf-8") + "\napi_key=sk-not-a-real-key\n",
        encoding="utf-8",
    )
    dataset = next((workspace / ".climate" / "data" / RUN_ID).glob("*.csv"))
    before = dataset.read_bytes()
    with pytest.raises(Exception) as exc_info:
        validate_run_artifacts(workspace, run_id=RUN_ID)
    err = exc_info.value
    assert getattr(err, "code", None) == "CLIMATE_VALIDATION_FAILED"
    dumped = json.dumps(err.to_error_object(), ensure_ascii=False)
    assert "sk-not-a-real-key" not in dumped
    assert dataset.read_bytes() == before
    assert report.read_bytes() != original


def test_validate_module_does_not_import_selenium_or_execute_code() -> None:
    import sys

    from openharness.climate import validate as validate_mod

    assert callable(validate_mod.validate_run_artifacts)
    assert "selenium" not in sys.modules
    source = VALIDATE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert "selenium" not in imported
    assert "subprocess" not in imported
    assert not any(isinstance(node, ast.Call) and getattr(node.func, "id", "") == "exec" for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and getattr(node.func, "id", "") == "eval" for node in ast.walk(tree))
