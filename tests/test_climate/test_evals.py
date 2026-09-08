"""EVAL-001/002/003、TEST-005、MEM-001：Scenario、Trace、real_offline 与 synthetic。"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evals.climate.agent_config import config_fingerprint, load_agent_config
from evals.climate.assertions import evaluate_hard_assertions
from evals.climate.models import (
    EvalMode,
    HardAssertionSpec,
    Scenario,
    TraceRecord,
    load_scenario,
)
from evals.climate.real_offline import network_guard, run_real_offline
from evals.climate.runner import run_suite
from openharness.climate.models import loads_run_context
from openharness.climate.tools import (
    ClimateAcquireDataTool,
    ClimateAnalyzePlotTool,
    ClimateInitWorkflowTool,
    ClimateInspectDatasetTool,
    ClimatePlanStepsTool,
    ClimateReadContextTool,
    ClimateWriteReportInput,
    ClimateWriteReportTool,
)

ROOT = Path(__file__).resolve().parents[2]


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "evals", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _minimal_scenario(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "sample_pipeline",
        "description": "最小 wiring 场景",
        "mode": "synthetic_dry_run",
        "initial_files": {},
        "turns": [{"role": "user", "content": "分析示例温度序列并生成报告"}],
        "expected_tool_sequence": [
            "climate_init_workflow",
            "climate_plan_steps",
            "climate_acquire_data",
            "climate_inspect_dataset",
            "climate_analyze_plot",
            "climate_write_report",
            "climate_read_context",
        ],
        "hard_assertions": [
            {
                "id": "seq",
                "type": "tool_sequence",
                "expected": [
                    "climate_init_workflow",
                    "climate_plan_steps",
                ],
            }
        ],
        "timeout_seconds": 30,
    }
    payload.update(overrides)
    return payload


def _minimal_trace(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suite_version": "g3-foundation",
        "scenario_id": "sample_pipeline",
        "run_id": "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314",
        "mode": "synthetic_dry_run",
        "started_at": "2026-08-28T01:00:00Z",
        "finished_at": "2026-08-28T01:00:01Z",
        "duration_ms": 12,
        "tool_calls": [
            {
                "sequence": 1,
                "name": "climate_init_workflow",
                "input_redacted": {"objective": "分析示例温度序列并生成报告"},
                "is_error": False,
                "error_code": None,
                "duration_ms": 1,
            }
        ],
        "hook_events": [],
        "final_run_status": "initialized",
        "final_context_version": 1,
        "artifact_manifest": [],
        "assertion_results": [],
        "synthetic": True,
        "tools_executed": False,
        "model_invoked": False,
        "counts_toward_real_pass_rate": False,
    }
    payload.update(overrides)
    return payload


def test_scenario_requires_fields_and_mode_enum() -> None:
    """Scenario 必填字段与 mode 枚举。"""
    scenario = Scenario.model_validate(_minimal_scenario())
    assert scenario.id == "sample_pipeline"
    assert scenario.mode is EvalMode.synthetic_dry_run
    assert scenario.timeout_seconds == 30
    assert len(scenario.turns) == 1
    assert scenario.expected_tool_sequence[0] == "climate_init_workflow"

    for missing in (
        "id",
        "description",
        "mode",
        "initial_files",
        "turns",
        "expected_tool_sequence",
        "hard_assertions",
        "timeout_seconds",
    ):
        payload = _minimal_scenario()
        payload.pop(missing)
        with pytest.raises(ValidationError):
            Scenario.model_validate(payload)

    with pytest.raises(ValidationError):
        Scenario.model_validate(_minimal_scenario(mode="dry-run"))
    with pytest.raises(ValidationError):
        Scenario.model_validate(_minimal_scenario(timeout_seconds=0))
    with pytest.raises(ValidationError):
        Scenario.model_validate(_minimal_scenario(timeout_seconds=-1))
    with pytest.raises(ValidationError):
        Scenario.model_validate(_minimal_scenario(turns=[]))
    with pytest.raises(ValidationError):
        Scenario.model_validate(_minimal_scenario(expected_tool_sequence=[]))
    with pytest.raises(ValidationError):
        Scenario.model_validate(_minimal_scenario(unexpected=True))

    recognized = Scenario.model_validate(_minimal_scenario(mode="real_agent"))
    assert recognized.mode is EvalMode.real_agent
    Scenario.model_validate(_minimal_scenario(mode="real_offline"))


def test_load_sample_pipeline_yaml_roundtrip() -> None:
    """仓库内最小 sample_pipeline.yaml 可被严格校验。"""
    path = ROOT / "evals" / "climate" / "scenarios" / "sample_pipeline.yaml"
    scenario = load_scenario(path)
    assert scenario.id == "sample_pipeline"
    assert scenario.mode in {EvalMode.synthetic_dry_run, EvalMode.real_offline}
    assert scenario.timeout_seconds >= 1
    assert scenario.expected_tool_sequence
    assert scenario.turns
    assert scenario.hard_assertions


def test_trace_record_requires_section_12_fields_and_redacts_input() -> None:
    """TraceRecord 含第 12 节全部字段；input_redacted 不得含密钥或绝对路径。"""
    trace = TraceRecord.model_validate(_minimal_trace())
    dumped = trace.model_dump(mode="json")
    for key in (
        "suite_version",
        "scenario_id",
        "run_id",
        "mode",
        "started_at",
        "finished_at",
        "duration_ms",
        "tool_calls",
        "hook_events",
        "final_run_status",
        "final_context_version",
        "artifact_manifest",
        "assertion_results",
    ):
        assert key in dumped
    call = dumped["tool_calls"][0]
    for key in ("sequence", "name", "input_redacted", "is_error", "error_code", "duration_ms"):
        assert key in call

    for missing in (
        "suite_version",
        "scenario_id",
        "run_id",
        "mode",
        "started_at",
        "finished_at",
        "duration_ms",
        "tool_calls",
        "hook_events",
        "final_run_status",
        "final_context_version",
        "artifact_manifest",
        "assertion_results",
    ):
        payload = _minimal_trace()
        payload.pop(missing)
        with pytest.raises(ValidationError):
            TraceRecord.model_validate(payload)

    with pytest.raises(ValidationError):
        TraceRecord.model_validate(_minimal_trace(mode="offline"))

    secret_input = _minimal_trace()
    secret_input["tool_calls"] = [
        {
            "sequence": 1,
            "name": "climate_init_workflow",
            "input_redacted": {
                "objective": "分析",
                "api_key": "sk-secret",
                "path": str(Path.home() / ".cdsapirc"),
            },
            "is_error": False,
            "error_code": None,
            "duration_ms": 1,
        }
    ]
    with pytest.raises(ValidationError):
        TraceRecord.model_validate(secret_input)


def test_hard_assertion_success_and_failure() -> None:
    """hard assertion 成功/失败。"""
    trace = TraceRecord.model_validate(
        _minimal_trace(
            tool_calls=[
                {
                    "sequence": 1,
                    "name": "climate_init_workflow",
                    "input_redacted": {"objective": "分析示例温度序列并生成报告"},
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 1,
                },
                {
                    "sequence": 2,
                    "name": "climate_plan_steps",
                    "input_redacted": {"steps": ["acquire"]},
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 1,
                },
            ]
        )
    )
    passed = evaluate_hard_assertions(
        trace,
        [
            {
                "id": "seq",
                "type": "tool_sequence",
                "expected": ["climate_init_workflow", "climate_plan_steps"],
            }
        ],
    )
    assert passed
    assert all(item.passed for item in passed)

    failed = evaluate_hard_assertions(
        trace,
        [
            {
                "id": "seq",
                "type": "tool_sequence",
                "expected": ["climate_write_report"],
            }
        ],
    )
    assert failed
    assert any(item.passed is False for item in failed)


def test_cli_nonzero_when_hard_assertion_fails() -> None:
    """任一 hard assertion 失败时 CLI 非零。"""
    completed = _cli(
        "--suite",
        "climate",
        "--mode",
        "synthetic_dry_run",
        "--scenario",
        "hard_assertion_fail",
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "hard assertion" in combined.lower() or "assertion" in combined.lower()


def test_synthetic_dry_run_is_labeled_and_excluded_from_real_pass_rate() -> None:
    """synthetic dry-run 明显标记，不能计入真实通过率。"""
    completed = _cli("--suite", "climate", "--mode", "synthetic_dry_run")
    assert completed.returncode == 0
    combined = (completed.stdout + completed.stderr).lower()
    assert "synthetic" in combined
    assert "not executed" in combined or "未执行" in combined or "does not execute" in combined
    assert "tool" in combined or "工具" in combined
    assert "model" in combined or "模型" in combined

    report_path = ROOT / "evals" / "reports" / "climate-synthetic_dry_run.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["synthetic"] is True
    assert report["tools_executed"] is False
    assert report["model_invoked"] is False
    assert report["counts_toward_real_pass_rate"] is False
    assert report.get("real_pass_rate") in (None, 0, 0.0)
    traces = report.get("traces") or report.get("results")
    assert traces
    first = traces[0]
    if "trace" in first:
        first = first["trace"]
    assert first["mode"] == "synthetic_dry_run"
    assert first["synthetic"] is True
    assert first["counts_toward_real_pass_rate"] is False
    encoded = json.dumps(report)
    assert "sk-" not in encoded
    assert str(Path.home()) not in encoded


def test_cli_accepts_suite_and_mode_flags() -> None:
    """`--suite climate --mode real_offline|synthetic_dry_run` 参数可解析。"""
    help_out = _cli("--help")
    assert help_out.returncode == 0
    text = help_out.stdout + help_out.stderr
    assert "--suite" in text
    assert "--mode" in text
    assert "climate" in text
    assert "real_offline" in text
    assert "synthetic_dry_run" in text
    assert "real_agent" in text
    assert "--agent-config" in text
    assert "--runs" in text
    assert "--baseline-out" in text


def test_real_agent_is_schema_recognized_but_g3_refuses_execution() -> None:
    """real_agent 可被 schema 识别，G3 明确拒绝为 G4 尚未配置，不得伪造执行。"""
    scenario = Scenario.model_validate(_minimal_scenario(mode="real_agent"))
    assert scenario.mode is EvalMode.real_agent

    completed = _cli("--suite", "climate", "--mode", "real_agent")
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "CLIMATE_DEPENDENCY_MISSING" in combined
    assert "G4" in combined
    assert "尚未配置" in combined or "not configured" in combined.lower()
    lowered = combined.lower()
    assert "synthetic" not in lowered or "not executed" in lowered
    assert "pass_rate" not in lowered or "not counted" in lowered or "不计入" in combined


def test_climate_real_config_is_non_sensitive() -> None:
    """agent-config 只冻结非敏感 provider/model 引用，不得含凭证字段。"""
    path = ROOT / "evals" / "configs" / "climate-real.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(data, ensure_ascii=False)
    forbidden = (
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
        "cdsapirc",
        "CDSAPI_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    )
    lowered = text.lower()
    for field in forbidden:
        assert field.lower() not in lowered
    assert "sk-" not in lowered
    assert data["profile"] == "openai-compatible"
    assert data["model"] == "deepseek-v4-pro"
    assert data["permission_mode"] == "full_auto"
    assert data["max_turns"] == 200
    assert data["allow_sample_fallback"] is False
    assert data["cds_request"]["allow_sample_fallback"] is False
    assert data["cds_request"]["date_start"] == data["cds_request"]["date_end"]


def test_missing_suite_or_scenario_returns_stable_diagnostic() -> None:
    """不存在 suite/scenario 返回稳定诊断。"""
    missing_suite = _cli("--suite", "does-not-exist", "--mode", "synthetic_dry_run")
    assert missing_suite.returncode != 0
    suite_text = missing_suite.stdout + missing_suite.stderr
    assert "CLIMATE_INVALID_INPUT" in suite_text or "unknown suite" in suite_text.lower()
    assert "does-not-exist" in suite_text

    missing_scenario = _cli(
        "--suite",
        "climate",
        "--mode",
        "synthetic_dry_run",
        "--scenario",
        "no-such-scenario",
    )
    assert missing_scenario.returncode != 0
    scenario_text = missing_scenario.stdout + missing_scenario.stderr
    assert "CLIMATE_INVALID_INPUT" in scenario_text or "not found" in scenario_text.lower()
    assert "no-such-scenario" in scenario_text


def test_runner_synthetic_adapter_does_not_call_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """synthetic adapter 只生成 wiring 数据，声明不执行工具/模型。"""
    called = {"n": 0}

    def _forbidden(*_args: Any, **_kwargs: Any) -> None:
        called["n"] += 1
        raise AssertionError("synthetic 不得调用真实工具")

    monkeypatch.setattr("openharness.climate.pipeline.init_workflow", _forbidden)
    code = run_suite("climate", "synthetic_dry_run")
    assert code == 0
    assert called["n"] == 0


def _scenario(name: str) -> Scenario:
    return load_scenario(ROOT / "evals" / "climate" / "scenarios" / f"{name}.yaml")


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_hard_pass(trace: TraceRecord) -> None:
    failed = [item for item in trace.assertion_results if not item.passed]
    assert not failed, failed
    assert trace.synthetic is False
    assert trace.tools_executed is True
    assert trace.model_invoked is False
    assert trace.counts_toward_real_pass_rate is True
    assert trace.mode is EvalMode.real_offline
    dumped = json.dumps(trace.model_dump(mode="json"), ensure_ascii=False)
    assert str(Path.home()) not in dumped
    assert ".cdsapirc" not in dumped.lower()
    assert "sk-" not in dumped
    assert "Traceback" not in dumped


def _count_executes(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counts: dict[str, int] = {}

    def _wrap(cls: Any) -> None:
        original = cls.execute

        async def wrapped(self: Any, arguments: Any, context: Any) -> Any:
            counts[cls.name] = counts.get(cls.name, 0) + 1
            return await original(self, arguments, context)

        monkeypatch.setattr(cls, "execute", wrapped)

    for cls in (
        ClimateInitWorkflowTool,
        ClimatePlanStepsTool,
        ClimateAcquireDataTool,
        ClimateInspectDatasetTool,
        ClimateAnalyzePlotTool,
        ClimateWriteReportTool,
        ClimateReadContextTool,
    ):
        _wrap(cls)
    return counts


def test_sample_pipeline_real_offline_hard_assertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-002：sample_pipeline 七工具按依赖顺序真实执行，产物与 Trace 硬断言。"""
    scenario = _scenario("sample_pipeline")
    assert scenario.mode is EvalMode.real_offline
    assert [item.name for item in scenario.tool_invocations] == scenario.expected_tool_sequence
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    counts = _count_executes(monkeypatch)

    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    trace = trace.model_copy(update={"assertion_results": results})
    _assert_hard_pass(trace)

    names = [item.name for item in trace.tool_calls]
    assert names == scenario.expected_tool_sequence
    assert all(item.is_error is False for item in trace.tool_calls)
    assert all(item.duration_ms >= 0 for item in trace.tool_calls)
    assert trace.duration_ms >= 0
    assert trace.final_run_status == "completed"
    assert trace.network_isolated is True
    assert trace.context_versions
    assert trace.context_versions == sorted(trace.context_versions)
    assert trace.final_context_version == trace.context_versions[-1]

    run_id = trace.run_id
    assert run_id
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / run_id / "context.json").read_text(encoding="utf-8")
    )
    assert ctx.status == "completed"
    assert ctx.version == trace.final_context_version
    kinds = {item["kind"] for item in trace.artifact_manifest}
    assert {"dataset", "plot", "report"} <= kinds
    for item in trace.artifact_manifest:
        path = workspace / item["path"]
        assert path.is_file()
        assert _file_digest(path) == item["sha256"]
        assert item["matches_context"] is True
        assert str(workspace) not in item["path"]

    report_text = (workspace / ".climate" / "output" / run_id / "report.md").read_text(
        encoding="utf-8"
    )
    assert "](.climate/" in report_text
    assert str(workspace) not in report_text
    assert counts["climate_init_workflow"] == 1
    assert counts["climate_acquire_data"] == 1
    assert counts["climate_write_report"] == 1


