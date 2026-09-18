"""TEST-004：从空 workspace 真实调用工具的离线纵向切片。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from openharness.climate.errors import ERROR_RETRYABLE
from openharness.climate.models import loads_run_context, loads_workspace_index
from openharness.climate.registry import create_climate_tool_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

RUN_ID = "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314"
OBJECTIVE = "分析示例温度序列并生成报告"

LOCAL_CSV = (
    "date,temperature_c,precipitation_mm\n"
    "2026-02-01,1.5,0.0\n"
    "2026-02-02,2.5,1.0\n"
)

STANDARD_STEPS: list[dict[str, Any]] = [
    {
        "step_id": "acquire",
        "action": "acquire_data",
        "title": "获取数据",
        "depends_on": [],
    },
    {
        "step_id": "inspect",
        "action": "inspect_dataset",
        "title": "检查数据",
        "depends_on": ["acquire"],
    },
    {
        "step_id": "plot",
        "action": "analyze_plot",
        "title": "绘制图表",
        "depends_on": ["inspect"],
    },
    {
        "step_id": "report",
        "action": "write_report",
        "title": "撰写报告",
        "depends_on": ["inspect", "plot"],
    },
]


def _workspace(tmp_path: Path) -> Path:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    return workspace


async def _invoke(tool: BaseTool, workspace: Path, **kwargs: Any) -> tuple[ToolResult, dict[str, Any]]:
    arguments = tool.input_model.model_validate(kwargs)
    result = await tool.execute(arguments, ToolExecutionContext(cwd=workspace))
    payload = json.loads(result.output)
    assert payload["ok"] is (not result.is_error)
    return result, payload


def _assert_failure(payload: dict[str, Any], code: str) -> None:
    assert payload["ok"] is False
    assert payload["error"]["code"] == code
    assert payload["error"]["retryable"] is ERROR_RETRYABLE[code]


@pytest.mark.asyncio
async def test_offline_vertical_slice_from_empty_workspace(tmp_path: Path) -> None:
    """init → plan → acquire(sample) → inspect → plot → report → read，真实写盘。"""
    workspace = _workspace(tmp_path)
    assert not (workspace / ".climate").exists()
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    read = registry.get("climate_read_context")
    assert init and plan and acquire and inspect and plot and report and read

    _, created = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    assert created["ok"] is True
    index = loads_workspace_index((workspace / ".climate" / "index.json").read_text(encoding="utf-8"))
    assert index.active_run_id == RUN_ID
    ctx_path = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    ctx = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    assert ctx.status == "initialized"
    assert ctx.events[0].type == "run_created"

    original = ctx_path.read_bytes()
    _, duplicate = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _assert_failure(duplicate, "CLIMATE_RUN_EXISTS")
    assert ctx_path.read_bytes() == original

    _, planned = await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    assert planned["ok"] is True
    assert planned["data"]["step_ids"] == ["acquire", "inspect", "plot", "report"]
    assert planned["data"]["confirmed"] is True
    ctx = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    assert ctx.status == "running"
    assert len(ctx.steps) == 4

    _, acquired = await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    assert acquired["ok"] is True
    csv_path = workspace / ".climate" / "data" / RUN_ID / "sample.csv"
    raw = csv_path.read_bytes()
    rows = [line for line in raw.decode("utf-8").splitlines() if line]
    assert len(rows) == 31
    assert rows[0] == "date,temperature_c,precipitation_mm"
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert acquired["data"]["sha256"] == digest
    assert acquired["data"]["media_type"] == "text/csv"
    assert acquired["data"]["path"] == f".climate/data/{RUN_ID}/sample.csv"
    assert acquired["data"]["size_bytes"] == len(raw)

    before_stat = csv_path.stat()
    before_hash = digest
    _, inspected = await _invoke(inspect, workspace, step_id="inspect")
    assert inspected["ok"] is True
    assert inspected["data"]["row_count"] == 30
    assert len(inspected["data"]["warnings"]) <= 20
    after_stat = csv_path.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert "sha256:" + hashlib.sha256(csv_path.read_bytes()).hexdigest() == before_hash

    _, plotted = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
        title="示例温度",
    )
    assert plotted["ok"] is True
    plot_rel = plotted["data"]["path"]
    plot_raw = (workspace / plot_rel).read_bytes()
    assert plot_rel.startswith(f".climate/output/{RUN_ID}/")
    if plotted["data"]["media_type"] == "image/png":
        assert plot_raw.startswith(b"\x89PNG\r\n\x1a\n")
        assert plotted["data"]["fallback_reason"] is None
    else:
        assert plotted["data"]["media_type"] == "image/svg+xml"
        assert b"<svg" in plot_raw.lower()
        assert plotted["data"]["fallback_reason"] == "matplotlib_missing"
    assert plotted["data"]["sha256"] == "sha256:" + hashlib.sha256(plot_raw).hexdigest()

    _, reported = await _invoke(
        report,
        workspace,
        step_id="report",
        title="示例气候报告",
        summary="离线 sample 流水线完成。",
    )
    assert reported["ok"] is True
    report_rel = ".climate/output/" + RUN_ID + "/report.md"
    report_text = (workspace / report_rel).read_text(encoding="utf-8")
    assert OBJECTIVE in report_text
    assert "sample" in report_text
    assert plot_rel in report_text
    assert str(workspace) not in report_text
    ctx = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    assert ctx.status == "completed"
    assert any(event.type == "run_completed" for event in ctx.events)

    _, view = await _invoke(read, workspace, include_events=True, event_limit=100)
    assert view["ok"] is True
    encoded = json.dumps(view, ensure_ascii=False)
    assert str(workspace) not in encoded
    assert RUN_ID in encoded
    ctx = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    assert ctx.version == view["context_version"]


@pytest.mark.asyncio
async def test_illegal_order_and_cds_are_stable_errors(tmp_path: Path) -> None:
    """未 init、未 plan、非法顺序、G2 cds 请求给出稳定错误。"""
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    read = registry.get("climate_read_context")
    assert init and plan and acquire and inspect and plot and report and read

    _, unread = await _invoke(read, workspace)
    _assert_failure(unread, "CLIMATE_RUN_NOT_FOUND")

    _, no_run = await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _assert_failure(no_run, "CLIMATE_RUN_NOT_FOUND")

    _, no_acquire = await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    _assert_failure(no_acquire, "CLIMATE_RUN_NOT_FOUND")

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _, before_plan = await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    _assert_failure(before_plan, "CLIMATE_INVALID_TRANSITION")
    assert not (workspace / ".climate" / "data" / RUN_ID / "sample.csv").exists()

    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _, inspect_early = await _invoke(inspect, workspace, step_id="inspect")
    _assert_failure(inspect_early, "CLIMATE_DEPENDENCY_NOT_READY")
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    inspect_step = next(step for step in ctx.steps if step.step_id == "inspect")
    assert inspect_step.status == "pending"

    _, plot_early = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
    )
    _assert_failure(plot_early, "CLIMATE_DEPENDENCY_NOT_READY")
    _, report_early = await _invoke(
        report,
        workspace,
        step_id="report",
        title="示例气候报告",
        summary="过早撰写",
    )
    _assert_failure(report_early, "CLIMATE_DEPENDENCY_NOT_READY")
    assert not (workspace / ".climate" / "output" / RUN_ID / "report.md").exists()

    _, cds = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request={"dataset": "reanalysis-era5-single-levels"},
    )
    _assert_failure(cds, "CLIMATE_INVALID_INPUT")
    assert not (workspace / ".climate" / "data" / RUN_ID / "sample.csv").exists()


@pytest.mark.asyncio
async def test_inspect_rejects_unsafe_path(tmp_path: Path) -> None:
    """含 path 的 inspect 必须再次经过 Climate 路径校验。"""
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    assert init and plan and acquire and inspect

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, workspace, step_id="acquire", mode="sample")

    _, escaped = await _invoke(
        inspect,
        workspace,
        step_id="inspect",
        path="../secret.csv",
    )
    _assert_failure(escaped, "CLIMATE_INVALID_PATH")
    csv_path = workspace / ".climate" / "data" / RUN_ID / "sample.csv"
    assert csv_path.is_file()


@pytest.mark.asyncio
async def test_offline_local_vertical_slice_from_empty_workspace(tmp_path: Path) -> None:
    """init → plan → local acquire → inspect → plot → report → read，真实复制 workspace CSV。"""
    workspace = _workspace(tmp_path)
    assert not (workspace / ".climate").exists()
    source = workspace / "inputs" / "obs.csv"
    source.parent.mkdir()
    source.write_text(LOCAL_CSV, encoding="utf-8", newline="\n")
    source_bytes = source.read_bytes()
    source_mtime = source.stat().st_mtime_ns

    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    read = registry.get("climate_read_context")
    assert init and plan and acquire and inspect and plot and report and read

    _, created = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    assert created["ok"] is True
    _, planned = await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    assert planned["ok"] is True
    _, acquired = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path="inputs/obs.csv"
    )
    assert acquired["ok"] is True
    rel = acquired["data"]["path"]
    dest = workspace / rel
    assert dest.resolve() != source.resolve()
    assert dest.read_bytes() == source_bytes
    assert source.read_bytes() == source_bytes
    assert source.stat().st_mtime_ns == source_mtime
    digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    assert acquired["data"]["sha256"] == digest
    assert acquired["data"]["media_type"] == "text/csv"

    _, inspected = await _invoke(inspect, workspace, step_id="inspect")
    assert inspected["ok"] is True
    assert inspected["data"]["row_count"] == 2
    assert source.stat().st_mtime_ns == source_mtime
    assert dest.read_bytes() == source_bytes

    _, plotted = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
        title="本地温度",
    )
    assert plotted["ok"] is True
    plot_rel = plotted["data"]["path"]
    assert (workspace / plot_rel).is_file()
    assert plot_rel.startswith(f".climate/output/{RUN_ID}/")

    _, reported = await _invoke(
        report,
        workspace,
        step_id="report",
        title="本地气候报告",
        summary="local acquisition 流水线完成。",
    )
    assert reported["ok"] is True
    report_text = (workspace / ".climate" / "output" / RUN_ID / "report.md").read_text(
        encoding="utf-8"
    )
    assert "local" in report_text
    assert plot_rel in report_text
    assert str(workspace) not in report_text

    _, view = await _invoke(read, workspace, include_events=True, event_limit=100)
    assert view["ok"] is True
    encoded = json.dumps(view, ensure_ascii=False)
    assert str(workspace) not in encoded
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    assert ctx.version == view["context_version"]
    assert ctx.status == "completed"
    acquire_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert acquire_step.status == "succeeded"


@pytest.mark.asyncio
async def test_offline_sample_svg_fallback_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sample 流水线在 matplotlib 缺失时生成真实 SVG 并完成 report。"""
    import openharness.climate.pipeline as climate_pipeline

    monkeypatch.setattr(climate_pipeline, "matplotlib_available", lambda: False)
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    read = registry.get("climate_read_context")
    assert init and plan and acquire and inspect and plot and report and read

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    await _invoke(inspect, workspace, step_id="inspect")
    _, plotted = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
        title="示例温度",
    )
    assert plotted["ok"] is True
    assert plotted["data"]["media_type"] == "image/svg+xml"
    raw = (workspace / plotted["data"]["path"]).read_bytes()
    assert b"<svg" in raw.lower()
    assert b"placeholder" not in raw.lower()
    await _invoke(
        report,
        workspace,
        step_id="report",
        title="示例气候报告",
        summary="SVG fallback 流水线完成。",
    )
    _, view = await _invoke(read, workspace)
    assert view["ok"] is True
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    assert ctx.status == "completed"


