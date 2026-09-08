"""real_offline adapter：真实调用 Climate 工具，禁网，Trace 来自磁盘事实。"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import shlex
import socket
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from openharness.climate.errors import ClimateError, redact_secrets
from openharness.climate.models import loads_run_context, loads_workspace_index
from openharness.climate.pipeline import utc_now
from openharness.climate.registry import create_climate_tool_registry
from openharness.hooks.events import HookEvent
from openharness.hooks.executor import HookExecutionContext, HookExecutor
from openharness.hooks.loader import HookRegistry
from openharness.hooks.schemas import CommandHookDefinition
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.utils.shell import resolve_shell_command

from evals.climate.models import (
    EvalMode,
    Scenario,
    ScenarioToolInvocation,
    TraceRecord,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "evals" / "climate" / "fixtures"
SUITE_VERSION = "g3-real-offline"
SESSION_SENTINEL = "eval-session-1-sentinel"
OUTPUT_GUARD_SCENARIO_ID = "pre_tool_output_guard"
KNOWLEDGE_ALIAS_SCENARIO_ID = "knowledge_alias_smoke"
OUTPUT_GUARD_MATCHER = "climate_write_report"
OUTPUT_GUARD_MARKER = "blocked-output-secret"
KNOWLEDGE_FIXTURE_DIR = ROOT / "tests" / "fixtures" / "climate_knowledge"


class NetworkBlockedError(OSError):
    """real_offline 禁止任何出站网络。"""


class _GuardedSocket(socket.socket):
    def connect(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        raise NetworkBlockedError("real_offline 禁止网络")

    def connect_ex(self, *args: Any, **kwargs: Any) -> int:  # type: ignore[override]
        raise NetworkBlockedError("real_offline 禁止网络")


def _blocked_create_connection(*_args: Any, **_kwargs: Any) -> Any:
    raise NetworkBlockedError("real_offline 禁止网络")


@contextmanager
def network_guard() -> Iterator[None]:
    """替换 socket 出站入口；不伪造工具成功。"""
    original_socket = socket.socket
    original_create = socket.create_connection
    socket.socket = _GuardedSocket  # type: ignore[misc]
    socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[misc]
        socket.create_connection = original_create


def run_real_offline(scenario: Scenario, *, workspace: Path) -> TraceRecord:
    """同步入口：在临时或调用方 workspace 中真实执行 scenario。"""
    return asyncio.run(
        run_real_offline_async(scenario, workspace=workspace),
        debug=False,
    )


async def run_real_offline_async(scenario: Scenario, *, workspace: Path) -> TraceRecord:
    """真实执行 Climate 工具并采集 Trace；异常转为 error_code，不含 traceback。"""
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    _materialize_inputs(scenario, workspace)
    if scenario.id == KNOWLEDGE_ALIAS_SCENARIO_ID:
        _prepare_knowledge_index(workspace)
    started = time.perf_counter()
    started_at = utc_now()
    tool_calls: list[dict[str, Any]] = []
    recovery: dict[str, Any] = {
        "session_boundary": False,
        "session1_destroyed": False,
        "read_context_before_continue": False,
        "inherited_tool_metadata": False,
        "recovery_source": None,
        "source_unmodified": None,
    }
    source_before = _source_snapshot(workspace, scenario)
    if not scenario.tool_invocations:
        raise ClimateError(
            code="CLIMATE_INVALID_INPUT",
            message="real_offline scenario 缺少 tool_invocations",
            retryable=False,
            details={"field": "tool_invocations"},
        )

    grouped = _group_sessions(list(scenario.tool_invocations))
    sequence = 0
    last_run_id: str | None = None
    versions: list[int] = []
    hook_events: list[dict[str, Any]] = []
    network_isolated = False
    hook_executor = (
        _build_output_guard_executor(workspace)
        if scenario.id == OUTPUT_GUARD_SCENARIO_ID
        else None
    )

    try:
        with network_guard():
            network_isolated = True
            previous_meta_id: int | None = None
            for session_id, invocations in grouped:
                if session_id > 1:
                    recovery["session_boundary"] = True
                    gc.collect()
                    recovery["session1_destroyed"] = True
                registry = create_climate_tool_registry(
                    include_knowledge=scenario.id == KNOWLEDGE_ALIAS_SCENARIO_ID
                )
                metadata: dict[str, Any] = {}
                if session_id == 1:
                    metadata["eval_session"] = SESSION_SENTINEL
                context = ToolExecutionContext(cwd=workspace, metadata=metadata)
                if session_id == 1:
                    previous_meta_id = id(context.metadata)
                    recovery["session1_metadata_id"] = previous_meta_id
                else:
                    recovery["session2_context_is_new"] = True
                    recovery["session2_metadata_keys"] = sorted(context.metadata.keys())
                    recovery["inherited_tool_metadata"] = (
                        SESSION_SENTINEL in context.metadata
                        or (previous_meta_id is not None and id(context.metadata) == previous_meta_id)
                    )
                    if not invocations or invocations[0].name != "climate_read_context":
                        recovery["recovery_source"] = "missing_read_context"
                    else:
                        recovery["recovery_source"] = "disk_context"
                remaining = scenario.timeout_seconds - (time.perf_counter() - started)
                if remaining <= 0:
                    raise TimeoutError("scenario timeout")
                for invocation in invocations:
                    sequence += 1
                    call, maybe_run_id, version = await asyncio.wait_for(
                        _invoke_one(
                            registry,
                            context,
                            invocation,
                            sequence=sequence,
                            workspace=workspace,
                            hook_executor=hook_executor,
                            hook_events=hook_events,
                            recovery=recovery,
                        ),
                        timeout=max(remaining, 0.1),
                    )
                    tool_calls.append(call)
                    if maybe_run_id:
                        last_run_id = maybe_run_id
                    if version is not None:
                        versions.append(version)
                    if (
                        session_id > 1
                        and invocation.name == "climate_read_context"
                        and call.get("is_error") is False
                    ):
                        recovery["read_context_before_continue"] = True
                        payload = call.get("output_redacted") or {}
                        if payload.get("active_run_id") or last_run_id:
                            recovery["active_run_id"] = payload.get("active_run_id") or last_run_id
                    remaining = scenario.timeout_seconds - (time.perf_counter() - started)
                    if remaining <= 0:
                        raise TimeoutError("scenario timeout")
                del registry
                del context
    except TimeoutError:
        tool_calls.append(
            {
                "sequence": sequence + 1,
                "name": "timeout",
                "input_redacted": {},
                "is_error": True,
                "error_code": "CLIMATE_EXTERNAL_TIMEOUT",
                "duration_ms": 0,
                "context_version": versions[-1] if versions else None,
                "output_redacted": {},
                "session": grouped[-1][0] if grouped else 1,
            }
        )
    except NetworkBlockedError:
        tool_calls.append(
            {
                "sequence": sequence + 1,
                "name": "network",
                "input_redacted": {},
                "is_error": True,
                "error_code": "CLIMATE_EXTERNAL_FAILED",
                "duration_ms": 0,
                "output_redacted": {"reason": "network_blocked"},
                "session": 1,
            }
        )
    except ClimateError as exc:
        tool_calls.append(
            {
                "sequence": sequence + 1,
                "name": "adapter",
                "input_redacted": {},
                "is_error": True,
                "error_code": exc.code,
                "duration_ms": 0,
                "output_redacted": {"message": redact_secrets(exc.message, workspace=workspace)},
                "session": 1,
            }
        )
    except Exception as exc:
        tool_calls.append(
            {
                "sequence": sequence + 1,
                "name": "adapter",
                "input_redacted": {},
                "is_error": True,
                "error_code": "CLIMATE_EXTERNAL_FAILED",
                "duration_ms": 0,
                "output_redacted": {"reason": type(exc).__name__},
                "session": 1,
            }
        )

    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source_after = _source_snapshot(workspace, scenario)
    if source_before is not None:
        recovery["source_unmodified"] = source_before == source_after
        recovery["source_copied"] = True
    _annotate_report_links(tool_calls, workspace, last_run_id, recovery)
    manifest, final_status, final_version, run_id = _collect_disk_facts(workspace, last_run_id)

    return TraceRecord.model_validate(
        {
            "suite_version": SUITE_VERSION,
            "scenario_id": scenario.id,
            "run_id": run_id,
            "mode": EvalMode.real_offline,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "tool_calls": tool_calls,
            "hook_events": hook_events,
            "final_run_status": final_status,
            "final_context_version": final_version,
            "artifact_manifest": manifest,
            "assertion_results": [],
            "synthetic": False,
            "tools_executed": True,
            "model_invoked": False,
            "counts_toward_real_pass_rate": True,
            "network_isolated": network_isolated,
            "context_versions": versions,
            "recovery": _redact_payload(recovery, workspace),
        }
    )


def _group_sessions(
    invocations: list[ScenarioToolInvocation],
) -> list[tuple[int, list[ScenarioToolInvocation]]]:
    grouped: list[tuple[int, list[ScenarioToolInvocation]]] = []
    for item in invocations:
        if not grouped or grouped[-1][0] != item.session:
            grouped.append((item.session, [item]))
        else:
            grouped[-1][1].append(item)
    return grouped


def _materialize_inputs(scenario: Scenario, workspace: Path) -> None:
    for rel, content in scenario.initial_files.items():
        dest = _safe_workspace_dest(workspace, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8", newline="\n")
    for dest_rel, name in scenario.fixture_files.items():
        src = FIXTURE_DIR / name
        if not src.is_file():
            raise ClimateError(
                code="CLIMATE_INVALID_INPUT",
                message="fixture 不存在",
                retryable=False,
                details={"path": name, "field": "fixture_files"},
            )
        dest = _safe_workspace_dest(workspace, dest_rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def _prepare_knowledge_index(workspace: Path) -> None:
    """EVAL-005：从仓库 fixture 重建索引；不是 Agent ingest 工具。"""
    dest = workspace / "corpus"
    dest.mkdir(parents=True, exist_ok=True)
    for src in sorted(KNOWLEDGE_FIXTURE_DIR.glob("*.md")):
        (dest / src.name).write_bytes(src.read_bytes())
    from openharness.climate.knowledge import rebuild_knowledge_index

    rebuild_knowledge_index(workspace, "corpus")


def _safe_workspace_dest(workspace: Path, rel: str) -> Path:
    posix = rel.replace("\\", "/")
    dest = (workspace / posix).resolve()
    if not dest.is_relative_to(workspace.resolve()):
        raise ClimateError(
            code="CLIMATE_INVALID_PATH",
            message="initial_files 路径逃逸",
            retryable=False,
            details={"path": posix, "field": "initial_files"},
        )
    return dest


def _source_snapshot(workspace: Path, scenario: Scenario) -> dict[str, Any] | None:
    paths: list[Path] = []
    for dest_rel in scenario.fixture_files:
        paths.append(_safe_workspace_dest(workspace, dest_rel))
    for dest_rel, content in scenario.initial_files.items():
        if dest_rel.endswith(".csv"):
            paths.append(_safe_workspace_dest(workspace, dest_rel))
            del content
    if not paths:
        return None
    snapshot: dict[str, Any] = {}
    for path in paths:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        snapshot[path.name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "mtime_ns": path.stat().st_mtime_ns,
        }
    return snapshot or None


class _UnusedApiClient:
    """Command Hook 不得走模型；误调用时失败。"""

    async def stream_message(self, request: Any) -> Any:
        del request
        raise RuntimeError("output guard 不得调用模型")


def _output_guard_command() -> str:
    """按当前 shell 生成可移植的 Python 守卫命令。"""
    exe = str(Path(sys.executable).resolve())
    script = str((ROOT / "evals" / "climate" / "output_guard.py").resolve())
    argv0 = Path(resolve_shell_command("exit 0")[0]).name.lower()
    if argv0.startswith("bash") or argv0 in {"sh", "dash"}:
        return f"{shlex.quote(exe)} {shlex.quote(script)}"
    ps_exe = exe.replace("'", "''")
    ps_script = script.replace("'", "''")
    return f"& '{ps_exe}' '{ps_script}'"


def _build_output_guard_executor(workspace: Path) -> HookExecutor:
    """注册 matcher 精确命中 climate_write_report 的 PRE_TOOL_USE Command Hook。"""
    registry = HookRegistry()
    registry.register(
        HookEvent.PRE_TOOL_USE,
        CommandHookDefinition(
            command=_output_guard_command(),
            matcher=OUTPUT_GUARD_MATCHER,
            block_on_failure=True,
        ),
    )
    return HookExecutor(
        registry,
        HookExecutionContext(
            cwd=workspace,
            api_client=_UnusedApiClient(),
            default_model="unused",
        ),
    )


def _climate_fingerprint(workspace: Path) -> dict[str, str]:
    """对 `.climate` 常规文件做内容指纹；忽略锁文件。"""
    root = workspace / ".climate"
    if not root.is_dir():
        return {}
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix == ".lock" or "locks" in path.parts:
            continue
        rel = path.relative_to(workspace).as_posix()
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _context_snapshot(workspace: Path) -> dict[str, Any] | None:
    index_path = workspace / ".climate" / "index.json"
    if not index_path.is_file():
        return None
    try:
        index = loads_workspace_index(index_path.read_text(encoding="utf-8"))
    except (OSError, ClimateError, ValueError):
        return None
    run_id = index.active_run_id
    if not run_id:
        return None
    ctx_path = workspace / ".climate" / "runs" / run_id / "context.json"
    if not ctx_path.is_file():
        return None
    try:
        context = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    except (OSError, ClimateError, ValueError):
        return None
    return {
        "version": context.version,
        "status": context.status,
        "events": [item.model_dump(mode="json") for item in context.events],
    }


async def _run_pre_tool_hook(
    hook_executor: HookExecutor | None,
    invocation: ScenarioToolInvocation,
    *,
    sequence: int,
    started: float,
    input_redacted: dict[str, Any],
    workspace: Path,
    hook_events: list[dict[str, Any]] | None,
    recovery: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None, int | None] | None:
    """按 QueryEngine 顺序在 execute 前运行 PRE_TOOL_USE；blocked 时不调用工具。"""
    if hook_executor is None:
        return None
    fingerprint_before = _climate_fingerprint(workspace)
    context_before = _context_snapshot(workspace)
    pre_hooks = await hook_executor.execute(
        HookEvent.PRE_TOOL_USE,
        {
            "tool_name": invocation.name,
            "tool_input": dict(invocation.input),
            "event": HookEvent.PRE_TOOL_USE.value,
        },
    )
    if not pre_hooks.results:
        return None
    if hook_events is not None:
        hook_events.append(
            {
                "sequence": len(hook_events) + 1,
                "event": HookEvent.PRE_TOOL_USE.value,
                "tool_name": invocation.name,
                "blocked": pre_hooks.blocked,
                "reason_code": "CLIMATE_HOOK_BLOCKED" if pre_hooks.blocked else None,
            }
        )
    if not pre_hooks.blocked:
        return None
    version = context_before.get("version") if context_before else None
    if recovery is not None:
        fingerprint_after = _climate_fingerprint(workspace)
        context_after = _context_snapshot(workspace)
        recovery["hook_blocked_before_execute"] = True
        recovery["write_report_executed"] = False
        recovery["file_tree_unchanged"] = fingerprint_before == fingerprint_after
        recovery["context_version_unchanged"] = (context_before or {}).get("version") == (
            context_after or {}
        ).get("version")
        recovery["events_unchanged"] = (context_before or {}).get("events") == (
            context_after or {}
        ).get("events")
        recovery["guard_marker"] = OUTPUT_GUARD_MARKER in str(
            (invocation.input or {}).get("summary") or ""
        )
    return (
        {
            "sequence": sequence,
            "name": invocation.name,
            "input_redacted": input_redacted,
            "is_error": True,
            "error_code": "CLIMATE_HOOK_BLOCKED",
            "duration_ms": _elapsed_ms(started),
            "context_version": version if isinstance(version, int) else None,
            "output_redacted": {
                "ok": False,
                "provenance": "hook",
                "blocked": True,
            },
            "session": invocation.session,
        },
        None,
        version if isinstance(version, int) else None,
    )


async def _invoke_one(
    registry: ToolRegistry,
    context: ToolExecutionContext,
    invocation: ScenarioToolInvocation,
    *,
    sequence: int,
    workspace: Path,
    hook_executor: HookExecutor | None = None,
    hook_events: list[dict[str, Any]] | None = None,
    recovery: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None, int | None]:
    started = time.perf_counter()
    tool = registry.get(invocation.name)
    input_redacted = _redact_payload(dict(invocation.input), workspace)
    if tool is None:
        return (
            {
                "sequence": sequence,
                "name": invocation.name,
                "input_redacted": input_redacted,
                "is_error": True,
                "error_code": "CLIMATE_INVALID_INPUT",
                "duration_ms": _elapsed_ms(started),
                "output_redacted": {},
                "session": invocation.session,
            },
            None,
            None,
        )
    blocked_call = await _run_pre_tool_hook(
        hook_executor,
        invocation,
        sequence=sequence,
        started=started,
        input_redacted=input_redacted,
        workspace=workspace,
        hook_events=hook_events,
        recovery=recovery,
    )
    if blocked_call is not None:
        return blocked_call
    try:
        arguments = tool.input_model.model_validate(invocation.input)
        result = await tool.execute(arguments, context)
    except ValidationError:
        return (
            {
                "sequence": sequence,
                "name": invocation.name,
                "input_redacted": input_redacted,
                "is_error": True,
                "error_code": "CLIMATE_INVALID_INPUT",
                "duration_ms": _elapsed_ms(started),
                "output_redacted": {},
                "session": invocation.session,
            },
            None,
            None,
        )
    except ClimateError as exc:
        return (
            {
                "sequence": sequence,
                "name": invocation.name,
                "input_redacted": input_redacted,
                "is_error": True,
                "error_code": exc.code,
                "duration_ms": _elapsed_ms(started),
                "output_redacted": {"message": redact_secrets(exc.message, workspace=workspace)},
                "session": invocation.session,
            },
            None,
            None,
        )
    except Exception as exc:
        return (
            {
                "sequence": sequence,
                "name": invocation.name,
                "input_redacted": input_redacted,
                "is_error": True,
                "error_code": "CLIMATE_EXTERNAL_FAILED",
                "duration_ms": _elapsed_ms(started),
                "output_redacted": {"reason": type(exc).__name__},
                "session": invocation.session,
            },
            None,
            None,
        )
    duration_ms = _elapsed_ms(started)
    payload = _parse_result(result)
    error_code = None
    if result.is_error:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        error_code = code if isinstance(code, str) else "CLIMATE_EXTERNAL_FAILED"
    run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else None
    version = payload.get("context_version") if isinstance(payload.get("context_version"), int) else None
    output_redacted = _summarize_output(tool, payload, workspace)
    return (
        {
            "sequence": sequence,
            "name": invocation.name,
            "input_redacted": input_redacted,
            "is_error": bool(result.is_error),
            "error_code": error_code,
            "duration_ms": duration_ms,
            "context_version": version,
            "output_redacted": output_redacted,
            "session": invocation.session,
        },
        run_id,
        version,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _parse_result(result: ToolResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _summarize_output(tool: BaseTool, payload: dict[str, Any], workspace: Path) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    summary: dict[str, Any] = {}
    if payload.get("ok") is True:
        summary["ok"] = True
    if isinstance(payload.get("run_id"), str):
        summary["run_id"] = payload["run_id"]
    if isinstance(payload.get("context_version"), int):
        summary["context_version"] = payload["context_version"]
    for key in (
        "row_count",
        "columns",
        "warnings",
        "sha256",
        "media_type",
        "size_bytes",
        "artifact_id",
        "path",
        "status",
        "active_run_id",
    ):
        if key in data:
            summary[key] = data[key]
        elif key in payload:
            summary[key] = payload[key]
    if tool.name == "climate_query_knowledge":
        if isinstance(data.get("hit_count"), int):
            summary["hit_count"] = data["hit_count"]
        if isinstance(data.get("query"), str):
            summary["query"] = data["query"]
        hits = data.get("hits")
        if isinstance(hits, list):
            bounded: list[dict[str, Any]] = []
            for hit in hits[:10]:
                if not isinstance(hit, dict):
                    continue
                bounded.append(
                    {
                        "chunk_id": hit.get("chunk_id"),
                        "parent_text": str(hit.get("parent_text") or "")[:2000],
                        "source": hit.get("source"),
                        "score": hit.get("score"),
                    }
                )
            summary["hits"] = bounded
    if tool.name == "climate_read_context":
        for key in ("status", "run_id", "context_version", "active_run_id"):
            if key in payload and key not in summary:
                summary[key] = payload[key]
        view = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if isinstance(view, dict):
            if isinstance(view.get("status"), str):
                summary["status"] = view["status"]
            if isinstance(view.get("run_id"), str):
                summary["run_id"] = view["run_id"]
    return _redact_payload(summary, workspace)


def _annotate_report_links(
    tool_calls: list[dict[str, Any]],
    workspace: Path,
    run_id: str | None,
    recovery: dict[str, Any],
) -> None:
    report_path: Path | None = None
    plot_rel: str | None = None
    if run_id:
        candidate = workspace / ".climate" / "output" / run_id / "report.md"
        if candidate.is_file():
            report_path = candidate
        plot_dir = workspace / ".climate" / "output" / run_id
        if plot_dir.is_dir():
            for item in plot_dir.iterdir():
                if item.suffix.lower() in {".png", ".svg"}:
                    plot_rel = f".climate/output/{run_id}/{item.name}"
                    break
    if report_path is None:
        return
    text = report_path.read_text(encoding="utf-8")
    abs_ws = str(workspace)
    has_rel = bool(plot_rel and plot_rel in text) or "](.climate/" in text
    has_abs = abs_ws in text or str(Path.home()) in text
    from openharness.climate.validate import bounded_report_text, score_report_markdown

    recovery["report_has_relative_plot"] = has_rel
    recovery["report_has_absolute_workspace"] = has_abs
    recovery["report_text"] = bounded_report_text(text)
    recovery["report_rule_score"] = score_report_markdown(text, workspace=workspace)
    recovery["report_is_bench85"] = False
    for call in tool_calls:
        if call.get("name") == "climate_write_report":
            output = dict(call.get("output_redacted") or {})
            output["has_relative_plot"] = has_rel
            output["has_absolute_workspace"] = has_abs
            call["output_redacted"] = output


def _collect_disk_facts(
    workspace: Path, last_run_id: str | None
) -> tuple[list[dict[str, Any]], str | None, int | None, str | None]:
    index_path = workspace / ".climate" / "index.json"
    if not index_path.is_file():
        return [], None, None, last_run_id
    try:
        index = loads_workspace_index(index_path.read_text(encoding="utf-8"))
    except (OSError, ClimateError, ValueError):
        return [], None, None, last_run_id
    run_id = index.active_run_id or last_run_id
    if not run_id:
        return [], None, None, last_run_id
    ctx_path = workspace / ".climate" / "runs" / run_id / "context.json"
    if not ctx_path.is_file():
        return [], None, None, run_id
    try:
        context = loads_run_context(ctx_path.read_text(encoding="utf-8"))
    except (OSError, ClimateError, ValueError):
        return [], None, None, run_id
    manifest: list[dict[str, Any]] = []
    for artifact in context.artifacts:
        abs_path = workspace / artifact.path
        exists = abs_path.is_file()
        digest = None
        size = None
        matches = False
        if exists:
            raw = abs_path.read_bytes()
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            size = len(raw)
            matches = digest == artifact.sha256 and size == artifact.size_bytes
        manifest.append(
            {
                "artifact_id": artifact.artifact_id,
                "kind": artifact.kind,
                "path": artifact.path,
                "sha256": digest or artifact.sha256,
                "size_bytes": size if size is not None else artifact.size_bytes,
                "matches_context": matches,
            }
        )
    return manifest, context.status, context.version, run_id


def _redact_payload(payload: Any, workspace: Path) -> Any:
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and any(
                token in key.lower() for token in ("api_key", "token", "password", "secret", "authorization")
            ):
                continue
            cleaned[str(key)] = _redact_payload(value, workspace)
        return cleaned
    if isinstance(payload, list):
        return [_redact_payload(item, workspace) for item in payload]
    if isinstance(payload, str):
        return redact_secrets(payload, workspace=workspace, catch_all_posix=False)
    return payload