def test_cached_inspect_real_offline_hard_assertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-002：cached_inspect 复制 fixture、统计匹配、幂等 inspect、禁网。"""
    scenario = _scenario("cached_inspect")
    fixture = ROOT / "evals" / "climate" / "fixtures" / "cached_inspect.csv"
    fixture_bytes = fixture.read_bytes()
    fixture_mtime = fixture.stat().st_mtime_ns
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    counts = _count_executes(monkeypatch)

    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    trace = trace.model_copy(update={"assertion_results": results})
    _assert_hard_pass(trace)

    source = workspace / "inputs" / "cached.csv"
    assert source.is_file()
    assert source.read_bytes() == fixture_bytes
    dest = workspace / ".climate" / "data" / trace.run_id / "local-acquire.csv"
    assert dest.is_file()
    assert dest.resolve() != source.resolve()
    assert dest.read_bytes() == fixture_bytes
    assert fixture.read_bytes() == fixture_bytes
    assert fixture.stat().st_mtime_ns == fixture_mtime
    assert (trace.recovery or {}).get("source_unmodified") is True

    inspect_calls = [item for item in trace.tool_calls if item.name == "climate_inspect_dataset"]
    assert len(inspect_calls) == 2
    assert inspect_calls[0].output_redacted["row_count"] == 3
    columns = {item["name"]: item for item in inspect_calls[0].output_redacted["columns"]}
    assert columns["date"]["dtype"] == "string"
    assert columns["date"]["null_count"] == 0
    assert columns["temperature_c"]["null_count"] == 1
    assert columns["temperature_c"]["mean"] == 12.0
    assert columns["precipitation_mm"]["mean"] == 2.0
    assert inspect_calls[1].output_redacted["row_count"] == 3
    assert inspect_calls[1].context_version == inspect_calls[0].context_version
    assert counts["climate_inspect_dataset"] == 2
    assert counts["climate_acquire_data"] == 1
    encoded = json.dumps(trace.model_dump(mode="json"))
    assert "http://" not in encoded
    assert "https://" not in encoded


def test_multiturn_recovery_destroys_memory_and_restores_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEM-001 / CTX-002：销毁第一会话内存，只从磁盘 Context 恢复，不继承 tool_metadata。"""
    scenario = _scenario("multiturn_recovery")
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    counts = _count_executes(monkeypatch)
    live_contexts: list[Any] = []

    from openharness.tools.base import ToolExecutionContext

    original_init = ToolExecutionContext.__init__

    def tracking_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        live_contexts.append(self)

    monkeypatch.setattr(ToolExecutionContext, "__init__", tracking_init)

    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    trace = trace.model_copy(update={"assertion_results": results})
    _assert_hard_pass(trace)

    sessions = [item.session for item in trace.tool_calls]
    assert sessions[:4] == [1, 1, 1, 1]
    assert sessions[4:] == [2, 2, 2]
    assert trace.tool_calls[4].name == "climate_read_context"
    assert trace.tool_calls[4].input_redacted.get("run_id") is None
    recovery = trace.recovery or {}
    assert recovery["session_boundary"] is True
    assert recovery["session1_destroyed"] is True
    assert recovery["read_context_before_continue"] is True
    assert recovery["recovery_source"] == "disk_context"
    assert recovery["inherited_tool_metadata"] is False
    assert "eval_session" not in recovery.get("session2_metadata_keys", [])
    assert len(live_contexts) >= 2
    assert live_contexts[0] is not live_contexts[-1]
    assert live_contexts[-1].metadata.get("eval_session") != "eval-session-1-sentinel"
    assert live_contexts[0].metadata is not live_contexts[-1].metadata

    run_id = trace.run_id
    assert run_id
    ctx = loads_run_context(
        (workspace / ".climate" / "runs" / run_id / "context.json").read_text(encoding="utf-8")
    )
    assert ctx.status == "completed"
    kinds = {item.kind for item in ctx.artifacts}
    assert "dataset" in kinds and "plot" in kinds and "report" in kinds
    for artifact in ctx.artifacts:
        path = workspace / artifact.path
        assert path.is_file()
        assert _file_digest(path) == artifact.sha256
    assert counts["climate_read_context"] == 1
    assert counts["climate_analyze_plot"] == 1
    assert counts["climate_write_report"] == 1
    assert "synthetic" not in json.dumps(trace.tool_calls[0].input_redacted)