@pytest.mark.asyncio
async def test_query_engine_path_rules_block_climate_tools_from_default_registry(
    tmp_path: Path,
) -> None:
    """PERM-001：默认 registry 接入后，QueryEngine 按 path 做权限检查，工具不得执行。"""
    from openharness.config.settings import PermissionSettings
    from openharness.engine.query import QueryContext, _execute_tool_call
    from openharness.permissions import PermissionChecker, PermissionMode
    from openharness.tools import create_default_tool_registry

    workspace = _workspace(tmp_path)
    blocked = workspace / "blocked"
    blocked.mkdir()
    (blocked / "secret.csv").write_text(LOCAL_CSV, encoding="utf-8")

    registry = create_default_tool_registry()
    inspect = registry.get("climate_inspect_dataset")
    assert inspect is not None
    executed = {"n": 0}
    original = inspect.execute

    async def _wrapped(arguments: Any, context: ToolExecutionContext) -> ToolResult:
        executed["n"] += 1
        return await original(arguments, context)

    inspect.execute = _wrapped  # type: ignore[method-assign]

    result = await _execute_tool_call(
        QueryContext(
            api_client=object(),  # type: ignore[arg-type]
            tool_registry=registry,
            permission_checker=PermissionChecker(
                PermissionSettings(
                    mode=PermissionMode.FULL_AUTO,
                    path_rules=[{"pattern": str((blocked / "*").resolve()), "allow": False}],
                )
            ),
            cwd=workspace,
            model="claude-test",
            system_prompt="system",
            max_tokens=16,
        ),
        "climate_inspect_dataset",
        "toolu_blocked_inspect",
        {"step_id": "inspect", "path": "blocked/secret.csv"},
    )
    assert result.is_error is True
    assert "matches deny rule" in result.content
    assert executed["n"] == 0
    assert not (workspace / ".climate").exists()


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _cds_request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset": "reanalysis-era5-single-levels",
        "variables": ["2m_temperature"],
        "area": [40.0, 116.0, 39.0, 116.25],
        "date_start": "2025-01-01",
        "date_end": "2025-01-02",
        "format": "netcdf",
    }
    payload.update(overrides)
    return payload


