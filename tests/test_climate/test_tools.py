"""TOOL-* / ERR-001 / PERM-001 / TEST-004：五个 Climate 工具的真实副作用契约。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openharness.climate.errors import ERROR_RETRYABLE
from openharness.climate.models import loads_run_context, loads_workspace_index
from openharness.climate.registry import create_climate_tool_registry
from openharness.climate.repository import ContextRepository
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

RUN_ID = "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314"
RUN_ID_B = "1f9f7fc5-a4e3-4df8-9e33-a2b39fb0a425"
OBJECTIVE = "分析示例温度序列并生成报告"

LOCAL_CSV = (
    "date,temperature_c,precipitation_mm\n"
    "2026-02-01,1.5,0.0\n"
    "2026-02-02,2.5,1.0\n"
)

LOCAL_DEP_STEPS: list[dict[str, Any]] = [
    {
        "step_id": "prep",
        "action": "acquire_data",
        "title": "准备样本",
        "depends_on": [],
    },
    {
        "step_id": "acquire",
        "action": "acquire_data",
        "title": "导入本地 CSV",
        "depends_on": ["prep"],
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
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


async def _invoke(tool: BaseTool, workspace: Path, **kwargs: Any) -> tuple[ToolResult, dict[str, Any]]:
    arguments = tool.input_model.model_validate(kwargs)
    result = await tool.execute(arguments, ToolExecutionContext(cwd=workspace))
    payload = json.loads(result.output)
    assert payload["ok"] is (not result.is_error)
    return result, payload


def _assert_success_envelope(payload: dict[str, Any]) -> None:
    assert payload["ok"] is True
    assert "data" in payload and isinstance(payload["data"], dict)
    assert payload["run_id"]
    assert isinstance(payload["context_version"], int)
    assert "error" not in payload


def _assert_failure_envelope(payload: dict[str, Any], code: str) -> None:
    assert payload["ok"] is False
    error = payload["error"]
    assert error["code"] == code
    assert error["retryable"] is ERROR_RETRYABLE[code]
    assert isinstance(error["message"], str)
    assert "Traceback" not in error["message"]
    assert isinstance(error["details"], dict)


def _context_path(workspace: Path, run_id: str = RUN_ID) -> Path:
    return workspace / ".climate" / "runs" / run_id / "context.json"


def _file_tree_bytes(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot


@pytest.mark.asyncio
async def test_all_tools_use_shared_contracts(tmp_path: Path) -> None:
    """TOOL-BASE-001：已实现工具均为 BaseTool，extra=forbid，统一 JSON envelope。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    for tool in registry.list_tools():
        assert isinstance(tool, BaseTool)
        assert tool.input_model.model_config.get("extra") == "forbid"
        schema = tool.to_api_schema()
        assert schema["name"] == tool.name
        assert schema["input_schema"].get("additionalProperties") is False

    init = registry.get("climate_init_workflow")
    assert init is not None
    _, payload = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _assert_success_envelope(payload)
    raw = _context_path(workspace).read_text(encoding="utf-8")
    assert raw.startswith("{")
    assert raw.endswith("}\n")
    assert json.dumps(json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True) + "\n" == raw
    ctx = loads_run_context(raw)
    assert ctx.run_id == RUN_ID
    assert ctx.status == "initialized"


@pytest.mark.asyncio
async def test_all_tool_results_match_error_envelope(tmp_path: Path) -> None:
    """ERR-001：成功/失败 ToolResult 均符合统一 envelope，且 is_error 与 ok 互反。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    acquire = registry.get("climate_acquire_data")
    plot = registry.get("climate_analyze_plot")
    report = registry.get("climate_write_report")
    assert init and acquire and plot and report

    _, ok_payload = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _assert_success_envelope(ok_payload)

    result, fail_payload = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    assert result.is_error is True
    _assert_failure_envelope(fail_payload, "CLIMATE_RUN_EXISTS")

    result, missing = await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    assert result.is_error is True
    _assert_failure_envelope(missing, "CLIMATE_INVALID_TRANSITION")

    result, plot_missing = await _invoke(
        plot, workspace, step_id="plot", chart_type="line", x="date", y="temperature_c"
    )
    assert plot_missing["ok"] is False
    assert result.is_error is True
    _assert_failure_envelope(plot_missing, "CLIMATE_INVALID_TRANSITION")

    result, report_missing = await _invoke(
        report, workspace, step_id="report", title="示例气候报告", summary="摘要"
    )
    assert result.is_error is True
    _assert_failure_envelope(report_missing, "CLIMATE_INVALID_TRANSITION")


@pytest.mark.asyncio
async def test_init_create_duplicate_and_resume(tmp_path: Path) -> None:
    """TOOL-INIT-001：新建、重复 run_id 拒绝且不覆盖、显式 orphan resume。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    assert init is not None

    _, created = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _assert_success_envelope(created)
    assert created["data"]["context_path"] == f".climate/runs/{RUN_ID}/context.json"
    assert created["data"]["status"] == "initialized"
    original = _context_path(workspace).read_bytes()
    index = loads_workspace_index((workspace / ".climate" / "index.json").read_text(encoding="utf-8"))
    assert index.active_run_id == RUN_ID

    _, duplicate = await _invoke(init, workspace, objective="另一个目标", run_id=RUN_ID)
    _assert_failure_envelope(duplicate, "CLIMATE_RUN_EXISTS")
    assert _context_path(workspace).read_bytes() == original
    assert loads_run_context(original.decode("utf-8")).objective == OBJECTIVE

    orphan = ContextRepository(workspace).save_run(
        loads_run_context(original.decode("utf-8")).model_copy(
            update={"run_id": RUN_ID_B, "objective": "orphan 目标"}
        ),
        expected_version=None,
    )
    assert orphan.run_id == RUN_ID_B
    _, resumed = await _invoke(init, workspace, resume_run_id=RUN_ID_B)
    _assert_success_envelope(resumed)
    assert resumed["run_id"] == RUN_ID_B
    index_after = loads_workspace_index(
        (workspace / ".climate" / "index.json").read_text(encoding="utf-8")
    )
    assert index_after.active_run_id == RUN_ID_B

    with pytest.raises(ValidationError):
        init.input_model.model_validate(
            {"objective": OBJECTIVE, "run_id": RUN_ID, "resume_run_id": RUN_ID_B}
        )
    with pytest.raises(ValidationError):
        init.input_model.model_validate({"resume_run_id": RUN_ID_B, "objective": OBJECTIVE})