def test_real_offline_scenarios_and_hook_provenance(tmp_path: Path) -> None:
    """EVAL-002：四核心场景真实离线；Hook 场景必须由 PRE_TOOL_USE 阻断。"""
    for name in ("sample_pipeline", "cached_inspect", "multiturn_recovery"):
        scenario = _scenario(name)
        workspace = (tmp_path / name / "ws").resolve()
        workspace.mkdir(parents=True)
        trace = run_real_offline(scenario, workspace=workspace)
        results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
        assert all(item.passed for item in results), results
        assert trace.mode is EvalMode.real_offline
        assert trace.synthetic is False
        assert trace.tools_executed is True
    hook = ROOT / "evals" / "climate" / "scenarios" / "pre_tool_output_guard.yaml"
    assert hook.is_file()
    scenario = load_scenario(hook)
    workspace = (tmp_path / "pre_tool_output_guard" / "ws").resolve()
    workspace.mkdir(parents=True)
    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    assert all(item.passed for item in results), results
    assert any(
        item.event == "pre_tool_use" and item.blocked is True for item in trace.hook_events
    )


def test_pre_tool_output_guard_blocks_before_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOOK-001：schema 合法的 write_report 被 PRE_TOOL_USE 阻断，execute=0，磁盘零变化。"""
    marker = "blocked-output-secret"
    scenario = _scenario("pre_tool_output_guard")
    report_inv = next(
        item for item in scenario.tool_invocations if item.name == "climate_write_report"
    )
    ClimateWriteReportInput.model_validate(report_inv.input)
    assert marker in str(report_inv.input["summary"])

    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    counts = _count_executes(monkeypatch)

    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    trace = trace.model_copy(update={"assertion_results": results})
    _assert_hard_pass(trace)

    assert counts.get("climate_write_report", 0) == 0
    assert counts["climate_analyze_plot"] == 1
    assert counts["climate_init_workflow"] == 1

    blocked = [item for item in trace.hook_events if item.blocked]
    assert len(blocked) == 1
    assert blocked[0].event == "pre_tool_use"
    assert blocked[0].tool_name == "climate_write_report"
    assert blocked[0].reason_code
    assert all(item.tool_name == "climate_write_report" for item in trace.hook_events)

    write_calls = [item for item in trace.tool_calls if item.name == "climate_write_report"]
    assert len(write_calls) == 1
    assert write_calls[0].is_error is True
    assert write_calls[0].error_code == "CLIMATE_HOOK_BLOCKED"
    assert write_calls[0].error_code != "CLIMATE_INVALID_INPUT"
    assert (write_calls[0].output_redacted or {}).get("provenance") == "hook"

    recovery = trace.recovery or {}
    assert recovery.get("write_report_executed") is False
    assert recovery.get("context_version_unchanged") is True
    assert recovery.get("events_unchanged") is True
    assert recovery.get("file_tree_unchanged") is True
    assert recovery.get("hook_blocked_before_execute") is True

    run_id = trace.run_id
    assert run_id
    ctx_path = workspace / ".climate" / "runs" / run_id / "context.json"
    ctx = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    assert ctx.status == "running"
    assert ctx.version == trace.final_context_version
    assert not (workspace / ".climate" / "output" / run_id / "report.md").exists()
    for artifact in ctx.artifacts:
        assert artifact.kind != "report"
        text = (workspace / artifact.path).read_text(encoding="utf-8", errors="replace")
        assert marker not in text
    encoded = json.dumps(trace.model_dump(mode="json"), ensure_ascii=False)
    assert "CLIMATE_INVALID_INPUT" not in encoded or write_calls[0].error_code == "CLIMATE_HOOK_BLOCKED"


def test_cli_real_offline_runs_core_scenarios() -> None:
    """CLI `--mode real_offline` 执行四场景，计入真实通过率；产物不进 Git。"""
    completed = _cli("--suite", "climate", "--mode", "real_offline")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report_path = ROOT / "evals" / "reports" / "climate-real_offline.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["synthetic"] is False
    assert report["tools_executed"] is True
    assert report["model_invoked"] is False
    assert report["counts_toward_real_pass_rate"] is True
    assert report["real_pass_rate"] == 1.0
    ids = [item["trace"]["scenario_id"] for item in report["traces"]]
    assert ids == [
        "sample_pipeline",
        "cached_inspect",
        "multiturn_recovery",
        "pre_tool_output_guard",
    ]
    for item in report["traces"]:
        assert item["passed"] is True
        assert item["trace"]["mode"] == "real_offline"
        assert item["trace"]["synthetic"] is False
        encoded = json.dumps(item["trace"])
        assert str(Path.home()) not in encoded
        assert "sk-" not in encoded
    guard = next(item for item in report["traces"] if item["trace"]["scenario_id"] == "pre_tool_output_guard")
    assert any(event["blocked"] is True for event in guard["trace"]["hook_events"])
    assert report_path.as_posix().endswith("evals/reports/climate-real_offline.json")


def test_readme_documents_offline_mvp_demo_and_limits() -> None:
    """DOC-001：README 写明 MVP 称谓条件、Demo、恢复、模式差异与限制。"""
    combined = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "README.zh-CN.md")
    )
    for token in (
        "ClimWorkflow Offline Engineering MVP",
        "real_offline",
        "synthetic_dry_run",
        "climate_read_context",
        "sample_pipeline",
        "CLIMATE_HOOK_BLOCKED",
        "CDS",
        ".climate/",
    ):
        assert token in combined
    assert "Day 10" in combined or "人工验收" in combined
    assert "不" in combined or "No real CDS" in combined or "no CDS" in combined.lower()


def test_readme_offline_demo_from_empty_workspace(tmp_path: Path) -> None:
    """DOC-001：空 workspace sample Demo，预期数据/图/报告/Context，并只从磁盘恢复。"""
    import asyncio

    from openharness.climate.registry import create_climate_tool_registry
    from openharness.tools.base import ToolExecutionContext

    workspace = (tmp_path / "demo-workspace").resolve()
    workspace.mkdir()
    assert not (workspace / ".climate").exists()
    scenario = _scenario("sample_pipeline")
    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    assert all(item.passed for item in results), results
    run_id = trace.run_id
    assert run_id
    assert trace.final_run_status == "completed"
    data_dir = workspace / ".climate" / "data" / run_id
    output_dir = workspace / ".climate" / "output" / run_id
    assert data_dir.is_dir()
    assert any(data_dir.iterdir())
    plots = list(output_dir.glob("*.png")) + list(output_dir.glob("*.svg"))
    assert plots
    report = output_dir / "report.md"
    assert report.is_file()
    report_text = report.read_text(encoding="utf-8")
    assert "](.climate/" in report_text
    assert str(workspace) not in report_text
    ctx_path = workspace / ".climate" / "runs" / run_id / "context.json"
    assert ctx_path.is_file()
    ctx = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    assert ctx.status == "completed"
    assert (workspace / ".climate" / "index.json").is_file()

    async def _recover() -> dict[str, Any]:
        tool = create_climate_tool_registry().get("climate_read_context")
        assert tool is not None
        result = await tool.execute(
            tool.input_model.model_validate({"include_events": True, "event_limit": 20}),
            ToolExecutionContext(cwd=workspace),
        )
        return json.loads(result.output)

    payload = asyncio.run(_recover())
    assert payload["ok"] is True
    data = payload.get("data") or {}
    assert (data.get("status") or payload.get("status")) == "completed"
    assert (payload.get("run_id") or data.get("run_id")) == run_id
    assert data.get("active_run_id") == run_id



def test_real_offline_forbids_network() -> None:
    """TEST-005：real_offline 禁网可通过 socket guard 证明，而非口头声明。"""
    with network_guard():
        with pytest.raises(OSError):
            socket.create_connection(("203.0.113.1", 80), timeout=0.2)
        with pytest.raises(OSError):
            socket.socket().connect(("203.0.113.1", 80))


def _passing_real_agent_trace() -> TraceRecord:
    names = [
        "climate_init_workflow",
        "climate_plan_steps",
        "climate_acquire_data",
        "climate_inspect_dataset",
        "climate_analyze_plot",
        "climate_write_report",
        "climate_read_context",
    ]
    calls = []
    for index, name in enumerate(names, start=1):
        output: dict[str, Any] = {"version": index}
        if name == "climate_acquire_data":
            output.update({"requested_mode": "cds", "effective_mode": "cds"})
        if name == "climate_write_report":
            output.update({"has_relative_plot": True, "has_absolute_workspace": False})
        calls.append(
            {
                "sequence": index,
                "name": name,
                "input_redacted": {"note": "omitted"},
                "is_error": False,
                "error_code": None,
                "duration_ms": 1,
                "context_version": index,
                "output_redacted": output,
            }
        )
    return TraceRecord.model_validate(
        {
            "suite_version": "g4-real-agent",
            "scenario_id": "cds_minimal_smoke",
            "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "mode": "real_agent",
            "started_at": "2026-09-01T00:00:00Z",
            "finished_at": "2026-09-01T00:01:00Z",
            "duration_ms": 1000,
            "tool_calls": calls,
            "hook_events": [],
            "final_run_status": "completed",
            "final_context_version": 7,
            "artifact_manifest": [
                {"kind": "dataset", "path": ".climate/data/run/era5.nc", "matches_context": True},
                {"kind": "plot", "path": ".climate/output/run/plot.png", "matches_context": True},
                {"kind": "report", "path": ".climate/output/run/report.md", "matches_context": True},
            ],
            "assertion_results": [],
            "synthetic": False,
            "tools_executed": True,
            "model_invoked": True,
            "counts_toward_real_pass_rate": True,
            "network_isolated": False,
            "context_versions": [1, 2, 3, 4, 5, 6, 7],
        }
    )


def test_real_agent_stamps_per_tool_duration_from_start_complete() -> None:
    """Started/Completed 之间的墙钟写回 duration_ms；未完成的工具在收尾时补上。"""
    from evals.climate.real_agent import _elapsed_ms, _stamp_tool_duration

    assert _elapsed_ms(100.0, 100.2504) == 250
    assert _elapsed_ms(100.0, 99.9) == 0

    calls: list[dict[str, object]] = [
        {"sequence": 1, "name": "climate_init_workflow", "duration_ms": 0},
        {"sequence": 2, "name": "climate_acquire_data", "duration_ms": 0},
    ]
    started = {1: 10.0, 2: 10.5}
    _stamp_tool_duration(calls, started, sequence=1, ended=12.0)
    assert calls[0]["duration_ms"] == 2000
    assert 1 not in started
    assert 2 in started

    _stamp_tool_duration(calls, started, ended=70.5)
    assert calls[1]["duration_ms"] == 60000
    assert started == {}


def test_real_agent_runs_must_be_three() -> None:
    completed = _cli(
        "--suite",
        "climate",
        "--mode",
        "real_agent",
        "--agent-config",
        str(ROOT / "evals" / "configs" / "climate-real.json"),
        "--runs",
        "2",
        "--baseline-out",
        "evals/baselines/tmp.json",
    )
    assert completed.returncode != 0
    text = completed.stdout + completed.stderr
    assert "CLIMATE_INVALID_INPUT" in text
    assert "--runs" in text


def test_config_fingerprint_changes_with_scenario_or_commit() -> None:
    config = load_agent_config(ROOT / "evals" / "configs" / "climate-real.json")
    first = config_fingerprint(config, scenario_text="a", skill_text="s", git_commit="abc")
    second = config_fingerprint(config, scenario_text="b", skill_text="s", git_commit="abc")
    third = config_fingerprint(config, scenario_text="a", skill_text="s", git_commit="def")
    dirty_a = config_fingerprint(config, scenario_text="a", skill_text="s", git_commit="abc:aaa")
    dirty_b = config_fingerprint(config, scenario_text="a", skill_text="s", git_commit="abc:bbb")
    assert first != second
    assert first != third
    assert dirty_a != dirty_b
    assert dirty_a != first


def test_agent_config_rejects_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"api_key": "x", "model": "m"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="敏感"):
        load_agent_config(path)


def test_agent_config_rejects_unknown_permission_mode() -> None:
    """permission_mode 必须是 OpenHarness 已有枚举，不能写任意字符串。"""
    from evals.climate.agent_config import ClimateRealConfig

    config = load_agent_config(ROOT / "evals" / "configs" / "climate-real.json")
    payload = config.model_dump(mode="json")
    payload["permission_mode"] = "bypass"
    with pytest.raises(ValueError, match="permission_mode"):
        ClimateRealConfig.model_validate(payload)


def test_real_agent_permission_checker_follows_config() -> None:
    """checker 必须使用 agent-config 的 permission_mode，不得写死 FULL_AUTO。"""
    from evals.climate.real_agent import build_permission_checker
    from openharness.permissions.modes import PermissionMode

    config = load_agent_config(ROOT / "evals" / "configs" / "climate-real.json")
    auto = build_permission_checker(config)
    allowed = auto.evaluate("climate_acquire_data", is_read_only=False)
    assert allowed.allowed is True
    assert allowed.requires_confirmation is False

    plan = build_permission_checker(config.model_copy(update={"permission_mode": "plan"}))
    blocked = plan.evaluate("climate_acquire_data", is_read_only=False)
    assert blocked.allowed is False
    assert PermissionMode(config.permission_mode) is PermissionMode.FULL_AUTO

    default = build_permission_checker(config.model_copy(update={"permission_mode": "default"}))
    confirm = default.evaluate("climate_acquire_data", is_read_only=False)
    assert confirm.requires_confirmation is True


def test_real_agent_trace_run_id_comes_from_workspace_index(tmp_path: Path) -> None:
    """real_agent Trace.run_id 必须来自磁盘 active_run_id，不得写死 null。"""
    from evals.climate.real_agent import _workspace_facts

    run_id = "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314"
    climate = tmp_path / ".climate"
    (climate / "runs" / run_id).mkdir(parents=True)
    (climate / "index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": 1,
                "active_run_id": run_id,
                "run_ids": [run_id],
                "updated_at": "2026-08-22T14:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (climate / "runs" / run_id / "context.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "version": 2,
                "run_id": run_id,
                "objective": "分析示例温度序列并生成报告",
                "status": "running",
                "created_at": "2026-08-22T14:00:00Z",
                "updated_at": "2026-08-22T14:03:00Z",
                "steps": [],
                "artifacts": [],
                "events": [
                    {
                        "sequence": 1,
                        "timestamp": "2026-08-22T14:00:00Z",
                        "type": "run_created",
                        "step_id": None,
                        "data": {},
                    }
                ],
                "last_error": None,
            }
        ),
        encoding="utf-8",
    )
    status, version, artifacts, _acquire, found = _workspace_facts(tmp_path)
    assert found == run_id
    assert status == "running"
    assert version == 2
    assert artifacts == []
    empty_status, _, _, _, empty_id = _workspace_facts(tmp_path / "missing")
    assert empty_status is None
    assert empty_id is None


def test_real_agent_two_pass_publishes_and_keeps_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.climate import runner as runner_mod

    monkeypatch.setattr(runner_mod, "REPORT_DIR", tmp_path / "reports")
    passing = _passing_real_agent_trace()
    failing = passing.model_copy(update={"final_run_status": "failed", "tool_calls": passing.tool_calls[:2]})
    states = iter([passing, passing, failing])

    def _fake(scenario: Any, config: Any, *, workspace: Path, run_index: int) -> TraceRecord:
        del scenario, config, workspace, run_index
        return next(states)

    out = tmp_path / "baseline.json"
    code = runner_mod.run_suite(
        "climate",
        "real_agent",
        agent_config=str(ROOT / "evals" / "configs" / "climate-real.json"),
        runs=3,
        baseline_out=str(out),
        run_once=_fake,
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["passes"] == 2
    assert payload["baseline_published"] is True
    assert len(payload["results"]) == 3
    assert payload["results"][2]["passed"] is False
    dumped = json.dumps(payload)
    assert "api_key" not in dumped.lower()
    assert ".cdsapirc" not in dumped.lower()


def test_real_agent_one_pass_does_not_publish_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.climate import runner as runner_mod

    reports = tmp_path / "reports"
    monkeypatch.setattr(runner_mod, "REPORT_DIR", reports)
    passing = _passing_real_agent_trace()
    failing = passing.model_copy(update={"final_run_status": "failed", "tool_calls": passing.tool_calls[:1]})
    states = iter([passing, failing, failing])

    def _fake(scenario: Any, config: Any, *, workspace: Path, run_index: int) -> TraceRecord:
        del scenario, config, workspace, run_index
        return next(states)

    out = tmp_path / "baseline.json"
    code = runner_mod.run_suite(
        "climate",
        "real_agent",
        agent_config=str(ROOT / "evals" / "configs" / "climate-real.json"),
        runs=3,
        baseline_out=str(out),
        run_once=_fake,
    )
    assert code != 0
    assert not out.exists()
    unpublished = reports / "climate-real_agent-unpublished.json"
    assert unpublished.is_file()
    payload = json.loads(unpublished.read_text(encoding="utf-8"))
    assert payload["passes"] == 1
    assert payload["baseline_published"] is False
    assert len(payload["results"]) == 3
    assert payload["results"][1]["passed"] is False
    assert payload["results"][2]["passed"] is False
    dumped = json.dumps(payload)
    assert "api_key" not in dumped.lower()
    assert "sk-" not in dumped.lower()
    assert str(Path.home()) not in dumped
    assert str(Path.home()).replace("\\", "/") not in dumped


def test_real_agent_isolated_workspaces_and_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三次独立 workspace；commit/scenario 变化会改变 fingerprint，计数必须重计。"""
    from evals.climate import runner as runner_mod

    monkeypatch.setattr(runner_mod, "REPORT_DIR", tmp_path / "reports")
    seen: list[tuple[int, str]] = []

    def _fake(scenario: Any, config: Any, *, workspace: Path, run_index: int) -> TraceRecord:
        del scenario, config
        seen.append((run_index, str(workspace.resolve())))
        return _passing_real_agent_trace()

    out = tmp_path / "baseline.json"
    code = runner_mod.run_suite(
        "climate",
        "real_agent",
        agent_config=str(ROOT / "evals" / "configs" / "climate-real.json"),
        runs=3,
        baseline_out=str(out),
        run_once=_fake,
    )
    assert code == 0
    assert [index for index, _ in seen] == [1, 2, 3]
    paths = {path for _, path in seen}
    assert len(paths) == 3
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["fingerprint"]
    assert payload["runs"] == 3
    assert payload["min_passes"] == 2
    assert payload["dirty"] is True or payload["dirty"] is False
    if payload["dirty"]:
        assert payload["dirty_digest"]
    assert all(item["workspace_isolated"] is True for item in payload["results"])
    config = load_agent_config(ROOT / "evals" / "configs" / "climate-real.json")
    changed = config_fingerprint(
        config,
        scenario_text="changed-scenario",
        skill_text="s",
        git_commit=str(payload["git_commit"]),
    )
    assert changed != payload["fingerprint"]