class _FakeCdsClient:
    def __init__(
        self,
        source: Path | None = None,
        *,
        errors: list[BaseException] | None = None,
    ) -> None:
        self.source = source
        self.errors = list(errors or [])
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
        self.calls.append((dataset, dict(request), target))
        if self.errors:
            raise self.errors.pop(0)
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        assert self.source is not None
        path.write_bytes(self.source.read_bytes())


async def _init_plan(workspace: Path) -> Any:
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    assert init and plan
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    return registry


@pytest.mark.asyncio
async def test_mock_cds_netcdf_inspect_plot_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mock CDS NetCDF → inspect → plot → report。"""
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(
        cds_mod, "build_cds_client", lambda: _FakeCdsClient(FIXTURES / "minimal_t2m.nc")
    )
    workspace = _workspace(tmp_path)
    registry = await _init_plan(workspace)
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    assert acquire and inspect and plot and report

    _, acquired = await _invoke(
        acquire, workspace, step_id="acquire", mode="cds", cds_request=_cds_request()
    )
    assert acquired["ok"] is True
    assert acquired["data"]["media_type"] == "application/x-netcdf"

    _, inspected = await _invoke(inspect, workspace, step_id="inspect")
    assert inspected["ok"] is True
    assert inspected["data"]["variables"] == ["t2m"]
    assert inspected["data"]["format"] == "netcdf"

    _, plotted = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="histogram",
        y="t2m",
        title="ERA5 t2m",
    )
    assert plotted["ok"] is True
    plot_rel = plotted["data"]["path"]
    assert (workspace / plot_rel).is_file()

    _, reported = await _invoke(
        report,
        workspace,
        step_id="report",
        title="ERA5 mock 报告",
        summary="NetCDF inspect 流水线完成。",
    )
    assert reported["ok"] is True
    text = (workspace / ".climate" / "output" / RUN_ID / "report.md").read_text(encoding="utf-8")
    assert "t2m" in text
    assert plot_rel in text
    assert str(workspace) not in text
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    assert ctx.status == "completed"


@pytest.mark.asyncio
async def test_mock_cds_timeout_explicit_fallback_then_inspect_plot_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """可重试失败 + 显式 fallback → sample inspect/plot/report。"""
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cds_mod,
        "build_cds_client",
        lambda: _FakeCdsClient(
            errors=[cds_mod.CdsTimeout(), cds_mod.CdsTimeout(), cds_mod.CdsTimeout()]
        ),
    )
    workspace = _workspace(tmp_path)
    registry = await _init_plan(workspace)
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    assert acquire and inspect and plot and report

    _, acquired = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_cds_request(allow_sample_fallback=True),
    )
    assert acquired["ok"] is True
    assert acquired["data"]["requested_mode"] == "cds"
    assert acquired["data"]["effective_mode"] == "sample"
    assert acquired["data"]["fallback_reason"] == "CLIMATE_EXTERNAL_TIMEOUT"

    _, inspected = await _invoke(inspect, workspace, step_id="inspect")
    assert inspected["ok"] is True
    assert inspected["data"]["row_count"] == 30

    _, plotted = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
        title="sample fallback",
    )
    assert plotted["ok"] is True
    _, reported = await _invoke(
        report,
        workspace,
        step_id="report",
        title="fallback 报告",
        summary="显式 sample fallback 流水线完成。",
    )
    assert reported["ok"] is True
    text = (workspace / ".climate" / "output" / RUN_ID / "report.md").read_text(encoding="utf-8")
    assert "sample" in text
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    assert ctx.status == "completed"


@pytest.mark.asyncio
async def test_mock_cds_fail_without_fallback_has_no_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cds_mod,
        "build_cds_client",
        lambda: _FakeCdsClient(
            errors=[cds_mod.CdsTimeout(), cds_mod.CdsTimeout(), cds_mod.CdsTimeout()]
        ),
    )
    workspace = _workspace(tmp_path)
    registry = await _init_plan(workspace)
    acquire = registry.get("climate_acquire_data")
    assert acquire
    _, payload = await _invoke(
        acquire, workspace, step_id="acquire", mode="cds", cds_request=_cds_request()
    )
    _assert_failure(payload, "CLIMATE_EXTERNAL_TIMEOUT")
    data_dir = workspace / ".climate" / "data" / RUN_ID
    assert not (data_dir / "sample.csv").exists()
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    step = next(item for item in ctx.steps if item.step_id == "acquire")
    assert step.status == "failed"


@pytest.mark.asyncio
async def test_format_masquerade_rejects_without_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(
        cds_mod, "build_cds_client", lambda: _FakeCdsClient(FIXTURES / "grib_magic.nc")
    )
    workspace = _workspace(tmp_path)
    registry = await _init_plan(workspace)
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    assert acquire and inspect
    _, payload = await _invoke(
        acquire, workspace, step_id="acquire", mode="cds", cds_request=_cds_request()
    )
    _assert_failure(payload, "CLIMATE_DATA_INVALID")
    data_dir = workspace / ".climate" / "data" / RUN_ID
    assert not data_dir.exists() or list(data_dir.rglob("*")) == []
    _, inspected = await _invoke(inspect, workspace, step_id="inspect")
    _assert_failure(inspected, "CLIMATE_DEPENDENCY_NOT_READY")
    assert not (workspace / ".climate" / "output" / RUN_ID / "profile.json").exists()