@pytest.mark.asyncio
async def test_plan_validates_dag_and_is_atomic(tmp_path: Path) -> None:
    """TOOL-PLAN-001：合法四步 DAG 一次 mutation 持久化；非法 DAG 不留下部分 steps。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    assert init and plan

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    before = _context_path(workspace).read_bytes()
    cyclic = [
        {
            "step_id": "acquire",
            "action": "acquire_data",
            "title": "获取数据",
            "depends_on": ["report"],
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
            "depends_on": ["plot"],
        },
    ]
    _, bad = await _invoke(plan, workspace, steps=cyclic)
    _assert_failure_envelope(bad, "CLIMATE_INVALID_INPUT")
    assert _context_path(workspace).read_bytes() == before
    assert loads_run_context(before.decode("utf-8")).steps == []

    shuffled = [STANDARD_STEPS[2], STANDARD_STEPS[0], STANDARD_STEPS[3], STANDARD_STEPS[1]]
    _, ok = await _invoke(plan, workspace, steps=shuffled)
    _assert_success_envelope(ok)
    assert ok["data"]["step_ids"] == ["acquire", "inspect", "plot", "report"]
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.status == "running"
    assert [step.step_id for step in ctx.steps] == ["acquire", "inspect", "plot", "report"]
    assert [step.status for step in ctx.steps] == ["pending"] * 4
    assert any(event.type == "plan_created" for event in ctx.events)

    version = ctx.version
    _, again = await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _assert_success_envelope(again)
    ctx2 = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx2.status == "running"
    assert ctx2.version == version + 1
    assert [step.status for step in ctx2.steps] == ["pending"] * 4
    assert sum(1 for event in ctx2.events if event.type == "plan_created") == 2
    assert not any(event.type == "plan_confirmed" for event in ctx2.events)


def _clone_steps(*overrides: dict[str, Any]) -> list[dict[str, Any]]:
    cloned = [dict(step) for step in STANDARD_STEPS]
    by_id = {step["step_id"]: step for step in cloned}
    for item in overrides:
        target = by_id[item["step_id"]]
        target.update(item)
    return cloned


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "steps",
    [
        _clone_steps({"step_id": "inspect", "action": "acquire_data"}),
        [
            {**STANDARD_STEPS[0], "step_id": "dup"},
            {**STANDARD_STEPS[1], "step_id": "dup", "depends_on": []},
            STANDARD_STEPS[2],
            {**STANDARD_STEPS[3], "depends_on": ["dup", "plot"]},
        ],
        _clone_steps({"step_id": "inspect", "depends_on": ["missing"]}),
        _clone_steps({"step_id": "acquire", "depends_on": ["acquire"]}),
        _clone_steps({"step_id": "inspect", "depends_on": ["plot"]}),
        [
            STANDARD_STEPS[0],
            STANDARD_STEPS[1],
            STANDARD_STEPS[2],
            {**STANDARD_STEPS[3], "depends_on": ["inspect"]},
        ],
    ],
    ids=[
        "missing_action_type",
        "duplicate_step_id",
        "missing_dependency",
        "self_dependency",
        "cycle_inspect_plot",
        "report_cannot_reach_plot",
    ],
)
async def test_plan_rejects_illegal_dag_without_partial_write(
    tmp_path: Path, steps: list[dict[str, Any]]
) -> None:
    """TOOL-PLAN-001：非法 DAG 一次拒绝，Context 无部分 steps。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    assert init and plan

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    before = _context_path(workspace).read_bytes()
    _, payload = await _invoke(plan, workspace, steps=steps)
    _assert_failure_envelope(payload, "CLIMATE_INVALID_INPUT")
    after = _context_path(workspace).read_bytes()
    assert after == before
    ctx = loads_run_context(after.decode("utf-8"))
    assert ctx.status == "initialized"
    assert ctx.steps == []


def test_plan_step_fields_are_strict() -> None:
    """TOOL-PLAN-001：step_id/action/title/depends_on 在 schema 层严格校验。"""
    registry = create_climate_tool_registry()
    plan = registry.get("climate_plan_steps")
    assert plan is not None
    model = plan.input_model

    def payload(step: dict[str, Any]) -> dict[str, Any]:
        rest = [item for item in STANDARD_STEPS if item["step_id"] != "acquire"]
        return {"steps": [step, *rest]}

    with pytest.raises(ValidationError):
        model.model_validate(payload({**STANDARD_STEPS[0], "step_id": "Acquire"}))
    with pytest.raises(ValidationError):
        model.model_validate(payload({**STANDARD_STEPS[0], "step_id": "has_underscore"}))
    with pytest.raises(ValidationError):
        model.model_validate(payload({**STANDARD_STEPS[0], "action": "download"}))
    with pytest.raises(ValidationError):
        model.model_validate(payload({**STANDARD_STEPS[0], "title": ""}))
    with pytest.raises(ValidationError):
        model.model_validate(payload({**STANDARD_STEPS[0], "title": "x" * 201}))
    with pytest.raises(ValidationError):
        model.model_validate(
            payload({**STANDARD_STEPS[0], "depends_on": ["Inspect"]})
        )
    missing_action = dict(STANDARD_STEPS[0])
    missing_action.pop("action")
    with pytest.raises(ValidationError):
        model.model_validate(payload(missing_action))