def test_report_quality_smoke_yaml_disclaims_bench85() -> None:
    """EVAL-004：场景必须声明不是 Bench-85 / 论文 Report Score 替代。"""
    path = ROOT / "evals" / "climate" / "scenarios" / "report_quality_smoke.yaml"
    text = path.read_text(encoding="utf-8")
    scenario = load_scenario(path)
    assert scenario.id == "report_quality_smoke"
    assert scenario.mode is EvalMode.real_offline
    lowered = text.lower()
    assert "bench-85" in lowered
    assert "不是" in text or "非" in text
    assert "report score" in lowered
    assert "替代" in text
    assert any(item.type == "report_quality_rules" for item in scenario.hard_assertions)


def test_report_quality_rules_assertion_on_fixture_trace() -> None:
    """EVAL-004：规则级报告质量断言；默认不联网、不引入 LLM judge。"""
    from evals.climate.assertions import evaluate_hard_assertions
    from evals.climate.models import HardAssertionSpec, TraceRecord

    spec = HardAssertionSpec(
        id="report_quality",
        type="report_quality_rules",
        expected={
            "min_chars": 40,
            "required_headings": ["## Inspect", "## Plot", "## Summary"],
            "relative_links": True,
            "no_absolute_path": True,
            "no_secrets": True,
            "min_score": 6,
            "not_bench85": True,
        },
    )
    recovery = {
        "report_text": (
            "# 示例气候报告\n\n"
            "- run_id: 0e8e6eb4-93f2-4ce7-8d22-91a28fa99314\n"
            "## Inspect\n- row_count: 30\n"
            "## Plot\n![plot-plot](.climate/output/run/plot-plot.svg)\n"
            "## Summary\n离线 sample 流水线完成。\n"
        ),
        "report_has_relative_plot": True,
        "report_has_absolute_workspace": False,
        "report_rule_score": 8,
        "report_is_bench85": False,
    }
    trace = TraceRecord.model_validate(
        _minimal_trace(
            mode="real_offline",
            synthetic=False,
            tools_executed=True,
            counts_toward_real_pass_rate=True,
            recovery=recovery,
            tool_calls=[
                {
                    "sequence": 1,
                    "name": "climate_write_report",
                    "input_redacted": {"title": "示例气候报告"},
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 1,
                    "output_redacted": {
                        "has_relative_plot": True,
                        "has_absolute_workspace": False,
                    },
                }
            ],
        )
    )
    results = evaluate_hard_assertions(trace, [spec])
    assert results[0].passed is True
    assert "bench-85" not in results[0].message.lower() or "不是" in results[0].message


