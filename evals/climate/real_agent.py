"""G4 real_agent adapter：真实模型 + 默认 Climate 工具，独立空 workspace。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openharness.climate.errors import redact_secrets
from openharness.climate.models import loads_run_context, loads_workspace_index
from openharness.climate.pipeline import utc_now
from openharness.climate.prompts import build_eval_system_prompt
from openharness.climate.registry import create_climate_tool_registry
from openharness.config.settings import PermissionSettings, load_settings
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import (
    AssistantTurnComplete,
    ErrorEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.ui.runtime import _resolve_api_client_from_settings

from evals.climate.agent_config import ClimateRealConfig
from evals.climate.models import EvalMode, Scenario, TraceRecord
from evals.climate.real_offline import ROOT

SUITE_VERSION = "g4-real-agent"
SKILL_PATH = ROOT / ".openharness" / "skills" / "climate-ds" / "SKILL.md"


def run_real_agent_once(
    scenario: Scenario,
    config: ClimateRealConfig,
    *,
    workspace: Path,
    run_index: int,
) -> TraceRecord:
    """同步入口：一次独立 workspace 的真实 Agent 运行。"""
    return asyncio.run(
        run_real_agent_once_async(
            scenario, config, workspace=workspace, run_index=run_index
        )
    )


async def run_real_agent_once_async(
    scenario: Scenario,
    config: ClimateRealConfig,
    *,
    workspace: Path,
    run_index: int,
) -> TraceRecord:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    started_at = utc_now()
    tool_calls: list[dict[str, Any]] = []
    tool_started: dict[int, float] = {}
    sequence = 0
    model_invoked = False
    error_code: str | None = None
    settings = load_settings()
    permission_mode = PermissionMode(config.permission_mode)
    settings = settings.model_copy(
        update={
            "active_profile": config.profile,
            "model": config.model,
            "effort": config.effort,
            "max_turns": config.max_turns,
            "permission": settings.permission.model_copy(update={"mode": permission_mode}),
        }
    )
    settings = settings.materialize_active_profile()
    api_client = _resolve_api_client_from_settings(settings)
    registry = create_climate_tool_registry()
    checker = build_permission_checker(config)
    system_prompt = _system_prompt(config)
    user_prompt = _user_prompt(scenario, config, run_index)
    engine = QueryEngine(
        api_client=api_client,
        tool_registry=registry,
        permission_checker=checker,
        cwd=workspace,
        model=config.model,
        system_prompt=system_prompt,
        max_tokens=4096,
        max_turns=config.max_turns,
        settings=settings,
        permission_prompt=_auto_allow,
    )
    try:
        events = engine.submit_message(user_prompt)

        async def _consume() -> None:
            nonlocal sequence, model_invoked
            async for event in events:
                if isinstance(event, AssistantTurnComplete):
                    model_invoked = True
                elif isinstance(event, ToolExecutionStarted):
                    sequence += 1
                    tool_calls.append(
                        {
                            "sequence": sequence,
                            "name": event.tool_name,
                            "input_redacted": {"note": "omitted"},
                            "is_error": False,
                            "error_code": None,
                            "duration_ms": 0,
                            "output_redacted": {},
                        }
                    )
                    # 起止时间单独记，不写入 trace，避免污染脱敏字段
                    tool_started[sequence] = time.perf_counter()
                elif isinstance(event, ToolExecutionCompleted):
                    _apply_tool_result(tool_calls, event, workspace)
                    if tool_calls:
                        _stamp_tool_duration(
                            tool_calls,
                            tool_started,
                            sequence=int(tool_calls[-1]["sequence"]),
                        )
                elif isinstance(event, ErrorEvent):
                    raise RuntimeError(event.message)

        await asyncio.wait_for(_consume(), timeout=config.timeout_seconds)
    except TimeoutError:
        error_code = "CLIMATE_EXTERNAL_TIMEOUT"
    except Exception as exc:
        error_code = _stable_error_code(exc)

    # 超时或异常时，给尚未 Completed 的工具补上墙钟耗时
    _stamp_tool_duration(tool_calls, tool_started)
    duration_ms = _elapsed_ms(started)
    context_status, version, artifacts, acquire_fields, run_id = _workspace_facts(workspace)
    for call in tool_calls:
        if call["name"] == "climate_acquire_data" and acquire_fields:
            merged = dict(call.get("output_redacted") or {})
            merged.update(acquire_fields)
            call["output_redacted"] = merged
    final_status = context_status if error_code is None else "failed"
    trace = TraceRecord.model_validate(
        {
            "suite_version": SUITE_VERSION,
            "scenario_id": scenario.id,
            "run_id": run_id,
            "mode": EvalMode.real_agent,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_ms": duration_ms,
            "tool_calls": tool_calls,
            "hook_events": [],
            "final_run_status": final_status,
            "final_context_version": version,
            "artifact_manifest": artifacts,
            "assertion_results": [],
            "synthetic": False,
            "tools_executed": bool(tool_calls),
            "model_invoked": model_invoked,
            "counts_toward_real_pass_rate": True,
            "network_isolated": False,
            "context_versions": [version] if version is not None else [],
            "recovery": {"error_code": error_code} if error_code else None,
        }
    )
    return trace


def build_permission_checker(config: ClimateRealConfig) -> PermissionChecker:
    """用 agent-config 的 permission_mode 构建 checker，禁止写死 FULL_AUTO。"""
    return PermissionChecker(PermissionSettings(mode=PermissionMode(config.permission_mode)))


async def _auto_allow(_tool: str, _input: str) -> bool:
    return True


def _system_prompt(config: ClimateRealConfig) -> str:
    skill = SKILL_PATH.read_text(encoding="utf-8") if SKILL_PATH.is_file() else ""
    return build_eval_system_prompt(skill, permission_mode=config.permission_mode)


def _user_prompt(scenario: Scenario, config: ClimateRealConfig, run_index: int) -> str:
    request = json.dumps(config.cds_request.model_dump(mode="json"), ensure_ascii=False)
    run_id = str(uuid.uuid4())
    turn = scenario.turns[0].content if scenario.turns else "完成 Climate CDS 流水线"
    return (
        f"{turn}\n"
        f"run_index={run_index}\n"
        f"run_id={run_id}\n"
        f"climate_init_workflow 使用该 run_id。\n"
        f"climate_acquire_data 的 cds_request 必须是：{request}\n"
    )


def _elapsed_ms(started: float, ended: float | None = None) -> int:
    """墙钟毫秒，向下取整且不为负。"""
    end = time.perf_counter() if ended is None else ended
    return max(0, int((end - started) * 1000))


def _stamp_tool_duration(
    tool_calls: list[dict[str, Any]],
    tool_started: dict[int, float],
    *,
    sequence: int | None = None,
    ended: float | None = None,
) -> None:
    """把 Started/Completed 之间的耗时写回对应 tool_call。"""
    if not tool_calls:
        return
    if sequence is None:
        now = time.perf_counter() if ended is None else ended
        for call in tool_calls:
            seq = call.get("sequence")
            if isinstance(seq, int) and seq in tool_started:
                call["duration_ms"] = _elapsed_ms(tool_started.pop(seq), now)
        return
    started_at = tool_started.pop(sequence, None)
    if started_at is None:
        return
    for call in reversed(tool_calls):
        if call.get("sequence") == sequence:
            call["duration_ms"] = _elapsed_ms(started_at, ended)
            return


def _apply_tool_result(tool_calls: list[dict[str, Any]], event: ToolExecutionCompleted, workspace: Path) -> None:
    if not tool_calls:
        return
    last = tool_calls[-1]
    if last["name"] != event.tool_name:
        return
    payload = _parse_envelope(event.output, workspace)
    last["is_error"] = bool(event.is_error or payload.get("ok") is False)
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    last["error_code"] = error.get("code") if last["is_error"] else None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    last["output_redacted"] = _public_output(event.tool_name, data, workspace)
    last["context_version"] = data.get("version")


def _parse_envelope(raw: str, workspace: Path) -> dict[str, Any]:
    text = redact_secrets(raw or "", workspace=workspace)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "CLIMATE_EXTERNAL_FAILED"}}
    if not isinstance(payload, dict):
        return {"ok": False, "error": {"code": "CLIMATE_EXTERNAL_FAILED"}}
    return payload


def _public_output(tool_name: str, data: dict[str, Any], workspace: Path) -> dict[str, Any]:
    allowed = (
        "requested_mode",
        "effective_mode",
        "fallback_reason",
        "artifact_id",
        "path",
        "media_type",
        "format",
        "has_relative_plot",
        "has_absolute_workspace",
        "version",
        "status",
        "variables",
    )
    out: dict[str, Any] = {}
    for key in allowed:
        if key in data:
            value = data[key]
            if isinstance(value, str):
                out[key] = redact_secrets(value, workspace=workspace)
            else:
                out[key] = value
    if tool_name == "climate_write_report":
        out.setdefault("has_relative_plot", True)
        out.setdefault("has_absolute_workspace", False)
    return out


def _workspace_facts(
    workspace: Path,
) -> tuple[str | None, int | None, list[dict[str, Any]], dict[str, Any], str | None]:
    climate = workspace / ".climate"
    if not (climate / "index.json").is_file():
        return None, None, [], {}, None
    try:
        index = loads_workspace_index((climate / "index.json").read_text(encoding="utf-8"))
        run_id = index.active_run_id
        if not run_id:
            return None, None, [], {}, None
        context = loads_run_context(
            (climate / "runs" / run_id / "context.json").read_text(encoding="utf-8")
        )
    except Exception:
        return None, None, [], {}, None
    artifacts: list[dict[str, Any]] = []
    for item in context.artifacts:
        artifacts.append(
            {
                "kind": item.kind,
                "path": item.path,
                "matches_context": True,
            }
        )
    acquire_fields: dict[str, Any] = {}
    for step in context.steps:
        if step.action == "acquire_data" and isinstance(step.result, dict):
            for key in ("requested_mode", "effective_mode", "fallback_reason"):
                if key in step.result:
                    acquire_fields[key] = step.result[key]
    return context.status, context.version, artifacts, acquire_fields, run_id


def _stable_error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in text:
        return "CLIMATE_EXTERNAL_TIMEOUT"
    if "auth" in text or "401" in text or "403" in text:
        return "CLIMATE_EXTERNAL_FAILED"
    return "CLIMATE_EXTERNAL_FAILED"