@pytest.mark.asyncio
async def test_plan_cannot_replace_after_business_step_started(tmp_path: Path) -> None:
    """TOOL-PLAN-001：已开始业务 step 后不得替换 plan；run 保持 running。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _, accepted = await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    _assert_success_envelope(accepted)
    assert accepted["data"]["status"] == "running"
    await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    before = _context_path(workspace).read_bytes()
    ctx = loads_run_context(before.decode("utf-8"))
    assert ctx.status == "running"
    acquire_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert acquire_step.status == "succeeded"

    _, again = await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _assert_failure_envelope(again, "CLIMATE_INVALID_TRANSITION")
    after = _context_path(workspace).read_bytes()
    assert after == before


@pytest.mark.asyncio
async def test_sample_is_deterministic_and_atomic(tmp_path: Path) -> None:
    """TOOL-ACQUIRE-001：sample 离线生成独立 CSV artifact，原子发布并记录摘要。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    _, payload = await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    _assert_success_envelope(payload)

    rel = f".climate/data/{RUN_ID}/sample.csv"
    csv_path = workspace / rel
    raw = csv_path.read_bytes()
    assert raw.decode("utf-8").startswith("date,temperature_c,precipitation_mm\n")
    assert b"\r" not in raw
    rows = [line for line in raw.decode("utf-8").splitlines() if line]
    assert len(rows) == 31
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert payload["data"]["path"] == rel
    assert payload["data"]["media_type"] == "text/csv"
    assert payload["data"]["size_bytes"] == len(raw)
    assert payload["data"]["sha256"] == digest
    assert list((workspace / ".climate").rglob("*.part")) == []

    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    artifact = next(item for item in ctx.artifacts if item.kind == "dataset")
    assert artifact.path == rel
    assert artifact.media_type == "text/csv"
    assert artifact.size_bytes == len(raw)
    assert artifact.sha256 == digest
    acquire_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert acquire_step.status == "succeeded"
    assert artifact.artifact_id in (acquire_step.result or {}).get("artifact_ids", [])

    other = _workspace(tmp_path / "other")
    await _invoke(init, other, objective=OBJECTIVE, run_id=RUN_ID_B)
    await _invoke(plan, other, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, other, step_id="acquire", mode="sample")
    other_csv = (other / ".climate" / "data" / RUN_ID_B / "sample.csv").read_bytes()
    assert other_csv == raw


@pytest.mark.asyncio
async def test_acquire_mode_fields_and_no_implicit_fallback(tmp_path: Path) -> None:
    """TOOL-ACQUIRE-002：mode 字段互斥；G2 cds 不得静默降级为 sample。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    with pytest.raises(ValidationError):
        acquire.input_model.model_validate(
            {"step_id": "acquire", "mode": "sample", "path": "src.csv"}
        )
    with pytest.raises(ValidationError):
        acquire.input_model.model_validate({"step_id": "acquire", "mode": "local"})
    with pytest.raises(ValidationError):
        acquire.input_model.model_validate(
            {
                "step_id": "acquire",
                "mode": "local",
                "path": "obs.csv",
                "cds_request": {"dataset": "reanalysis-era5-single-levels"},
            }
        )

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _, cds = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request={"dataset": "reanalysis-era5-single-levels"},
    )
    _assert_failure_envelope(cds, "CLIMATE_INVALID_INPUT")
    data_dir = workspace / ".climate" / "data" / RUN_ID
    assert not data_dir.exists() or list(data_dir.rglob("*")) == []


def _write_local_csv(workspace: Path, relative: str = "inputs/obs.csv") -> Path:
    path = workspace.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LOCAL_CSV, encoding="utf-8", newline="\n")
    return path


def _try_make_dir_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if sys.platform != "win32":
            raise
    import subprocess

    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not link.exists():
        raise OSError(completed.stderr.strip() or completed.stdout.strip() or "mklink /J failed")


@pytest.mark.asyncio
async def test_sample_and_local_are_deterministic_and_atomic(tmp_path: Path) -> None:
    """TOOL-ACQUIRE-001：local 离线复制独立 CSV，源文件不变，原子发布并记录摘要。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    source = _write_local_csv(workspace)
    source_rel = "inputs/obs.csv"
    before_stat = source.stat()
    before_bytes = source.read_bytes()

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    _, payload = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path=source_rel
    )
    _assert_success_envelope(payload)

    rel = payload["data"]["path"]
    dest = workspace / rel
    assert rel.startswith(f".climate/data/{RUN_ID}/")
    assert rel.endswith(".csv")
    assert dest.resolve() != source.resolve()
    assert dest.read_bytes() == before_bytes
    after_stat = source.stat()
    assert source.read_bytes() == before_bytes
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    digest = "sha256:" + hashlib.sha256(before_bytes).hexdigest()
    assert payload["data"]["media_type"] == "text/csv"
    assert payload["data"]["size_bytes"] == len(before_bytes)
    assert payload["data"]["sha256"] == digest
    assert list((workspace / ".climate").rglob("*.part")) == []

    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    artifact = next(item for item in ctx.artifacts if item.kind == "dataset")
    assert artifact.path == rel
    assert artifact.sha256 == digest
    acquire_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert acquire_step.status == "succeeded"
    assert artifact.artifact_id in (acquire_step.result or {}).get("artifact_ids", [])