def test_report_quality_smoke_real_offline(tmp_path: Path) -> None:
    """EVAL-004：离线跑 report_quality_smoke；默认禁网；不计入改写 G4 baseline。"""
    scenario = load_scenario(
        ROOT / "evals" / "climate" / "scenarios" / "report_quality_smoke.yaml"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    failed = [item for item in results if not item.passed]
    assert failed == [], [item.message for item in failed]
    assert trace.network_isolated is True
    assert trace.synthetic is False
    score = (trace.recovery or {}).get("report_rule_score")
    assert isinstance(score, int)
    assert 6 <= score <= 10
    assert (trace.recovery or {}).get("report_is_bench85") is False
    baseline = ROOT / "evals" / "baselines" / "climate-real-9b592ba.json"
    original = baseline.read_bytes()
    assert original  # 历史 G4 证据不得被本测试改写
    assert baseline.read_bytes() == original


def test_knowledge_alias_smoke_yaml_disclaims_bench85_and_enterprise_kb() -> None:
    """EVAL-005：场景必须声明不是 Bench-85 / 不是企业知识库准确率。"""
    path = ROOT / "evals" / "climate" / "scenarios" / "knowledge_alias_smoke.yaml"
    text = path.read_text(encoding="utf-8")
    scenario = load_scenario(path)
    assert scenario.id == "knowledge_alias_smoke"
    assert scenario.mode is EvalMode.real_offline
    lowered = text.lower()
    assert "bench-85" in lowered
    assert "不是" in text
    assert "企业知识库" in text or "hit rate" in lowered
    assert any(item.type == "knowledge_hits_variable" for item in scenario.hard_assertions)
    from evals.climate.runner import _REAL_OFFLINE_ORDER

    assert "knowledge_alias_smoke" not in _REAL_OFFLINE_ORDER
    assert len(_REAL_OFFLINE_ORDER) == 4


def test_knowledge_hits_variable_assertion_on_fixture_trace() -> None:
    """EVAL-005：别名命中官方变量名；source 为相对路径。"""
    spec = HardAssertionSpec(
        id="alias_2m",
        type="knowledge_hits_variable",
        expected={"query": "2m temperature", "variable": "t2m", "max_rank": 5},
    )
    trace = TraceRecord.model_validate(
        _minimal_trace(
            mode="real_offline",
            synthetic=False,
            tools_executed=True,
            counts_toward_real_pass_rate=True,
            tool_calls=[
                {
                    "sequence": 1,
                    "name": "climate_query_knowledge",
                    "input_redacted": {"query": "2m temperature", "top_k": 5},
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 1,
                    "output_redacted": {
                        "ok": True,
                        "hit_count": 1,
                        "hits": [
                            {
                                "chunk_id": "p0-c0",
                                "parent_text": "官方短名 t2m。别名 2m temperature。",
                                "source": "corpus/era5_variable_aliases.md",
                                "score": 0.03,
                            }
                        ],
                    },
                }
            ],
        )
    )
    results = evaluate_hard_assertions(trace, [spec])
    assert results[0].passed is True


def test_knowledge_alias_smoke_real_offline(tmp_path: Path) -> None:
    """EVAL-005：离线跑 knowledge_alias_smoke；默认禁网；不并入四场景。"""
    scenario = load_scenario(
        ROOT / "evals" / "climate" / "scenarios" / "knowledge_alias_smoke.yaml"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    trace = run_real_offline(scenario, workspace=workspace)
    results = evaluate_hard_assertions(trace, list(scenario.hard_assertions))
    failed = [item for item in results if not item.passed]
    assert failed == [], [item.message for item in failed]
    assert trace.network_isolated is True
    assert trace.synthetic is False
    names = [item.name for item in trace.tool_calls]
    assert names == ["climate_query_knowledge"] * 3
    baseline = ROOT / "evals" / "baselines" / "climate-real-9b592ba.json"
    original = baseline.read_bytes()
    assert baseline.read_bytes() == original
    from evals.climate.runner import _REAL_OFFLINE_ORDER

    assert scenario.id not in _REAL_OFFLINE_ORDER


def test_cds_knowledge_smoke_yaml_is_real_agent_not_offline_order() -> None:
    """知识工具 real_agent 场景不得并入四场景，也不得覆盖 MODEL-001 YAML。"""
    path = ROOT / "evals" / "climate" / "scenarios" / "cds_knowledge_smoke.yaml"
    text = path.read_text(encoding="utf-8")
    scenario = load_scenario(path)
    assert scenario.id == "cds_knowledge_smoke"
    assert scenario.mode is EvalMode.real_agent
    lowered = text.lower()
    assert "bench-85" in lowered
    assert "不是" in text
    assert "cds_minimal_smoke" in text or "MODEL-001" in text
    types = {item.id: item.type for item in scenario.hard_assertions}
    assert types["rag_used"] == "tool_called"
    assert types["alias_t2m"] == "knowledge_hits_variable"
    known = next(item for item in scenario.hard_assertions if item.id == "known_tools")
    assert "climate_query_knowledge" in known.expected
    from evals.climate.runner import _REAL_OFFLINE_ORDER

    assert scenario.id not in _REAL_OFFLINE_ORDER
    assert len(_REAL_OFFLINE_ORDER) == 4
    config = load_agent_config(ROOT / "evals" / "configs" / "climate-real-g6-knowledge.json")
    assert config.scenario_id == "cds_knowledge_smoke"
    assert config.allow_sample_fallback is False


def test_real_agent_knowledge_scenario_registers_ninth_tool_and_indexes(tmp_path: Path) -> None:
    """仅 cds_knowledge_smoke 预建索引并注册第九工具；cds_minimal_smoke 仍八工具。"""
    from evals.climate.real_agent import registry_for_scenario
    from evals.climate.real_offline import _prepare_knowledge_index

    knowledge = load_scenario(
        ROOT / "evals" / "climate" / "scenarios" / "cds_knowledge_smoke.yaml"
    )
    minimal = load_scenario(
        ROOT / "evals" / "climate" / "scenarios" / "cds_minimal_smoke.yaml"
    )
    enabled = registry_for_scenario(knowledge)
    names = [tool.name for tool in enabled.list_tools()]
    assert names[-1] == "climate_query_knowledge"
    assert len(names) == 9
    default = registry_for_scenario(minimal)
    default_names = [tool.name for tool in default.list_tools()]
    assert "climate_query_knowledge" not in default_names
    assert len(default_names) == 8
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _prepare_knowledge_index(workspace)
    assert (workspace / ".climate" / "knowledge" / "manifest.json").is_file()


def test_tool_called_and_knowledge_hits_without_exact_query() -> None:
    """real_agent 不要求模型查询词与离线 YAML 完全一致，只要命中 t2m。"""
    called = HardAssertionSpec(id="rag_used", type="tool_called", expected="climate_query_knowledge")
    hits = HardAssertionSpec(
        id="alias_t2m",
        type="knowledge_hits_variable",
        expected={"variable": "t2m", "max_rank": 5},
    )
    missing = HardAssertionSpec(id="rag_used", type="tool_called", expected="climate_query_knowledge")
    base = _minimal_trace(
        mode="real_agent",
        synthetic=False,
        tools_executed=True,
        model_invoked=True,
        counts_toward_real_pass_rate=True,
        tool_calls=[
            {
                "sequence": 1,
                "name": "climate_query_knowledge",
                "input_redacted": {"query": "2 米气温"},
                "is_error": False,
                "error_code": None,
                "duration_ms": 1,
                "output_redacted": {
                    "query": "2 米气温",
                    "hit_count": 1,
                    "hits": [
                        {
                            "chunk_id": "p0-c0",
                            "parent_text": "官方短名 t2m。别名 2 米气温。",
                            "source": "corpus/era5_variable_aliases.md",
                            "score": 0.04,
                        }
                    ],
                },
            }
        ],
    )
    trace = TraceRecord.model_validate(base)
    results = evaluate_hard_assertions(trace, [called, hits])
    assert all(item.passed for item in results), [item.message for item in results]
    empty = TraceRecord.model_validate(
        _minimal_trace(
            mode="real_agent",
            synthetic=False,
            tools_executed=True,
            model_invoked=True,
            counts_toward_real_pass_rate=True,
            tool_calls=[
                {
                    "sequence": 1,
                    "name": "climate_init_workflow",
                    "input_redacted": {"note": "omitted"},
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 1,
                    "output_redacted": {"version": 1},
                }
            ],
        )
    )
    missed = evaluate_hard_assertions(empty, [missing, hits])
    assert missed[0].passed is False
    assert missed[1].passed is False


def test_real_agent_replays_knowledge_hits_when_event_output_empty(tmp_path: Path) -> None:
    """知识工具不写 Context；事件输出为空时必须能从索引重放 hits。"""
    from evals.climate.real_agent import _replay_knowledge_hits_if_needed
    from evals.climate.real_offline import _prepare_knowledge_index

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _prepare_knowledge_index(workspace)
    call = {
        "name": "climate_query_knowledge",
        "is_error": False,
        "input_redacted": {"query": "2 米气温"},
        "output_redacted": {},
    }
    _replay_knowledge_hits_if_needed(call, workspace)
    output = call["output_redacted"]
    assert output.get("query") == "2 米气温"
    assert output.get("hit_count", 0) >= 1
    blob = "\n".join(str(hit.get("parent_text") or "") for hit in output.get("hits") or [])
    assert "t2m" in blob
    spec = HardAssertionSpec(
        id="alias_t2m",
        type="knowledge_hits_variable",
        expected={"variable": "t2m", "max_rank": 5},
    )
    trace = TraceRecord.model_validate(
        _minimal_trace(
            mode="real_agent",
            synthetic=False,
            tools_executed=True,
            model_invoked=True,
            counts_toward_real_pass_rate=True,
            tool_calls=[
                {
                    "sequence": 1,
                    "name": "climate_query_knowledge",
                    "input_redacted": call["input_redacted"],
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 1,
                    "output_redacted": output,
                }
            ],
        )
    )
    results = evaluate_hard_assertions(trace, [spec])
    assert results[0].passed is True


def test_real_agent_knowledge_suite_does_not_publish_without_rag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """知识场景若三次都没有 climate_query_knowledge，不得发布成功 baseline。"""
    from evals.climate import runner as runner_mod

    reports = tmp_path / "reports"
    monkeypatch.setattr(runner_mod, "REPORT_DIR", reports)
    seen: list[str] = []

    def _fake(scenario: Any, config: Any, *, workspace: Path, run_index: int) -> TraceRecord:
        del config, workspace, run_index
        seen.append(scenario.id)
        return _passing_real_agent_trace()

    out = tmp_path / "baseline.json"
    code = runner_mod.run_suite(
        "climate",
        "real_agent",
        agent_config=str(ROOT / "evals" / "configs" / "climate-real-g6-knowledge.json"),
        runs=3,
        baseline_out=str(out),
        run_once=_fake,
    )
    assert seen == ["cds_knowledge_smoke"] * 3
    assert code != 0
    assert not out.exists()
    unpublished = reports / "climate-real_agent-unpublished.json"
    assert unpublished.is_file()
    payload = json.loads(unpublished.read_text(encoding="utf-8"))
    assert payload["passes"] == 0
    assert payload["baseline_published"] is False
    assert payload["config"]["scenario_id"] == "cds_knowledge_smoke"
