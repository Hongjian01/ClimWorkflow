"""Climate 离线工作流编排：init / plan / acquire / inspect / plot / report / read。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openharness.climate.errors import ClimateError, climate_error, redact_secrets
from openharness.climate.models import Artifact, CdsRequestInput, Event, RunContext, Step
from openharness.climate.paths import (
    WriteZone,
    resolve_workspace_path,
    to_workspace_relative_posix,
    validate_local_source_file,
    validate_write_zone,
)
from openharness.climate.repository import ContextRepository, PublishedFile
from openharness.climate.state import WorkflowStateMachine, is_plan_confirmed

_REQUIRED_ACTIONS = frozenset(
    {"acquire_data", "inspect_dataset", "analyze_plot", "write_report"}
)
_MAX_INSPECT_BYTES = 50 * 1024 * 1024
_PROFILE_WARNING_LIMIT = 20


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_input_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_sample_csv() -> bytes:
    """固定 30 行 sample：UTF-8 / LF / 确定数值。"""
    lines = ["date,temperature_c,precipitation_mm"]
    start = date(2026, 1, 1)
    for index in range(30):
        day = start + timedelta(days=index)
        temperature = 10.0 + (index % 7)
        precipitation = float((index * 3) % 10)
        lines.append(f"{day.isoformat()},{temperature:.1f},{precipitation:.1f}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def publish_sample_dataset(repo: ContextRepository, run_id: str) -> PublishedFile:
    """sample 获取的公共服务：固定 CSV，原子发布到 run data 区。"""
    return repo.publish_run_file(
        run_id,
        f".climate/data/{run_id}/sample.csv",
        build_sample_csv(),
        zone=WriteZone.DATA,
    )


def init_workflow(
    workspace: Path,
    *,
    objective: str | None,
    run_id: str | None,
    resume_run_id: str | None,
) -> tuple[dict[str, Any], str, int]:
    repo = ContextRepository(workspace)
    repo.recover_active_run_transactions()
    if resume_run_id is not None:
        context = repo.resume_orphan(resume_run_id)
        return _run_summary(context), context.run_id, context.version

    created_id = run_id or str(uuid.uuid4())
    now = utc_now()
    context = RunContext(
        schema_version=2,
        version=1,
        run_id=created_id,
        objective=objective or "",
        status="initialized",
        created_at=now,
        updated_at=now,
        steps=[],
        artifacts=[],
        events=[
            Event(sequence=1, timestamp=now, type="run_created", step_id=None, data={})
        ],
        last_error=None,
    )
    saved = repo.create_and_activate_run(context)
    return _run_summary(saved), saved.run_id, saved.version


def plan_steps(
    workspace: Path,
    *,
    run_id: str | None,
    steps: list[Any],
    confirmed: bool = False,
) -> tuple[dict[str, Any], str, int]:
    repo, state, resolved, current = _prepare_mutation(workspace, run_id)
    planned, topo = _validate_plan(steps, workspace=repo.workspace)
    same_dag = bool(current.steps) and _dag_signature(current.steps) == _dag_signature(planned)
    if confirmed and same_dag and current.status == "running":
        saved = state.confirm_plan(
            resolved,
            expected_version=current.version,
            topological_ids=topo,
        )
    else:
        saved = state.accept_plan(
            resolved,
            planned,
            expected_version=current.version,
            topological_ids=topo,
            confirmed=confirmed,
        )
    return _plan_payload(saved, topo), saved.run_id, saved.version


def acquire_data(
    workspace: Path,
    *,
    run_id: str | None,
    step_id: str,
    mode: str,
    path: str | None,
    cds_request: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, int]:
    repo, state, resolved, current = _prepare_mutation(workspace, run_id)
    _require_running(current, workspace=repo.workspace)
    step = _require_step(current, step_id, expected_action="acquire_data", workspace=repo.workspace)

    parsed_cds: CdsRequestInput | None = None
    cds_dest: Path | None = None
    cds_relative: str | None = None
    if mode == "cds":
        if path is not None:
            raise climate_error(
                "CLIMATE_INVALID_INPUT",
                "cds 模式不得提供 path",
                details={"field": "path", "step_id": step_id},
                workspace=repo.workspace,
            )
        if cds_request is None:
            raise climate_error(
                "CLIMATE_INVALID_INPUT",
                "cds 模式必须提供 cds_request",
                details={"field": "cds_request", "step_id": step_id},
                workspace=repo.workspace,
            )
        from openharness.climate.cds import FORMAT_EXTENSION, parse_cds_request

        parsed_cds = parse_cds_request(cds_request)
        cds_relative = f".climate/data/{resolved}/cds-{step_id}{FORMAT_EXTENSION[parsed_cds.format]}"
        cds_dest = resolve_workspace_path(repo.workspace, cds_relative)
        validate_write_zone(repo.workspace, cds_dest, WriteZone.DATA, run_id=resolved)
    source_file = None
    dest_relative = None
    if mode == "local":
        if path is None:
            raise climate_error(
                "CLIMATE_INVALID_INPUT",
                "local 模式必须提供 path",
                details={"field": "path", "step_id": step_id},
                workspace=repo.workspace,
            )
        if cds_request is not None:
            raise climate_error(
                "CLIMATE_INVALID_INPUT",
                "local 模式不得提供 cds_request",
                details={"field": "mode", "step_id": step_id},
                workspace=repo.workspace,
            )
        source_file = validate_local_source_file(repo.workspace, path)
        if not source_file.name.lower().endswith(".csv"):
            raise climate_error(
                "CLIMATE_FORMAT_UNSUPPORTED",
                "local 模式仅支持 CSV",
                details={"path": path, "field": "path"},
                workspace=repo.workspace,
            )
        dest_relative = f".climate/data/{resolved}/local-{step_id}.csv"
        dest_path = resolve_workspace_path(repo.workspace, dest_relative)
        if dest_path.resolve(strict=False) == source_file.resolve():
            raise climate_error(
                "CLIMATE_INVALID_PATH",
                "local artifact 不得与源文件同一路径",
                details={"path": path, "field": "path"},
                workspace=repo.workspace,
            )
    elif mode == "sample":
        if path is not None or cds_request is not None:
            raise climate_error(
                "CLIMATE_INVALID_INPUT",
                "sample 模式不得提供 path 或 cds_request",
                details={"field": "mode", "step_id": step_id},
                workspace=repo.workspace,
            )
    elif mode != "cds":
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "不支持的 acquisition 模式",
            details={"field": "mode", "allowed": ["sample", "local", "cds"], "step_id": step_id},
            workspace=repo.workspace,
        )

    _require_dependencies(current, step, workspace=repo.workspace)
    hash_request = parsed_cds.model_dump(mode="json") if parsed_cds is not None else cds_request
    digest = canonical_input_hash({"mode": mode, "path": path, "cds_request": hash_request})
    replay = _replay_payload(current, step, digest)
    if replay is not None:
        return replay, current.run_id, current.version

    # PLAN-001：确认闸门必须在写 .part / 下载 / start step 之前。
    _require_plan_confirmed(current, workspace=repo.workspace)

    started = state.replay_or_start_step(
        resolved,
        step_id,
        input_hash=digest,
        expected_version=current.version,
    )
    audit: dict[str, Any] = {}
    try:
        if mode == "cds":
            if parsed_cds is None or cds_dest is None or cds_relative is None:
                raise climate_error(
                    "CLIMATE_INVALID_INPUT",
                    "cds 模式缺少已校验的请求",
                    details={"field": "cds_request", "step_id": step_id},
                    workspace=repo.workspace,
                )
            from openharness.climate.cds import (
                MEDIA_TYPES,
                allow_sample_fallback,
                download_cds_dataset_with_candidates,
                _read_shared_payload,
            )

            try:
                _, cand_audit = download_cds_dataset_with_candidates(parsed_cds, cds_dest)
            except ClimateError as cds_exc:
                if not allow_sample_fallback(parsed_cds, cds_exc):
                    raise
                _cleanup_acquire_parts(repo.workspace, resolved)
                published = publish_sample_dataset(repo, resolved)
                artifact_id = "data-primary"
                media_type = "text/csv"
                audit = {
                    "requested_mode": "cds",
                    "effective_mode": "sample",
                    "fallback_reason": cds_exc.code,
                }
                if isinstance(cds_exc.details.get("candidate_count"), int):
                    audit["candidate_count"] = cds_exc.details["candidate_count"]
                if isinstance(cds_exc.details.get("candidate_index"), int):
                    audit["candidate_index"] = cds_exc.details["candidate_index"]
            else:
                digest_bytes = hashlib.sha256(_read_shared_payload(cds_dest)).hexdigest()
                published = PublishedFile(
                    path=to_workspace_relative_posix(repo.workspace, cds_dest),
                    size_bytes=cds_dest.stat().st_size,
                    sha256=f"sha256:{digest_bytes}",
                )
                artifact_id = f"dataset-{step_id}"
                media_type = MEDIA_TYPES[parsed_cds.format]
                audit = {
                    "requested_mode": "cds",
                    "effective_mode": "cds",
                    **cand_audit,
                }
        elif mode == "local":
            if source_file is None or dest_relative is None:
                raise climate_error(
                    "CLIMATE_INVALID_INPUT",
                    "local 模式缺少已校验的源路径",
                    details={"field": "path", "step_id": step_id},
                    workspace=repo.workspace,
                )
            published = repo.copy_run_file(
                resolved,
                dest_relative,
                source_file,
                zone=WriteZone.DATA,
            )
            artifact_id = f"dataset-{step_id}"
            media_type = "text/csv"
        else:
            published = publish_sample_dataset(repo, resolved)
            artifact_id = "data-primary"
            media_type = "text/csv"
        artifact = Artifact(
            artifact_id=artifact_id,
            kind="dataset",
            path=published.path,
            media_type=media_type,
            size_bytes=published.size_bytes,
            sha256=published.sha256,
            created_by_step=step_id,
            created_at=utc_now(),
        )
        payload = {
            "artifact_id": artifact.artifact_id,
            "path": published.path,
            "media_type": artifact.media_type,
            "size_bytes": published.size_bytes,
            "sha256": published.sha256,
            **audit,
        }
        saved = state.apply_step_event(
            resolved,
            step_id,
            "success",
            expected_version=started.version,
            input_hash=digest,
            result={"artifact_ids": [artifact.artifact_id], **payload},
            artifacts=[artifact],
            event_data=audit or None,
        )
        return payload, saved.run_id, saved.version
    except ClimateError as exc:
        _cleanup_acquire_parts(repo.workspace, resolved)
        _record_step_failure(state, started, step_id, exc)
        raise


def inspect_dataset(
    workspace: Path,
    *,
    run_id: str | None,
    step_id: str,
    path: str | None,
) -> tuple[dict[str, Any], str, int]:
    repo, state, resolved, current = _prepare_mutation(workspace, run_id)
    _require_running(current, workspace=repo.workspace)
    step = _require_step(
        current, step_id, expected_action="inspect_dataset", workspace=repo.workspace
    )
    if path is not None:
        resolve_workspace_path(repo.workspace, path)
    _require_dependencies(current, step, workspace=repo.workspace)

    relative = path
    if relative is None:
        latest = _latest_dataset(current, workspace=repo.workspace)
        relative = latest.path
    target = resolve_workspace_path(repo.workspace, relative)
    if not target.is_file():
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "inspect 目标不是普通文件",
            details={"path": relative, "field": "path"},
            workspace=repo.workspace,
        )
    digest = canonical_input_hash({"path": relative})
    replay = _replay_payload(current, step, digest)
    if replay is not None:
        return replay, current.run_id, current.version

    started = state.replay_or_start_step(
        resolved,
        step_id,
        input_hash=digest,
        expected_version=current.version,
    )
    try:
        profile = _inspect_file(target, relative_path=relative, workspace=repo.workspace)
        profile_bytes = (
            json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        published = repo.publish_run_file(
            resolved,
            f".climate/output/{resolved}/profile.json",
            profile_bytes,
            zone=WriteZone.OUTPUT,
        )
        artifact = Artifact(
            artifact_id="profile-primary",
            kind="profile",
            path=published.path,
            media_type="application/json",
            size_bytes=published.size_bytes,
            sha256=published.sha256,
            created_by_step=step_id,
            created_at=utc_now(),
        )
        payload = {
            "artifact_id": artifact.artifact_id,
            "path": relative,
            **profile,
        }
        saved = state.apply_step_event(
            resolved,
            step_id,
            "success",
            expected_version=started.version,
            input_hash=digest,
            result={"artifact_ids": [artifact.artifact_id], "path": relative, **profile},
            artifacts=[artifact],
        )
        return payload, saved.run_id, saved.version
    except ClimateError as exc:
        _record_step_failure(state, started, step_id, exc)
        raise


def matplotlib_available() -> bool:
    """检测 PNG renderer 所需的 matplotlib；测试可 monkeypatch。

    用 find_spec，避免在 TUI 线程里 import matplotlib（Windows TkAgg 会死锁）。
    """
    import importlib.util

    return importlib.util.find_spec("matplotlib") is not None


def analyze_plot(
    workspace: Path,
    *,
    run_id: str | None,
    step_id: str,
    path: str | None,
    chart_type: str,
    x: str | None,
    y: str,
    title: str | None,
) -> tuple[dict[str, Any], str, int]:
    repo, state, resolved, current = _prepare_mutation(workspace, run_id)
    _require_running(current, workspace=repo.workspace)
    step = _require_step(
        current, step_id, expected_action="analyze_plot", workspace=repo.workspace
    )
    if path is not None:
        resolve_workspace_path(repo.workspace, path)
    _require_dependencies(current, step, workspace=repo.workspace)

    inspected = _inspected_dataset_paths(current, workspace=repo.workspace)
    relative = path if path is not None else inspected[-1]
    if relative not in inspected:
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "plot 只能读取已检查的 dataset",
            details={"path": relative, "field": "path"},
            workspace=repo.workspace,
        )
    target = resolve_workspace_path(repo.workspace, relative)
    digest = canonical_input_hash(
        {"path": relative, "chart_type": chart_type, "x": x, "y": y, "title": title}
    )
    replay = _replay_payload(current, step, digest)
    if replay is not None:
        return replay, current.run_id, current.version
    if not target.is_file():
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "plot 目标不是普通文件",
            details={"path": relative, "field": "path"},
            workspace=repo.workspace,
        )

    prepared = _prepare_plot_series(
        target,
        relative_path=relative,
        chart_type=chart_type,
        x_name=x,
        y_name=y,
        title=title or "",
        workspace=repo.workspace,
    )

    started = state.replay_or_start_step(
        resolved,
        step_id,
        input_hash=digest,
        expected_version=current.version,
    )
    try:
        image, media_type, fallback_reason = _render_plot(prepared)
        extension = "png" if media_type == "image/png" else "svg"
        dest_relative = f".climate/output/{resolved}/plot-{step_id}.{extension}"
        published = repo.publish_run_file(
            resolved,
            dest_relative,
            image,
            zone=WriteZone.OUTPUT,
        )
        artifact = Artifact(
            artifact_id=f"plot-{step_id}",
            kind="plot",
            path=published.path,
            media_type=media_type,
            size_bytes=published.size_bytes,
            sha256=published.sha256,
            created_by_step=step_id,
            created_at=utc_now(),
        )
        payload = {
            "artifact_id": artifact.artifact_id,
            "path": published.path,
            "media_type": media_type,
            "size_bytes": published.size_bytes,
            "sha256": published.sha256,
            "fallback_reason": fallback_reason,
        }
        saved = state.apply_step_event(
            resolved,
            step_id,
            "success",
            expected_version=started.version,
            input_hash=digest,
            result={"artifact_ids": [artifact.artifact_id], **payload},
            artifacts=[artifact],
        )
        return payload, saved.run_id, saved.version
    except ClimateError as exc:
        _cleanup_output_parts(repo.workspace, resolved)
        _record_step_failure(state, started, step_id, exc)
        raise


def write_report(
    workspace: Path,
    *,
    run_id: str | None,
    step_id: str,
    title: str,
    summary: str,
) -> tuple[dict[str, Any], str, int]:
    repo, state, resolved, current = _prepare_mutation(workspace, run_id)
    digest = canonical_input_hash({"title": title, "summary": summary})
    if current.status == "completed":
        step = _require_step(
            current, step_id, expected_action="write_report", workspace=repo.workspace
        )
        replay = _replay_payload(current, step, digest)
        if replay is not None:
            return replay, current.run_id, current.version
        raise climate_error(
            "CLIMATE_IDEMPOTENCY_CONFLICT",
            "成功 step 被不同输入重放",
            details={"step_id": step_id, "field": "input_hash"},
            workspace=repo.workspace,
        )
    _require_running(current, workspace=repo.workspace)
    step = _require_step(
        current, step_id, expected_action="write_report", workspace=repo.workspace
    )
    _require_dependencies(current, step, workspace=repo.workspace)
    replay = _replay_payload(current, step, digest)
    if replay is not None:
        return replay, current.run_id, current.version

    markdown = _render_report_markdown(
        current, title=title, summary=summary, workspace=repo.workspace
    )
    started = state.replay_or_start_step(
        resolved,
        step_id,
        input_hash=digest,
        expected_version=current.version,
    )
    try:
        dest_relative = f".climate/output/{resolved}/report.md"
        published = repo.publish_run_file(
            resolved,
            dest_relative,
            markdown.encode("utf-8"),
            zone=WriteZone.OUTPUT,
        )
        artifact = Artifact(
            artifact_id="report-primary",
            kind="report",
            path=published.path,
            media_type="text/markdown",
            size_bytes=published.size_bytes,
            sha256=published.sha256,
            created_by_step=step_id,
            created_at=utc_now(),
        )
        payload = {
            "artifact_id": artifact.artifact_id,
            "path": published.path,
            "media_type": artifact.media_type,
            "size_bytes": published.size_bytes,
            "sha256": published.sha256,
        }
        saved = state.apply_step_event(
            resolved,
            step_id,
            "success",
            expected_version=started.version,
            input_hash=digest,
            result={"artifact_ids": [artifact.artifact_id], **payload},
            artifacts=[artifact],
        )
        if all(item.status in {"succeeded", "skipped"} for item in saved.steps):
            saved = state.apply_run_event(
                resolved,
                "report_succeeded",
                expected_version=saved.version,
            )
        return payload, saved.run_id, saved.version
    except ClimateError as exc:
        _cleanup_output_parts(repo.workspace, resolved)
        _record_step_failure(state, started, step_id, exc)
        raise


def validate_artifacts(
    workspace: Path,
    *,
    run_id: str | None,
) -> tuple[dict[str, Any], str, int]:
    """只读产物规则校验；不写 Context、不改源数据。"""
    from openharness.climate.validate import validate_run_artifacts

    repo = ContextRepository(workspace)
    if repo.has_pending_active_run_transaction():
        raise climate_error(
            "CLIMATE_RECOVERY_REQUIRED",
            "存在未完成的 active-run 事务，请先执行受权限控制的恢复 mutation",
            details={"reason": "pending_wal"},
            workspace=workspace,
        )
    resolved = _resolve_run_id(repo, run_id)
    context = repo.load_run(resolved)
    data = validate_run_artifacts(workspace, run_id=resolved)
    return data, context.run_id, context.version


def read_context(
    workspace: Path,
    *,
    run_id: str | None,
    include_events: bool,
    event_limit: int,
) -> tuple[dict[str, Any], str, int]:
    repo = ContextRepository(workspace)
    if repo.has_pending_active_run_transaction():
        raise climate_error(
            "CLIMATE_RECOVERY_REQUIRED",
            "存在未完成的 active-run 事务，请先执行受权限控制的恢复 mutation",
            details={"reason": "pending_wal"},
            workspace=workspace,
        )
    resolved = _resolve_run_id(repo, run_id)
    context = repo.load_run(resolved)
    try:
        index = repo.load_index()
        active = index.active_run_id
    except ClimateError:
        active = None
    orphans = repo.list_orphan_run_ids(readonly=True)
    view: dict[str, Any] = {
        "run_id": context.run_id,
        "objective": context.objective,
        "status": context.status,
        "version": context.version,
        "created_at": context.created_at,
        "updated_at": context.updated_at,
        "steps": [step.model_dump(mode="json") for step in context.steps],
        "artifacts": [art.model_dump(mode="json") for art in context.artifacts],
        "last_error": (
            context.last_error.model_dump(mode="json") if context.last_error else None
        ),
        "orphan_run_ids": orphans,
        "active_run_id": active,
    }
    if include_events:
        view["events"] = [
            event.model_dump(mode="json") for event in context.events[-event_limit:]
        ]
    return view, context.run_id, context.version


def _run_summary(context: RunContext) -> dict[str, Any]:
    return {
        "run_id": context.run_id,
        "status": context.status,
        "objective": context.objective,
        "context_path": f".climate/runs/{context.run_id}/context.json",
    }


def _prepare_mutation(
    workspace: Path, run_id: str | None
) -> tuple[ContextRepository, WorkflowStateMachine, str, RunContext]:
    repo = ContextRepository(workspace)
    repo.recover_active_run_transactions()
    resolved = _resolve_run_id(repo, run_id)
    state = WorkflowStateMachine(repo)
    current = repo.load_run(resolved)
    recovered = state.recover_interrupted_steps(
        resolved, expected_version=current.version
    )
    return repo, state, resolved, recovered


def _resolve_run_id(repo: ContextRepository, run_id: str | None) -> str:
    if run_id is not None:
        return run_id
    index_path = repo.workspace / ".climate" / "index.json"
    if not index_path.is_file():
        raise climate_error(
            "CLIMATE_RUN_NOT_FOUND",
            "没有 active run",
            workspace=repo.workspace,
        )
    index = repo.load_index()
    if index.active_run_id is None:
        raise climate_error(
            "CLIMATE_RUN_NOT_FOUND",
            "没有 active run",
            workspace=repo.workspace,
        )
    return index.active_run_id


def _plan_payload(context: RunContext, topo: list[str]) -> dict[str, Any]:
    """plan 工具摘要：步骤标题/动作，以及 acquire 步已成功时的拟定 mode（若有）。"""
    by_id = {step.step_id: step for step in context.steps}
    steps_out: list[dict[str, Any]] = []
    for step_id in topo:
        step = by_id[step_id]
        item: dict[str, Any] = {
            "step_id": step.step_id,
            "title": step.title,
            "action": step.action,
        }
        if step.action == "acquire_data":
            mode = _proposed_acquire_mode(context, step)
            if mode is not None:
                item["mode"] = mode
        steps_out.append(item)
    return {
        "step_ids": topo,
        "status": context.status,
        "confirmed": is_plan_confirmed(context),
        "steps": steps_out,
    }


def _dag_signature(steps: list[Step]) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """拓扑+action 签名；不含 title，换标题不算新 plan。"""
    return tuple(
        sorted((step.step_id, step.action, tuple(step.depends_on)) for step in steps)
    )


def _proposed_acquire_mode(context: RunContext, step: Step) -> str | None:
    """仅在 acquire 已成功时回填 mode；plan 输入没有 mode 字段。"""
    if step.status != "succeeded" or not step.result:
        return None
    path = step.result.get("path")
    if not isinstance(path, str):
        return None
    name = path.rsplit("/", 1)[-1]
    if name == "sample.csv":
        return "sample"
    if name.startswith("local-"):
        return "local"
    if name.startswith("cds-"):
        return "cds"
    requested = step.result.get("requested_mode")
    if requested in {"sample", "local", "cds"}:
        return requested
    return None


def _require_plan_confirmed(context: RunContext, *, workspace: Path) -> None:
    if is_plan_confirmed(context):
        return
    error = climate_error(
        "CLIMATE_INVALID_TRANSITION",
        "计划尚未确认，不能获取数据",
        details={"reason": "plan_unconfirmed", "field": "plan", "status": context.status},
        workspace=workspace,
    )
    error.retryable = True
    raise error


def _require_running(context: RunContext, *, workspace: Path) -> None:
    if context.status != "running":
        raise climate_error(
            "CLIMATE_INVALID_TRANSITION",
            "当前 run 尚未接受 plan",
            details={"status": context.status, "run_id": context.run_id},
            workspace=workspace,
        )


def _require_step(
    context: RunContext,
    step_id: str,
    *,
    expected_action: str,
    workspace: Path,
) -> Step:
    for step in context.steps:
        if step.step_id == step_id:
            if step.action != expected_action:
                raise climate_error(
                    "CLIMATE_INVALID_INPUT",
                    "step action 与工具不匹配",
                    details={"step_id": step_id, "field": "action"},
                    workspace=workspace,
                )
            return step
    raise climate_error(
        "CLIMATE_INVALID_INPUT",
        "step 不存在",
        details={"step_id": step_id},
        workspace=workspace,
    )


def _require_dependencies(context: RunContext, step: Step, *, workspace: Path) -> None:
    by_id = {item.step_id: item for item in context.steps}
    for dep in step.depends_on:
        other = by_id.get(dep)
        if other is None or other.status != "succeeded":
            raise climate_error(
                "CLIMATE_DEPENDENCY_NOT_READY",
                "前置 step 尚未成功",
                details={"step_id": step.step_id, "status": other.status if other else None},
                workspace=workspace,
            )


def _replay_payload(
    context: RunContext, step: Step, digest: str
) -> dict[str, Any] | None:
    if step.status == "succeeded" and step.input_hash == digest and step.result:
        return dict(step.result)
    return None


def _record_step_failure(
    state: WorkflowStateMachine,
    started: RunContext,
    step_id: str,
    error: ClimateError,
) -> None:
    try:
        state.apply_step_event(
            started.run_id,
            step_id,
            "operation_error",
            expected_version=started.version,
            error=error.to_error_object(),
        )
    except ClimateError:
        return


def _cleanup_acquire_parts(workspace: Path, run_id: str) -> None:
    root = workspace / ".climate" / "data" / run_id
    if not root.is_dir():
        return
    for path in root.rglob("*.part"):
        if path.is_file():
            with suppress(OSError):
                path.unlink()


def _latest_dataset(context: RunContext, *, workspace: Path) -> Artifact:
    datasets = [item for item in context.artifacts if item.kind == "dataset"]
    if not datasets:
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "没有可用的 dataset artifact",
            details={"field": "path", "run_id": context.run_id},
            workspace=workspace,
        )
    datasets.sort(key=lambda item: item.created_at)
    return datasets[-1]


def _validate_plan(steps: list[Any], *, workspace: Path) -> tuple[list[Step], list[str]]:
    ids = [item.step_id for item in steps]
    if len(ids) != len(set(ids)):
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "step_id 必须唯一",
            details={"field": "step_id"},
            workspace=workspace,
        )
    present = {item.action for item in steps}
    if not _REQUIRED_ACTIONS.issubset(present):
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "四类 action 必须各至少出现一次",
            details={"field": "action", "allowed": sorted(_REQUIRED_ACTIONS)},
            workspace=workspace,
        )

    id_set = set(ids)
    indegree: dict[str, int] = {sid: 0 for sid in ids}
    outgoing: dict[str, list[str]] = {sid: [] for sid in ids}
    by_id = {item.step_id: item for item in steps}
    for item in steps:
        seen_dep: set[str] = set()
        for dep in item.depends_on:
            if dep not in id_set:
                raise climate_error(
                    "CLIMATE_INVALID_INPUT",
                    "depends_on 引用不存在的 step",
                    details={"step_id": item.step_id, "field": "depends_on"},
                    workspace=workspace,
                )
            if dep == item.step_id or dep in seen_dep:
                raise climate_error(
                    "CLIMATE_INVALID_INPUT",
                    "依赖不合法",
                    details={"step_id": item.step_id, "field": "depends_on"},
                    workspace=workspace,
                )
            seen_dep.add(dep)
            outgoing[dep].append(item.step_id)
            indegree[item.step_id] += 1

    order_index = {sid: index for index, sid in enumerate(ids)}
    ready = sorted(
        (sid for sid, degree in indegree.items() if degree == 0),
        key=lambda sid: order_index[sid],
    )
    topo: list[str] = []
    remaining = dict(indegree)
    while ready:
        node = ready.pop(0)
        topo.append(node)
        for nxt in outgoing[node]:
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=lambda sid: order_index[sid])
    if len(topo) != len(ids):
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "依赖图必须无环",
            details={"field": "depends_on"},
            workspace=workspace,
        )

    for item in steps:
        if item.action != "write_report":
            continue
        ancestors = _ancestors(item.step_id, by_id)
        actions = {by_id[sid].action for sid in ancestors}
        if "inspect_dataset" not in actions or "analyze_plot" not in actions:
            raise climate_error(
                "CLIMATE_INVALID_INPUT",
                "每个 report 必须可达 inspect 与 plot",
                details={"step_id": item.step_id, "field": "depends_on"},
                workspace=workspace,
            )

    planned = [
        Step(
            step_id=by_id[sid].step_id,
            action=by_id[sid].action,
            title=by_id[sid].title,
            depends_on=list(by_id[sid].depends_on),
            status="pending",
            attempts=0,
        )
        for sid in topo
    ]
    return planned, topo


def _ancestors(step_id: str, by_id: dict[str, Any]) -> set[str]:
    seen: set[str] = set()
    stack = list(by_id[step_id].depends_on)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(by_id[node].depends_on)
    return seen


def _inspect_file(path: Path, *, relative_path: str, workspace: Path) -> dict[str, Any]:
    """按扩展名分发 CSV 或已冻结科学格式；profile 仅为 JSON 值。"""
    from openharness.climate.formats import SUPPORTED_FORMATS, format_from_extension

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "无法读取 dataset",
            details={"path": relative_path, "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc
    if size > _MAX_INSPECT_BYTES:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "dataset 超过 50 MiB 读取上限",
            details={"path": relative_path, "field": "path"},
            workspace=workspace,
        )
    claimed = format_from_extension(path)
    if claimed in SUPPORTED_FORMATS:
        from openharness.climate.formats import validate_published_artifact
        from openharness.climate.readers import read_scientific_profile

        fmt = validate_published_artifact(path, claimed, parse=False)
        return read_scientific_profile(path, fmt)
    if path.name.lower().endswith(".csv"):
        return _inspect_csv(path, relative_path=relative_path, workspace=workspace)
    raise climate_error(
        "CLIMATE_FORMAT_UNSUPPORTED",
        "inspect 仅支持 CSV 与已冻结科学格式",
        details={"path": relative_path, "field": "path"},
        workspace=workspace,
    )


def _inspect_csv(path: Path, *, relative_path: str, workspace: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "无法读取 dataset",
            details={"path": relative_path, "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc
    if size > _MAX_INSPECT_BYTES:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "dataset 超过 50 MiB 读取上限",
            details={"path": relative_path, "field": "path"},
            workspace=workspace,
        )
    if not path.name.lower().endswith(".csv"):
        raise climate_error(
            "CLIMATE_FORMAT_UNSUPPORTED",
            "inspect 仅支持 CSV 与已冻结科学格式",
            details={"path": relative_path, "field": "path"},
            workspace=workspace,
        )

    warnings: list[str] = []

    def warn(message: str) -> None:
        if len(warnings) < _PROFILE_WARNING_LIMIT:
            warnings.append(message)

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise climate_error(
                    "CLIMATE_DATA_INVALID",
                    "CSV 为空",
                    details={"path": relative_path, "field": "path"},
                    workspace=workspace,
                ) from None
            if not header or any(name.strip() == "" for name in header):
                warn("存在空列名")
            width = len(header)
            null_counts = [0] * width
            int_ok = [True] * width
            float_ok = [True] * width
            numeric_min: list[float | None] = [None] * width
            numeric_max: list[float | None] = [None] * width
            numeric_sum = [0.0] * width
            numeric_n = [0] * width
            row_count = 0
            for row in reader:
                row_count += 1
                if len(row) != width:
                    warn("存在与表头长度不一致的行")
                for col, raw in enumerate(row[:width]):
                    value = raw.strip() if isinstance(raw, str) else ""
                    if value == "":
                        null_counts[col] += 1
                        continue
                    as_int = _try_int(value)
                    as_float = _try_float(value)
                    if as_int is None:
                        int_ok[col] = False
                    if as_float is None:
                        float_ok[col] = False
                    else:
                        numeric_n[col] += 1
                        numeric_sum[col] += as_float
                        current_min = numeric_min[col]
                        current_max = numeric_max[col]
                        numeric_min[col] = (
                            as_float if current_min is None else min(current_min, as_float)
                        )
                        numeric_max[col] = (
                            as_float if current_max is None else max(current_max, as_float)
                        )
    except UnicodeDecodeError as exc:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "CSV 不是合法 UTF-8",
            details={"path": relative_path, "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc
    except OSError as exc:
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "无法读取 dataset",
            details={"path": relative_path, "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc

    columns: list[dict[str, Any]] = []
    for index, name in enumerate(header):
        dtype = "string"
        if numeric_n[index] and int_ok[index] and null_counts[index] + numeric_n[index] == row_count:
            dtype = "int"
        elif numeric_n[index] and float_ok[index] and null_counts[index] + numeric_n[index] == row_count:
            dtype = "float"
        elif numeric_n[index] and not float_ok[index]:
            warn(f"列 {name} 含混合类型")
        column: dict[str, Any] = {
            "name": name,
            "dtype": dtype,
            "null_count": null_counts[index],
        }
        if dtype in {"int", "float"} and numeric_n[index]:
            column["min"] = numeric_min[index]
            column["max"] = numeric_max[index]
            column["mean"] = numeric_sum[index] / numeric_n[index]
        columns.append(column)

    return {
        "row_count": row_count,
        "columns": columns,
        "warnings": warnings[:_PROFILE_WARNING_LIMIT],
    }


def _try_int(value: str) -> int | None:
    body = value[1:] if value.startswith(("+", "-")) else value
    if not body.isdigit():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _try_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_HISTOGRAM_BINS = 10


@dataclass(frozen=True)
class PreparedPlot:
    """与渲染器无关的有界绘图数据。"""

    chart_type: str
    x_values: tuple[str, ...] | None
    y_values: tuple[float, ...]
    x_name: str | None
    y_name: str
    title: str


def _inspected_dataset_paths(context: RunContext, *, workspace: Path) -> list[str]:
    found: list[str] = []
    for step in context.steps:
        if step.action != "inspect_dataset" or step.status != "succeeded" or not step.result:
            continue
        source = step.result.get("path")
        if isinstance(source, str) and source:
            found.append(source)
    if found:
        return found
    raise climate_error(
        "CLIMATE_INVALID_INPUT",
        "没有已检查的 dataset",
        details={"field": "path", "run_id": context.run_id},
        workspace=workspace,
    )


def _cleanup_output_parts(workspace: Path, run_id: str) -> None:
    root = workspace / ".climate" / "output" / run_id
    if not root.is_dir():
        return
    for path in root.rglob("*.part"):
        if path.is_file():
            with suppress(OSError):
                path.unlink()


def _prepare_scientific_plot(
    path: Path,
    *,
    claimed_format: str,
    relative_path: str,
    chart_type: str,
    x_name: str | None,
    y_name: str,
    title: str,
    workspace: Path,
) -> PreparedPlot:
    """从冻结格式读取有界 1-D 序列；不把库对象带入绘图数据。"""
    del x_name, relative_path, workspace
    from openharness.climate.formats import validate_published_artifact
    from openharness.climate.readers import read_plot_values

    fmt = validate_published_artifact(path, claimed_format, parse=False)
    values = read_plot_values(path, fmt, y_name)
    x_values = None
    if chart_type in {"line", "bar"}:
        x_values = tuple(str(index) for index in range(len(values)))
    return PreparedPlot(
        chart_type=chart_type,
        x_values=x_values,
        y_values=values,
        x_name=None,
        y_name=y_name,
        title=title,
    )


def _prepare_plot_series(
    path: Path,
    *,
    relative_path: str,
    chart_type: str,
    x_name: str | None,
    y_name: str,
    title: str,
    workspace: Path,
) -> PreparedPlot:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "无法读取 dataset",
            details={"path": relative_path, "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc
    if size > _MAX_INSPECT_BYTES:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "dataset 超过 50 MiB 读取上限",
            details={"path": relative_path, "field": "path"},
            workspace=workspace,
        )
    if not path.name.lower().endswith(".csv"):
        from openharness.climate.formats import SUPPORTED_FORMATS, format_from_extension

        claimed = format_from_extension(path)
        if claimed not in SUPPORTED_FORMATS:
            raise climate_error(
                "CLIMATE_FORMAT_UNSUPPORTED",
                "plot 仅支持 CSV 与已冻结科学格式",
                details={"path": relative_path, "field": "path"},
                workspace=workspace,
            )
        return _prepare_scientific_plot(
            path,
            claimed_format=claimed,
            relative_path=relative_path,
            chart_type=chart_type,
            x_name=x_name,
            y_name=y_name,
            title=title,
            workspace=workspace,
        )

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise climate_error(
                    "CLIMATE_DATA_INVALID",
                    "CSV 为空",
                    details={"path": relative_path, "field": "path"},
                    workspace=workspace,
                ) from None
            if y_name not in header:
                raise climate_error(
                    "CLIMATE_DATA_INVALID",
                    "目标列不存在",
                    details={"field": "y", "path": relative_path},
                    workspace=workspace,
                )
            if chart_type in {"line", "bar"} and (x_name is None or x_name not in header):
                raise climate_error(
                    "CLIMATE_DATA_INVALID",
                    "x 列不存在",
                    details={"field": "x", "path": relative_path},
                    workspace=workspace,
                )
            y_index = header.index(y_name)
            x_index = header.index(x_name) if chart_type in {"line", "bar"} and x_name else None
            xs: list[str] = []
            ys: list[float] = []
            for row in reader:
                raw_y = row[y_index].strip() if y_index < len(row) else ""
                if raw_y == "":
                    continue
                parsed = _try_float(raw_y)
                if parsed is None:
                    raise climate_error(
                        "CLIMATE_DATA_INVALID",
                        "y 列必须是数值",
                        details={"field": "y", "path": relative_path},
                        workspace=workspace,
                    )
                if x_index is not None:
                    raw_x = row[x_index].strip() if x_index < len(row) else ""
                    xs.append(raw_x)
                ys.append(parsed)
    except UnicodeDecodeError as exc:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "CSV 不是合法 UTF-8",
            details={"path": relative_path, "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc
    except OSError as exc:
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "无法读取 dataset",
            details={"path": relative_path, "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc

    if not ys:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "没有可用于绘图的数值",
            details={"field": "y", "path": relative_path},
            workspace=workspace,
        )
    return PreparedPlot(
        chart_type=chart_type,
        x_values=tuple(xs) if x_index is not None else None,
        y_values=tuple(ys),
        x_name=x_name,
        y_name=y_name,
        title=title,
    )


def _render_plot(prepared: PreparedPlot) -> tuple[bytes, str, str | None]:
    if matplotlib_available():
        try:
            data = _render_matplotlib_png(prepared)
        except (ImportError, OSError, ValueError, RuntimeError, AttributeError):
            data = b""
        if data.startswith(_PNG_MAGIC):
            return data, "image/png", None
    return _render_svg(prepared), "image/svg+xml", "matplotlib_missing"


def _render_matplotlib_png(prepared: PreparedPlot) -> bytes:
    """子进程 Agg 渲染；TUI 进程不得 import matplotlib。"""
    spec = {
        "chart_type": prepared.chart_type,
        "x_values": list(prepared.x_values) if prepared.x_values is not None else None,
        "y_values": [float(item) for item in prepared.y_values],
        "x_name": prepared.x_name,
        "y_name": prepared.y_name,
        "title": prepared.title,
    }
    spec_fd, spec_raw = tempfile.mkstemp(prefix="climate-plot-", suffix=".json")
    out_fd, out_raw = tempfile.mkstemp(prefix="climate-plot-", suffix=".png")
    os.close(spec_fd)
    os.close(out_fd)
    spec_path = Path(spec_raw)
    out_path = Path(out_raw)
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    env["PYTHONUTF8"] = "1"
    run_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "timeout": 45.0,
        "env": env,
        "check": False,
        "close_fds": True,
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "openharness.climate.plot_worker",
                str(spec_path),
                str(out_path),
            ],
            **run_kwargs,
        )
        if completed.returncode != 0:
            return b""
        data = out_path.read_bytes()
        return data if data.startswith(_PNG_MAGIC) else b""
    except (OSError, subprocess.TimeoutExpired):
        return b""
    finally:
        for path in (spec_path, out_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _render_svg(prepared: PreparedPlot) -> bytes:
    width, height = 640, 480
    left, right, top, bottom = 60, 20, 40, 50
    plot_w = width - left - right
    plot_h = height - top - bottom
    values: tuple[float, ...]
    if prepared.chart_type == "histogram":
        values = _histogram_counts(prepared.y_values)
    else:
        values = prepared.y_values
    y_min = min(values)
    y_max = max(values)
    if y_min == y_max:
        y_min -= 1.0
        y_max += 1.0

    def to_y(value: float) -> float:
        return top + plot_h - (value - y_min) / (y_max - y_min) * plot_h

    shapes: list[str] = []
    if prepared.chart_type == "line":
        count = len(values)
        points: list[str] = []
        for index, value in enumerate(values):
            x_pos = left if count == 1 else left + index * plot_w / (count - 1)
            points.append(f"{x_pos:.2f},{to_y(value):.2f}")
        shapes.append(
            '<polyline fill="none" stroke="#2563eb" stroke-width="2" '
            f'points="{" ".join(points)}"/>'
        )
    else:
        count = max(len(values), 1)
        slot = plot_w / count
        bar_w = slot * 0.72
        zero = to_y(0.0) if y_min <= 0 <= y_max else top + plot_h
        for index, value in enumerate(values):
            x_pos = left + index * slot + (slot - bar_w) / 2
            y_pos = to_y(value)
            top_y = min(y_pos, zero)
            bar_h = max(abs(zero - y_pos), 1.0)
            shapes.append(
                f'<rect x="{x_pos:.2f}" y="{top_y:.2f}" width="{bar_w:.2f}" '
                f'height="{bar_h:.2f}" fill="#2563eb"/>'
            )

    title = _xml_escape(prepared.title) if prepared.title else _xml_escape(prepared.y_name)
    body = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}">'
            ),
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
            f'<text x="{width / 2:.2f}" y="24" text-anchor="middle" font-size="14">{title}</text>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#111111"/>',
            (
                f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" '
                'stroke="#111111"/>'
            ),
            *shapes,
            "</svg>",
            "",
        ]
    )
    return body.encode("utf-8")


def _histogram_counts(values: tuple[float, ...], bins: int = _HISTOGRAM_BINS) -> tuple[float, ...]:
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return (float(len(values)),) + (0.0,) * (bins - 1)
    width = (hi - lo) / bins
    counts = [0.0] * bins
    for value in values:
        index = min(int((value - lo) / width), bins - 1)
        counts[index] += 1.0
    return tuple(counts)


def _render_report_markdown(
    context: RunContext,
    *,
    title: str,
    summary: str,
    workspace: Path,
) -> str:
    """纯文本 Markdown：summary 只拼接，不执行模板 / HTML / Shell。"""
    modes = _acquisition_modes(context)
    profile = _latest_inspect_profile(context)
    plots = [item for item in context.artifacts if item.kind == "plot"]
    generated_at = utc_now()
    lines: list[str] = [
        "# " + title,
        "",
        "- run_id: " + context.run_id,
        "- objective: " + context.objective,
        "- mode: " + (", ".join(modes) if modes else "unknown"),
        "- generated_at: " + generated_at,
        "",
        "## Inspect",
    ]
    row_count = profile.get("row_count")
    lines.append("- row_count: " + (str(row_count) if row_count is not None else "unknown"))
    columns = profile.get("columns")
    if isinstance(columns, list):
        names = [
            str(column.get("name"))
            for column in columns
            if isinstance(column, dict) and column.get("name") is not None
        ]
        if names:
            lines.append("- columns: " + ", ".join(names))
    variables = profile.get("variables")
    if isinstance(variables, list):
        var_names = [str(item) for item in variables if isinstance(item, str)]
        if var_names:
            lines.append("- variables: " + ", ".join(var_names))
    warnings = profile.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.append("- warnings: " + str(len(warnings)))
    lines.extend(["", "## Plot"])
    if plots:
        for plot in plots:
            caption = plot.artifact_id
            lines.append("![" + caption + "](" + plot.path + ")")
    else:
        lines.append("（无图表产物）")
    lines.extend(["", "## Summary", summary, ""])
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return redact_secrets(text, workspace=workspace, catch_all_posix=False).replace(
        "\r\n", "\n"
    ).replace("\r", "\n")


def _acquisition_modes(context: RunContext) -> list[str]:
    modes: list[str] = []
    for step in context.steps:
        if step.action != "acquire_data" or step.status != "succeeded":
            continue
        path = ""
        if step.result and isinstance(step.result.get("path"), str):
            path = step.result["path"]
        name = path.rsplit("/", 1)[-1]
        if name == "sample.csv":
            modes.append("sample")
        elif name.startswith("local-"):
            modes.append("local")
        else:
            modes.append("acquired")
    return modes


def _latest_inspect_profile(context: RunContext) -> dict[str, Any]:
    for step in reversed(context.steps):
        if step.action == "inspect_dataset" and step.status == "succeeded" and step.result:
            return dict(step.result)
    return {}