@pytest.mark.asyncio
async def test_local_rejects_unsafe_and_non_regular_sources(tmp_path: Path) -> None:
    """PATH-004 / PERM-001：local 再次校验路径；拒绝逃逸、目录、非 CSV。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    before = _context_path(workspace).read_bytes()

    unsafe_cases = [
        "../secret.csv",
        "/etc/passwd",
        "//server/share/file.csv",
    ]
    if sys.platform == "win32":
        unsafe_cases.append("C:/Windows/System32/obs.csv")
    for relative in unsafe_cases:
        _, payload = await _invoke(
            acquire, workspace, step_id="acquire", mode="local", path=relative
        )
        _assert_failure_envelope(payload, "CLIMATE_INVALID_PATH")
        assert _context_path(workspace).read_bytes() == before

    folder = workspace / "inputs"
    folder.mkdir()
    _, as_dir = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path="inputs"
    )
    _assert_failure_envelope(as_dir, "CLIMATE_INVALID_PATH")

    notes = workspace / "notes.txt"
    notes.write_text("not csv\n", encoding="utf-8")
    _, not_csv = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path="notes.txt"
    )
    _assert_failure_envelope(not_csv, "CLIMATE_FORMAT_UNSUPPORTED")

    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    acquire_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert acquire_step.status == "pending"
    data_dir = workspace / ".climate" / "data" / RUN_ID
    assert not data_dir.exists() or list(data_dir.rglob("*")) == []

    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    (outside / "secret.csv").write_text(LOCAL_CSV, encoding="utf-8")
    link = workspace / "escape_link"
    try:
        _try_make_dir_link(link, outside)
        _, escaped = await _invoke(
            acquire,
            workspace,
            step_id="acquire",
            mode="local",
            path="escape_link/secret.csv",
        )
        _assert_failure_envelope(escaped, "CLIMATE_INVALID_PATH")
        assert not data_dir.exists() or list(data_dir.rglob("*")) == []
    except OSError:
        pass

    if sys.platform != "win32":
        fifo = workspace / "pipe.csv"
        os.mkfifo(fifo)
        _, fifo_payload = await _invoke(
            acquire, workspace, step_id="acquire", mode="local", path="pipe.csv"
        )
        _assert_failure_envelope(fifo_payload, "CLIMATE_INVALID_PATH")


@pytest.mark.asyncio
async def test_local_dependency_and_idempotency(tmp_path: Path) -> None:
    """IDEM-001：前置未完成拒绝；同输入重放不改 version/attempts；不同输入冲突。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    source = _write_local_csv(workspace)
    other = _write_local_csv(workspace, "inputs/other.csv")
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=LOCAL_DEP_STEPS, confirmed=True)

    _, early = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path="inputs/obs.csv"
    )
    _assert_failure_envelope(early, "CLIMATE_DEPENDENCY_NOT_READY")
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    local_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert local_step.status == "pending"
    assert local_step.attempts == 0

    await _invoke(acquire, workspace, step_id="prep", mode="sample")
    _, first = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path="inputs/obs.csv"
    )
    _assert_success_envelope(first)
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    version = ctx.version
    local_step = next(step for step in ctx.steps if step.step_id == "acquire")
    attempts = local_step.attempts
    dest_rel = first["data"]["path"]
    dest_bytes = (workspace / dest_rel).read_bytes()
    source_mtime = source.stat().st_mtime_ns

    _, replay = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path="inputs/obs.csv"
    )
    _assert_success_envelope(replay)
    assert replay["data"]["sha256"] == first["data"]["sha256"]
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.version == version
    local_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert local_step.attempts == attempts
    assert (workspace / dest_rel).read_bytes() == dest_bytes
    assert source.stat().st_mtime_ns == source_mtime

    _, conflict = await _invoke(
        acquire, workspace, step_id="acquire", mode="local", path="inputs/other.csv"
    )
    _assert_failure_envelope(conflict, "CLIMATE_IDEMPOTENCY_CONFLICT")
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.version == version
    local_step = next(step for step in ctx.steps if step.step_id == "acquire")
    assert local_step.attempts == attempts
    assert (workspace / dest_rel).read_bytes() == dest_bytes
    assert other.read_bytes() == LOCAL_CSV.encode("utf-8")


@pytest.mark.asyncio
async def test_inspect_is_bounded_and_does_not_touch_dataset(tmp_path: Path) -> None:
    """TOOL-INSPECT-001：默认检查最新 dataset，输出有界 profile，不改数据文件。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    assert init and plan and acquire and inspect

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, workspace, step_id="acquire", mode="sample")

    csv_path = workspace / ".climate" / "data" / RUN_ID / "sample.csv"
    before_stat = csv_path.stat()
    before_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    _, payload = await _invoke(inspect, workspace, step_id="inspect")
    _assert_success_envelope(payload)
    profile = payload["data"]
    assert profile["row_count"] == 30
    names = [column["name"] for column in profile["columns"]]
    assert names == ["date", "temperature_c", "precipitation_mm"]
    assert len(profile["warnings"]) <= 20
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "rows" not in profile
    assert encoded.count("2026-01-") <= 2
    for column in profile["columns"]:
        if column["name"] != "date":
            assert {"min", "max", "mean"} <= set(column)

    after_stat = csv_path.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == before_hash

    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    inspect_step = next(step for step in ctx.steps if step.step_id == "inspect")
    assert inspect_step.status == "succeeded"
    assert inspect_step.result is not None


@pytest.mark.asyncio
async def test_read_context_is_bounded_redacted_and_read_only(tmp_path: Path) -> None:
    """TOOL-READ-001：只读、脱敏、有界；未完成 WAL 返回 CLIMATE_RECOVERY_REQUIRED。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    read = registry.get("climate_read_context")
    assert init and plan and read

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)

    _, payload = await _invoke(read, workspace, include_events=True, event_limit=10)
    _assert_success_envelope(payload)
    view = payload["data"]
    assert str(workspace) not in json.dumps(payload, ensure_ascii=False)
    assert "orphan_run_ids" in view
    events = view.get("events") or view.get("run", {}).get("events")
    assert events is not None
    assert len(events) <= 10
    dumped = json.dumps(payload, ensure_ascii=False)
    assert ".climate/locks/" not in dumped
    assert "backups/" not in dumped
    assert "transactions/" not in dumped

    tx_id = "2a0b1c2d-3e4f-4a5b-8c9d-0e1f2a3b4c5d"
    marker = workspace / ".climate" / "transactions" / f"active-run-{tx_id}.json"
    marker.write_text(
        json.dumps(
            {
                "transaction_id": tx_id,
                "old_active_run_id": None,
                "new_active_run_id": RUN_ID,
                "run_context_written": True,
                "index_written": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = _file_tree_bytes(workspace / ".climate")
    result, recovery = await _invoke(read, workspace)
    assert result.is_error is True
    _assert_failure_envelope(recovery, "CLIMATE_RECOVERY_REQUIRED")
    assert _file_tree_bytes(workspace / ".climate") == snapshot


@pytest.mark.asyncio
async def test_tool_permission_classification_and_path_forwarding() -> None:
    """PERM-001：写工具 is_read_only=False；含 path 的 schema 可供 QueryEngine 提取。"""
    registry = create_climate_tool_registry()
    read = registry.get("climate_read_context")
    validate = registry.get("climate_validate_artifacts")
    inspect = registry.get("climate_inspect_dataset")
    acquire = registry.get("climate_acquire_data")
    plot = registry.get("climate_analyze_plot")
    assert read and validate and inspect and acquire and plot
    assert read.is_read_only(read.input_model.model_validate({})) is True
    assert validate.is_read_only(validate.input_model.model_validate({})) is True
    assert inspect.is_read_only(inspect.input_model.model_validate({"step_id": "inspect"})) is False
    assert acquire.is_read_only(
        acquire.input_model.model_validate({"step_id": "acquire", "mode": "sample"})
    ) is False
    assert plot.is_read_only(
        plot.input_model.model_validate(
            {"step_id": "plot", "chart_type": "line", "x": "date", "y": "temperature_c"}
        )
    ) is False
    for tool in (inspect, acquire, plot):
        assert "path" in tool.input_model.model_json_schema()["properties"]


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PLOT_ARGS = {
    "step_id": "plot",
    "chart_type": "line",
    "x": "date",
    "y": "temperature_c",
    "title": "示例温度",
}


def _matplotlib_installed() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


def _assert_real_png(raw: bytes) -> None:
    assert raw.startswith(PNG_MAGIC)
    assert len(raw) > 32
    assert b"placeholder" not in raw.lower()


def _assert_real_svg(raw: bytes) -> None:
    lowered = raw.lower()
    assert b"<svg" in lowered
    assert b"placeholder" not in lowered
    root = ET.fromstring(raw)
    tag = root.tag.rsplit("}", 1)[-1]
    assert tag == "svg"
    drawn = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"polyline", "polygon", "path", "rect", "line", "circle"}
    ]
    assert drawn, "SVG 必须包含真实绘图元素，不能是空壳或占位文本"


async def _prepare_through_inspect(
    tmp_path: Path,
    *,
    mode: str = "sample",
    csv_text: str | None = None,
    run_id: str = RUN_ID,
) -> tuple[Path, Any]:
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    assert init and plan and acquire and inspect
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=run_id)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    if mode == "local":
        relative = "inputs/obs.csv"
        source = workspace / "inputs" / "obs.csv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(csv_text or LOCAL_CSV, encoding="utf-8", newline="\n")
        await _invoke(acquire, workspace, step_id="acquire", mode="local", path=relative)
    else:
        await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    await _invoke(inspect, workspace, step_id="inspect")
    return workspace, registry


def _assert_plot_artifact(
    workspace: Path,
    payload: dict[str, Any],
    *,
    media_type: str,
    fallback_reason: str | None,
    raw: bytes,
) -> None:
    rel = payload["data"]["path"]
    assert rel.startswith(f".climate/output/{RUN_ID}/")
    assert payload["data"]["media_type"] == media_type
    assert payload["data"]["fallback_reason"] == fallback_reason
    assert payload["data"]["size_bytes"] == len(raw)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert payload["data"]["sha256"] == digest
    assert list((workspace / ".climate").rglob("*.part")) == []
    assert list((workspace / ".climate" / "data").rglob("*.png")) == []
    assert list((workspace / ".climate" / "data").rglob("*.svg")) == []
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    artifact = next(item for item in ctx.artifacts if item.kind == "plot")
    assert artifact.path == rel
    assert artifact.media_type == media_type
    assert artifact.size_bytes == len(raw)
    assert artifact.sha256 == digest
    plot_step = next(step for step in ctx.steps if step.step_id == "plot")
    assert plot_step.status == "succeeded"
    assert artifact.artifact_id in (plot_step.result or {}).get("artifact_ids", [])


@pytest.mark.asyncio
async def test_plot_png_and_svg_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOOL-PLOT-001：matplotlib PNG 与缺失时真实 SVG fallback，只写 output 区。"""
    import openharness.climate.pipeline as climate_pipeline

    svg_ws, svg_registry = await _prepare_through_inspect(tmp_path / "svg")
    plot = svg_registry.get("climate_analyze_plot")
    assert plot is not None
    monkeypatch.setattr(climate_pipeline, "matplotlib_available", lambda: False)
    _, svg_payload = await _invoke(plot, svg_ws, **PLOT_ARGS)
    _assert_success_envelope(svg_payload)
    svg_rel = svg_payload["data"]["path"]
    svg_raw = (svg_ws / svg_rel).read_bytes()
    _assert_real_svg(svg_raw)
    _assert_plot_artifact(
        svg_ws,
        svg_payload,
        media_type="image/svg+xml",
        fallback_reason="matplotlib_missing",
        raw=svg_raw,
    )
    encoded = json.dumps(svg_payload, ensure_ascii=False)
    assert str(svg_ws) not in encoded

    png_ws, png_registry = await _prepare_through_inspect(tmp_path / "png")
    png_plot = png_registry.get("climate_analyze_plot")
    assert png_plot is not None
    if _matplotlib_installed():
        monkeypatch.setattr(
            climate_pipeline, "matplotlib_available", lambda: True, raising=False
        )
        _, png_payload = await _invoke(png_plot, png_ws, **PLOT_ARGS)
        _assert_success_envelope(png_payload)
        png_rel = png_payload["data"]["path"]
        png_raw = (png_ws / png_rel).read_bytes()
        _assert_real_png(png_raw)
        _assert_plot_artifact(
            png_ws,
            png_payload,
            media_type="image/png",
            fallback_reason=None,
            raw=png_raw,
        )
    else:
        _, fallback_payload = await _invoke(png_plot, png_ws, **PLOT_ARGS)
        _assert_success_envelope(fallback_payload)
        fallback_raw = (png_ws / fallback_payload["data"]["path"]).read_bytes()
        _assert_real_svg(fallback_raw)
        assert fallback_payload["data"]["media_type"] == "image/svg+xml"


@pytest.mark.asyncio
async def test_plot_rejects_columns_paths_and_uninspected_data(tmp_path: Path) -> None:
    """TOOL-PLOT-001：列/图类型/路径/未 inspect 数据拒绝，且不写产物。"""
    registry = create_climate_tool_registry()
    plot = registry.get("climate_analyze_plot")
    assert plot is not None
    model = plot.input_model

    with pytest.raises(ValidationError):
        model.model_validate({"step_id": "plot", "chart_type": "pie", "y": "temperature_c"})
    with pytest.raises(ValidationError):
        model.model_validate(
            {"step_id": "plot", "chart_type": "line", "y": "temperature_c"}
        )
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "step_id": "plot",
                "chart_type": "histogram",
                "x": "date",
                "y": "temperature_c",
            }
        )
    with pytest.raises(ValidationError):
        model.model_validate({**PLOT_ARGS, "title": "x" * 201})
    with pytest.raises(ValidationError):
        model.model_validate({**PLOT_ARGS, "unexpected": True})
    model.model_validate({"step_id": "plot", "chart_type": "histogram", "y": "temperature_c"})
    model.model_validate(PLOT_ARGS)

    workspace, ready = await _prepare_through_inspect(tmp_path / "ready")
    plot = ready.get("climate_analyze_plot")
    assert plot is not None
    before = _context_path(workspace).read_bytes()

    _, missing_col = await _invoke(
        plot, workspace, step_id="plot", chart_type="line", x="date", y="not_a_column"
    )
    _assert_failure_envelope(missing_col, "CLIMATE_DATA_INVALID")
    assert _context_path(workspace).read_bytes() == before
    assert list((workspace / ".climate" / "output" / RUN_ID).glob("plot*")) == []

    _, escaped = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
        path="../secret.csv",
    )
    _assert_failure_envelope(escaped, "CLIMATE_INVALID_PATH")
    assert _context_path(workspace).read_bytes() == before

    outsider = workspace / "other.csv"
    outsider.write_text(LOCAL_CSV, encoding="utf-8", newline="\n")
    _, uninspected = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="temperature_c",
        path="other.csv",
    )
    _assert_failure_envelope(uninspected, "CLIMATE_INVALID_INPUT")
    assert _context_path(workspace).read_bytes() == before

    mixed_ws, mixed_reg = await _prepare_through_inspect(
        tmp_path / "mixed",
        mode="local",
        csv_text="date,label,temperature_c\n2026-02-01,warm,1.5\n2026-02-02,cold,2.5\n",
    )
    mixed_plot = mixed_reg.get("climate_analyze_plot")
    assert mixed_plot is not None
    mixed_before = _context_path(mixed_ws).read_bytes()
    _, non_numeric = await _invoke(
        mixed_plot, mixed_ws, step_id="plot", chart_type="line", x="date", y="label"
    )
    _assert_failure_envelope(non_numeric, "CLIMATE_DATA_INVALID")
    assert _context_path(mixed_ws).read_bytes() == mixed_before

    early_ws = _workspace(tmp_path / "early")
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, early_ws, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, early_ws, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, early_ws, step_id="acquire", mode="sample")
    early_before = _context_path(early_ws).read_bytes()
    early_plot = registry.get("climate_analyze_plot")
    assert early_plot is not None
    _, too_early = await _invoke(early_plot, early_ws, **PLOT_ARGS)
    _assert_failure_envelope(too_early, "CLIMATE_DEPENDENCY_NOT_READY")
    ctx = loads_run_context(early_before.decode("utf-8"))
    plot_step = next(step for step in ctx.steps if step.step_id == "plot")
    assert plot_step.status == "pending"
    assert plot_step.attempts == 0
    assert _context_path(early_ws).read_bytes() == early_before
    output_dir = early_ws / ".climate" / "output" / RUN_ID
    assert not output_dir.exists() or list(output_dir.glob("plot*")) == []


@pytest.mark.asyncio
async def test_plot_idempotency_and_conflict(tmp_path: Path) -> None:
    """IDEM-001：同输入 plot 重放不改 version；不同输入冲突且不改产物。"""
    workspace, registry = await _prepare_through_inspect(tmp_path)
    plot = registry.get("climate_analyze_plot")
    assert plot is not None

    _, first = await _invoke(plot, workspace, **PLOT_ARGS)
    _assert_success_envelope(first)
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    version = ctx.version
    plot_step = next(step for step in ctx.steps if step.step_id == "plot")
    attempts = plot_step.attempts
    rel = first["data"]["path"]
    dest_bytes = (workspace / rel).read_bytes()

    _, replay = await _invoke(plot, workspace, **PLOT_ARGS)
    _assert_success_envelope(replay)
    assert replay["data"]["sha256"] == first["data"]["sha256"]
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.version == version
    plot_step = next(step for step in ctx.steps if step.step_id == "plot")
    assert plot_step.attempts == attempts
    assert (workspace / rel).read_bytes() == dest_bytes

    _, conflict = await _invoke(
        plot,
        workspace,
        step_id="plot",
        chart_type="line",
        x="date",
        y="precipitation_mm",
        title="示例温度",
    )
    _assert_failure_envelope(conflict, "CLIMATE_IDEMPOTENCY_CONFLICT")
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.version == version
    assert (workspace / rel).read_bytes() == dest_bytes
    histogram = plot.input_model.model_validate(
        {"step_id": "plot", "chart_type": "histogram", "y": "temperature_c"}
    )
    assert histogram.chart_type == "histogram"


REPORT_ARGS = {
    "step_id": "report",
    "title": "示例气候报告",
    "summary": "温度序列平稳，降水有周期性波动。",
}


async def _prepare_through_plot(
    tmp_path: Path,
    *,
    mode: str = "sample",
    csv_text: str | None = None,
    run_id: str = RUN_ID,
) -> tuple[Path, Any]:
    workspace, registry = await _prepare_through_inspect(
        tmp_path, mode=mode, csv_text=csv_text, run_id=run_id
    )
    plot = registry.get("climate_analyze_plot")
    assert plot is not None
    _, plotted = await _invoke(plot, workspace, **PLOT_ARGS)
    _assert_success_envelope(plotted)
    return workspace, registry


@pytest.mark.asyncio
async def test_report_dependencies_artifact_and_completion(tmp_path: Path) -> None:
    """TOOL-REPORT-001：依赖、Markdown 内容、脱敏、原子发布，并将 run 转为 completed。"""
    registry = create_climate_tool_registry()
    report = registry.get("climate_write_report")
    assert report is not None
    model = report.input_model
    with pytest.raises(ValidationError):
        model.model_validate({"step_id": "report", "title": "", "summary": "摘要"})
    with pytest.raises(ValidationError):
        model.model_validate({"step_id": "report", "title": "x" * 201, "summary": "摘要"})
    with pytest.raises(ValidationError):
        model.model_validate({"step_id": "report", "title": "标题", "summary": ""})
    with pytest.raises(ValidationError):
        model.model_validate({"step_id": "report", "title": "标题", "summary": "s" * 12001})
    with pytest.raises(ValidationError):
        model.model_validate({**REPORT_ARGS, "unexpected": True})
    model.model_validate(REPORT_ARGS)

    early_ws, early_reg = await _prepare_through_inspect(tmp_path / "early")
    early_report = early_reg.get("climate_write_report")
    assert early_report is not None
    before = _context_path(early_ws).read_bytes()
    snapshot = _file_tree_bytes(early_ws / ".climate")
    _, too_early = await _invoke(early_report, early_ws, **REPORT_ARGS)
    _assert_failure_envelope(too_early, "CLIMATE_DEPENDENCY_NOT_READY")
    assert _context_path(early_ws).read_bytes() == before
    assert _file_tree_bytes(early_ws / ".climate") == snapshot
    assert not (early_ws / ".climate" / "output" / RUN_ID / "report.md").exists()
    ctx = loads_run_context(before.decode("utf-8"))
    report_step = next(step for step in ctx.steps if step.step_id == "report")
    assert report_step.status == "pending"
    assert ctx.status == "running"

    workspace, ready = await _prepare_through_plot(tmp_path / "ok")
    report = ready.get("climate_write_report")
    assert report is not None
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    plot_art = next(item for item in ctx.artifacts if item.kind == "plot")
    summary = (
        "字面量 {objective} {{title}} $(whoami) `rm -rf /` <script>alert(1)</script> "
        f"secret=cds-token-abc123XYZ home={workspace}"
    )
    _, payload = await _invoke(
        report,
        workspace,
        step_id="report",
        title="示例气候报告",
        summary=summary,
    )
    _assert_success_envelope(payload)
    rel = ".climate/output/" + RUN_ID + "/report.md"
    report_path = workspace / rel
    assert payload["data"]["path"] == rel
    raw = report_path.read_bytes()
    assert raw.decode("utf-8").endswith("\n")
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert OBJECTIVE in text
    assert "sample" in text
    assert "row_count" in text or "30" in text
    assert plot_art.path in text
    assert f"]({plot_art.path})" in text or plot_art.path in text
    assert "{objective}" in text
    assert "{{title}}" in text
    assert "$(whoami)" in text
    assert "`rm -rf /`" in text
    assert "<script>alert(1)</script>" in text
    assert str(workspace) not in text
    assert "cds-token-abc123XYZ" not in text
    assert RUN_ID in text
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)
    dumped = json.dumps(payload, ensure_ascii=False)
    assert str(workspace) not in dumped
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert payload["data"]["sha256"] == digest
    assert payload["data"]["size_bytes"] == len(raw)
    assert payload["data"]["media_type"] == "text/markdown"
    assert list((workspace / ".climate").rglob("*.part")) == []

    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.status == "completed"
    report_step = next(step for step in ctx.steps if step.step_id == "report")
    assert report_step.status == "succeeded"
    assert all(step.status in {"succeeded", "skipped"} for step in ctx.steps)
    artifact = next(item for item in ctx.artifacts if item.kind == "report")
    assert artifact.path == rel
    assert artifact.sha256 == digest
    assert any(event.type == "run_completed" for event in ctx.events)

    version = ctx.version
    report_bytes = raw
    _, replay = await _invoke(
        report,
        workspace,
        step_id="report",
        title="示例气候报告",
        summary=summary,
    )
    _assert_success_envelope(replay)
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.version == version
    assert ctx.status == "completed"
    assert report_path.read_bytes() == report_bytes

    _, conflict = await _invoke(report, workspace, **REPORT_ARGS)
    _assert_failure_envelope(conflict, "CLIMATE_IDEMPOTENCY_CONFLICT")
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.version == version
    assert report_path.read_bytes() == report_bytes

    local_ws, local_reg = await _prepare_through_plot(tmp_path / "local", mode="local")
    local_report = local_reg.get("climate_write_report")
    assert local_report is not None
    _, local_payload = await _invoke(local_report, local_ws, **REPORT_ARGS)
    _assert_success_envelope(local_payload)
    local_text = (local_ws / ".climate" / "output" / RUN_ID / "report.md").read_text(
        encoding="utf-8"
    )
    assert "local" in local_text
    assert str(local_ws) not in local_text
    local_ctx = loads_run_context(_context_path(local_ws).read_text(encoding="utf-8"))
    assert local_ctx.status == "completed"


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class _FakeCdsClient:
    """工具层 inspect/fallback 测试用假客户端。"""

    def __init__(self, source: Path | None = None, *, errors: list[BaseException] | None = None) -> None:
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


async def _acquire_cds_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
    *,
    fmt: str = "netcdf",
) -> tuple[Path, Any]:
    from openharness.climate import cds as cds_mod

    client = _FakeCdsClient(source)
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    _, payload = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_cds_request(format=fmt),
    )
    _assert_success_envelope(payload)
    return workspace, registry


@pytest.mark.asyncio
async def test_inspect_scientific_fixture_is_bounded_and_does_not_touch_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOOL-INSPECT-001 G4：NetCDF/GRIB inspect 不改源文件，profile 有界且无库对象。"""
    cases = (
        (FIXTURES / "minimal_t2m.nc", "netcdf", "t2m"),
        (FIXTURES / "minimal.grib", "grib", "t"),
    )
    inspect = None
    for source, fmt, variable in cases:
        workspace, registry = await _acquire_cds_fixture(
            tmp_path / fmt, monkeypatch, source, fmt=fmt
        )
        inspect = registry.get("climate_inspect_dataset")
        assert inspect is not None
        published = next(
            path
            for path in (workspace / ".climate" / "data" / RUN_ID).rglob("*")
            if path.is_file() and path.suffix.lower() in {".nc", ".grib"}
        )
        before = published.read_bytes()
        digest = hashlib.sha256(before).hexdigest()
        mtime = published.stat().st_mtime_ns

        _, payload = await _invoke(inspect, workspace, step_id="inspect")
        _assert_success_envelope(payload)
        profile = payload["data"]
        assert profile["format"] == fmt
        assert variable in profile["variables"]
        assert variable in profile["statistics"]
        assert {"min", "max", "mean", "count"} <= set(profile["statistics"][variable])
        encoded = json.dumps(profile)
        assert len(encoded.encode("utf-8")) < 16_384
        assert "rows" not in profile
        assert "netCDF4.Dataset" not in encoded
        assert published.read_bytes() == before
        assert hashlib.sha256(published.read_bytes()).hexdigest() == digest
        assert published.stat().st_mtime_ns == mtime

        ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
        step = next(item for item in ctx.steps if item.step_id == "inspect")
        assert step.status == "succeeded"
        dumped = json.dumps(ctx.model_dump(mode="json"))
        assert "Dataset object" not in dumped
        assert "eccodes" not in dumped.lower() or "format" in dumped


@pytest.mark.asyncio
async def test_inspect_rejects_truncated_and_masquerade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    assert init and plan and acquire and inspect
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)

    client = _FakeCdsClient(FIXTURES / "grib_magic.nc")
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)
    _, acquired = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_cds_request(),
    )
    _assert_failure_envelope(acquired, "CLIMATE_DATA_INVALID")
    data_dir = workspace / ".climate" / "data" / RUN_ID
    assert not data_dir.exists() or list(data_dir.rglob("*")) == []

    # 截断文件即使被放进 workspace 也不得产生 profile artifact
    workspace2, _registry2 = await _acquire_cds_fixture(
        tmp_path / "ok", monkeypatch, FIXTURES / "minimal_t2m.nc"
    )
    inspect2 = _registry2.get("climate_inspect_dataset")
    assert inspect2 is not None
    target = next(
        path
        for path in (workspace2 / ".climate" / "data" / RUN_ID).rglob("*.nc")
        if path.is_file()
    )
    target.write_bytes((FIXTURES / "truncated.nc").read_bytes())
    _, inspected = await _invoke(inspect2, workspace2, step_id="inspect")
    _assert_failure_envelope(inspected, "CLIMATE_DATA_INVALID")
    profile = workspace2 / ".climate" / "output" / RUN_ID / "profile.json"
    assert not profile.exists()


@pytest.mark.asyncio
async def test_inspect_optional_reader_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import formats as formats_mod

    workspace, registry = await _acquire_cds_fixture(
        tmp_path, monkeypatch, FIXTURES / "minimal_t2m.nc"
    )
    inspect = registry.get("climate_inspect_dataset")
    assert inspect is not None
    monkeypatch.setattr(formats_mod, "netcdf4_available", lambda: False)
    _, payload = await _invoke(inspect, workspace, step_id="inspect")
    _assert_failure_envelope(payload, "CLIMATE_DEPENDENCY_MISSING")
    assert payload["error"]["details"]["reason"] == "netCDF4"
    assert "C:\\" not in payload["error"]["message"]
    profile = workspace / ".climate" / "output" / RUN_ID / "profile.json"
    assert not profile.exists()


ALTERNATE_STEPS: list[dict[str, Any]] = [
    {
        "step_id": "fetch",
        "action": "acquire_data",
        "title": "获取 CDS 数据",
        "depends_on": [],
    },
    {
        "step_id": "inspect",
        "action": "inspect_dataset",
        "title": "检查数据",
        "depends_on": ["fetch"],
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


@pytest.mark.asyncio
async def test_acquire_without_plan_confirm_is_plan_unconfirmed(tmp_path: Path) -> None:
    """TEST-012 / PLAN-001：plan 后立即 acquire → plan_unconfirmed，不得写盘。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _, planned = await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _assert_success_envelope(planned)
    assert planned["data"]["confirmed"] is False
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.schema_version == 2
    assert any(event.type == "plan_created" for event in ctx.events)
    assert not any(event.type == "plan_confirmed" for event in ctx.events)

    _, payload = await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    assert payload["ok"] is False
    error = payload["error"]
    assert error["code"] == "CLIMATE_INVALID_TRANSITION"
    assert error["details"]["reason"] == "plan_unconfirmed"
    assert error["retryable"] is True
    assert "C:\\" not in error["message"]
    assert "/home/" not in error["message"]
    data_dir = workspace / ".climate" / "data" / RUN_ID
    assert list(data_dir.rglob("*")) == [] if data_dir.exists() else True
    assert list(workspace.rglob("*.part")) == []
    after = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    acquire_step = next(step for step in after.steps if step.step_id == "acquire")
    assert acquire_step.status == "pending"


@pytest.mark.asyncio
async def test_acquire_after_plan_confirm_sample_succeeds(tmp_path: Path) -> None:
    """TEST-012 / PLAN-002：confirmed=true 后 sample acquire 走既有成功路径。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _, planned = await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    _assert_success_envelope(planned)
    assert planned["data"]["confirmed"] is True
    titles = [item["title"] for item in planned["data"]["steps"]]
    assert titles == [step["title"] for step in STANDARD_STEPS]
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    assert ctx.schema_version == 2
    created = [event for event in ctx.events if event.type == "plan_created"]
    confirmed = [event for event in ctx.events if event.type == "plan_confirmed"]
    assert len(created) == 1 and len(confirmed) == 1
    assert confirmed[0].sequence > created[0].sequence

    _, payload = await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    _assert_success_envelope(payload)
    assert (workspace / ".climate" / "data" / RUN_ID / "sample.csv").is_file()


@pytest.mark.asyncio
async def test_replace_pending_plan_invalidates_old_confirmation(tmp_path: Path) -> None:
    """TEST-012：确认前/后整表换 pending plan 成功；旧确认无效，须再确认。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    _, replaced = await _invoke(plan, workspace, steps=ALTERNATE_STEPS)
    _assert_success_envelope(replaced)
    assert replaced["data"]["confirmed"] is False
    assert replaced["data"]["step_ids"][0] == "fetch"
    ctx = loads_run_context(_context_path(workspace).read_text(encoding="utf-8"))
    created = [event for event in ctx.events if event.type == "plan_created"]
    confirmed = [event for event in ctx.events if event.type == "plan_confirmed"]
    assert len(created) == 2
    assert all(item.sequence < created[-1].sequence for item in confirmed)

    _, blocked = await _invoke(acquire, workspace, step_id="fetch", mode="sample")
    assert blocked["ok"] is False
    assert blocked["error"]["details"]["reason"] == "plan_unconfirmed"
    assert blocked["error"]["retryable"] is True
    assert not (workspace / ".climate" / "data" / RUN_ID / "sample.csv").exists()

    _, rec = await _invoke(plan, workspace, steps=ALTERNATE_STEPS, confirmed=True)
    _assert_success_envelope(rec)
    assert rec["data"]["confirmed"] is True
    _, payload = await _invoke(acquire, workspace, step_id="fetch", mode="sample")
    _assert_success_envelope(payload)


@pytest.mark.asyncio
async def test_cannot_replace_plan_after_acquire_started(tmp_path: Path) -> None:
    """TEST-012 / TOOL-PLAN-001：acquire 已 start 后换 plan 仍失败。"""
    registry = create_climate_tool_registry()
    workspace = _workspace(tmp_path)
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire

    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS, confirmed=True)
    await _invoke(acquire, workspace, step_id="acquire", mode="sample")
    before = _context_path(workspace).read_bytes()
    _, again = await _invoke(plan, workspace, steps=ALTERNATE_STEPS, confirmed=True)
    _assert_failure_envelope(again, "CLIMATE_INVALID_TRANSITION")
    assert _context_path(workspace).read_bytes() == before
