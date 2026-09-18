"""Climate 工作流纯状态机：只计算/校验转换，持久化委托 Repository。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openharness.climate.errors import climate_error
from openharness.climate.models import (
    Artifact,
    ClimateErrorObject,
    Event,
    RunContext,
    Step,
)
from openharness.climate.repository import ContextRepository

# run: (status, event) → next_status
_RUN_TRANSITIONS: dict[tuple[str, str], str] = {
    ("initialized", "plan_accepted"): "running",
    ("running", "report_succeeded"): "completed",
    ("initialized", "fatal_failure"): "failed",
    ("running", "fatal_failure"): "failed",
    ("failed", "explicit_resume"): "running",
    ("completed", "read_replay"): "completed",
}

# step: (status, event) → (next_status, attempts_increment)
_STEP_TRANSITIONS: dict[tuple[str, str], tuple[str, bool]] = {
    ("pending", "start"): ("running", True),
    ("failed", "retry"): ("running", True),
    ("running", "success"): ("succeeded", False),
    ("running", "operation_error"): ("failed", False),
    ("pending", "explicit_skip"): ("skipped", False),
    ("succeeded", "same_input_replay"): ("succeeded", False),
}

_RUN_EVENT_TYPE: dict[str, str] = {
    "plan_accepted": "plan_created",
    "report_succeeded": "run_completed",
    "fatal_failure": "run_failed",
    "explicit_resume": "run_resumed",
}

_STEP_EVENT_TYPE: dict[str, str] = {
    "start": "step_started",
    "retry": "step_started",
    "success": "step_succeeded",
    "operation_error": "step_failed",
    "explicit_skip": "step_skipped",
}


class WorkflowStateMachine:
    """状态机只做转换校验与内存计算；写盘统一走 Repository。"""

    def __init__(self, repository: ContextRepository) -> None:
        self._repo = repository

    def apply_run_event(
        self,
        run_id: str,
        event: str,
        *,
        expected_version: int,
        error: dict[str, Any] | ClimateErrorObject | None = None,
    ) -> RunContext:
        current = self._repo.load_run(run_id)
        if current.version != expected_version:
            raise climate_error(
                "CLIMATE_VERSION_CONFLICT",
                "Context 版本冲突",
                details={
                    "expected_version": expected_version,
                    "actual_version": current.version,
                    "field": "run",
                },
                workspace=self._repo.workspace,
            )

        key = (current.status, event)
        if key not in _RUN_TRANSITIONS:
            raise climate_error(
                "CLIMATE_INVALID_TRANSITION",
                "非法 run 状态转换",
                details={"status": current.status, "field": event},
                workspace=self._repo.workspace,
            )
        next_status = _RUN_TRANSITIONS[key]

        if event == "read_replay":
            # 只读/同输入幂等：零副作用
            return current

        if event == "report_succeeded":
            unfinished = [
                s
                for s in current.steps
                if s.status not in {"succeeded", "skipped"}
            ]
            if unfinished:
                raise climate_error(
                    "CLIMATE_INVALID_TRANSITION",
                    "仍有未成功 step，不能标记 completed",
                    details={"status": current.status, "field": "report_succeeded"},
                    workspace=self._repo.workspace,
                )

        if event == "explicit_resume" and not self._can_resume(current):
            raise climate_error(
                "CLIMATE_INVALID_TRANSITION",
                "错误不可恢复或无可重试 step",
                details={"status": current.status, "field": "explicit_resume"},
                workspace=self._repo.workspace,
            )

        now = _utc_now()
        updates: dict[str, Any] = {
            "status": next_status,
            "updated_at": now,
        }
        if event == "fatal_failure":
            err_obj = _coerce_error(
                error
                or {
                    "code": "CLIMATE_INVALID_INPUT",
                    "message": "工作流失败",
                    "retryable": False,
                    "details": {},
                },
                workspace=self._repo.workspace,
            )
            updates["last_error"] = err_obj
        elif event == "explicit_resume":
            updates["last_error"] = None

        new_event = Event(
            sequence=len(current.events) + 1,
            timestamp=now,
            type=_RUN_EVENT_TYPE[event],  # type: ignore[arg-type]
            step_id=None,
            data={},
        )
        updates["events"] = [*current.events, new_event]
        updated = current.model_copy(update=updates)
        return self._persist(updated, expected_version=expected_version)

    def accept_plan(
        self,
        run_id: str,
        steps: list[Step],
        *,
        expected_version: int,
        topological_ids: list[str],
        confirmed: bool = False,
    ) -> RunContext:
        """一次 mutation：写入 plan；initialized → running。confirmed 时同盘追加确认事件。"""
        current = self._repo.load_run(run_id)
        if current.version != expected_version:
            raise climate_error(
                "CLIMATE_VERSION_CONFLICT",
                "Context 版本冲突",
                details={
                    "expected_version": expected_version,
                    "actual_version": current.version,
                    "field": "run",
                },
                workspace=self._repo.workspace,
            )
        if any(step.status != "pending" for step in current.steps):
            raise climate_error(
                "CLIMATE_INVALID_TRANSITION",
                "已开始业务 step 后不得替换 plan",
                details={"status": current.status, "field": "plan_accepted"},
                workspace=self._repo.workspace,
            )
        if current.status == "initialized":
            if (current.status, "plan_accepted") not in _RUN_TRANSITIONS:
                raise climate_error(
                    "CLIMATE_INVALID_TRANSITION",
                    "非法 run 状态转换",
                    details={"status": current.status, "field": "plan_accepted"},
                    workspace=self._repo.workspace,
                )
            next_status = _RUN_TRANSITIONS[(current.status, "plan_accepted")]
        elif current.status == "running":
            # 全 pending 时允许整表替换；run 保持 running，不走 apply_run_event 转换表。
            next_status = "running"
        else:
            raise climate_error(
                "CLIMATE_INVALID_TRANSITION",
                "非法 run 状态转换",
                details={"status": current.status, "field": "plan_accepted"},
                workspace=self._repo.workspace,
            )

        now = _utc_now()
        created = Event(
            sequence=len(current.events) + 1,
            timestamp=now,
            type="plan_created",
            step_id=None,
            data={"step_ids": list(topological_ids)},
        )
        new_events = [*current.events, created]
        if confirmed:
            new_events.append(
                Event(
                    sequence=created.sequence + 1,
                    timestamp=now,
                    type="plan_confirmed",
                    step_id=None,
                    data={"step_ids": list(topological_ids)},
                )
            )
        updated = current.model_copy(
            update={
                "status": next_status,
                "steps": list(steps),
                "updated_at": now,
                "events": new_events,
            }
        )
        return self._persist(updated, expected_version=expected_version)

    def confirm_plan(
        self,
        run_id: str,
        *,
        expected_version: int,
        topological_ids: list[str],
    ) -> RunContext:
        """全 pending 时追加 plan_confirmed；已确认则幂等返回。"""
        current = self._repo.load_run(run_id)
        if current.version != expected_version:
            raise climate_error(
                "CLIMATE_VERSION_CONFLICT",
                "Context 版本冲突",
                details={
                    "expected_version": expected_version,
                    "actual_version": current.version,
                    "field": "run",
                },
                workspace=self._repo.workspace,
            )
        if current.status != "running":
            raise climate_error(
                "CLIMATE_INVALID_TRANSITION",
                "非法 run 状态转换",
                details={"status": current.status, "field": "plan_confirmed"},
                workspace=self._repo.workspace,
            )
        if not current.steps or any(step.status != "pending" for step in current.steps):
            raise climate_error(
                "CLIMATE_INVALID_TRANSITION",
                "已开始业务 step 后不得确认或替换 plan",
                details={"status": current.status, "field": "plan_confirmed"},
                workspace=self._repo.workspace,
            )
        if is_plan_confirmed(current):
            return current
        now = _utc_now()
        new_event = Event(
            sequence=len(current.events) + 1,
            timestamp=now,
            type="plan_confirmed",
            step_id=None,
            data={"step_ids": list(topological_ids)},
        )
        updated = current.model_copy(
            update={
                "updated_at": now,
                "events": [*current.events, new_event],
            }
        )
        return self._persist(updated, expected_version=expected_version)

    def apply_step_event(
        self,
        run_id: str,
        step_id: str,
        event: str,
        *,
        expected_version: int,
        input_hash: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | ClimateErrorObject | None = None,
        artifacts: list[Artifact] | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> RunContext:
        current = self._repo.load_run(run_id)
        if current.version != expected_version:
            raise climate_error(
                "CLIMATE_VERSION_CONFLICT",
                "Context 版本冲突",
                details={
                    "expected_version": expected_version,
                    "actual_version": current.version,
                    "field": "run",
                },
                workspace=self._repo.workspace,
            )

        step = _find_step(current, step_id, workspace=self._repo.workspace)
        key = (step.status, event)
        if key not in _STEP_TRANSITIONS:
            raise climate_error(
                "CLIMATE_INVALID_TRANSITION",
                "非法 step 状态转换",
                details={"status": step.status, "step_id": step_id, "field": event},
                workspace=self._repo.workspace,
            )

        # 幂等：succeeded + same_input_replay
        if event == "same_input_replay":
            if input_hash is None or step.input_hash is None:
                raise climate_error(
                    "CLIMATE_INVALID_INPUT",
                    "幂等重放需要 input_hash",
                    details={"step_id": step_id, "field": "input_hash"},
                    workspace=self._repo.workspace,
                )
            if input_hash != step.input_hash:
                raise climate_error(
                    "CLIMATE_IDEMPOTENCY_CONFLICT",
                    "成功 step 被不同输入重放",
                    details={"step_id": step_id, "field": "input_hash"},
                    workspace=self._repo.workspace,
                )
            return current

        next_status, inc_attempts = _STEP_TRANSITIONS[key]
        now = _utc_now()
        step_updates: dict[str, Any] = {"status": next_status}
        if inc_attempts:
            step_updates["attempts"] = step.attempts + 1
            step_updates["started_at"] = now
            step_updates["finished_at"] = None
            step_updates["error"] = None
            if input_hash is not None:
                step_updates["input_hash"] = input_hash
        if event == "success":
            step_updates["finished_at"] = now
            step_updates["result"] = result
            step_updates["error"] = None
            if input_hash is not None:
                step_updates["input_hash"] = input_hash
        if event == "operation_error":
            step_updates["finished_at"] = now
            step_updates["error"] = _coerce_error(
                error
                or {
                    "code": "CLIMATE_INVALID_INPUT",
                    "message": "step 失败",
                    "retryable": False,
                    "details": {},
                },
                workspace=self._repo.workspace,
            )
        if event == "explicit_skip":
            step_updates["finished_at"] = now

        new_step = step.model_copy(update=step_updates)
        new_steps = [new_step if s.step_id == step_id else s for s in current.steps]
        new_event = Event(
            sequence=len(current.events) + 1,
            timestamp=now,
            type=_STEP_EVENT_TYPE[event],  # type: ignore[arg-type]
            step_id=step_id,
            data=dict(event_data or {}),
        )
        new_artifacts = list(current.artifacts)
        if event == "success" and artifacts:
            new_artifacts.extend(artifacts)
        updated = current.model_copy(
            update={
                "steps": new_steps,
                "artifacts": new_artifacts,
                "events": [*current.events, new_event],
                "updated_at": now,
            }
        )
        return self._persist(updated, expected_version=expected_version)

    def replay_or_start_step(
        self,
        run_id: str,
        step_id: str,
        *,
        input_hash: str,
        expected_version: int,
    ) -> RunContext:
        """统一入口：succeeded 同 hash 重放；不同 hash 冲突；pending/failed 进入 running。"""
        current = self._repo.load_run(run_id)
        step = _find_step(current, step_id, workspace=self._repo.workspace)
        if step.status == "succeeded":
            if step.input_hash == input_hash:
                return self.apply_step_event(
                    run_id,
                    step_id,
                    "same_input_replay",
                    expected_version=expected_version,
                    input_hash=input_hash,
                )
            raise climate_error(
                "CLIMATE_IDEMPOTENCY_CONFLICT",
                "成功 step 被不同输入重放",
                details={"step_id": step_id, "field": "input_hash"},
                workspace=self._repo.workspace,
            )
        if step.status == "pending":
            return self.apply_step_event(
                run_id,
                step_id,
                "start",
                expected_version=expected_version,
                input_hash=input_hash,
            )
        if step.status == "failed":
            return self.apply_step_event(
                run_id,
                step_id,
                "retry",
                expected_version=expected_version,
                input_hash=input_hash,
            )
        raise climate_error(
            "CLIMATE_INVALID_TRANSITION",
            "当前 step 状态不允许 start/replay",
            details={"status": step.status, "step_id": step_id},
            workspace=self._repo.workspace,
        )

    def recover_interrupted_steps(
        self,
        run_id: str,
        *,
        expected_version: int,
    ) -> RunContext:
        """残留 running step → failed/CLIMATE_INTERRUPTED，并清理 .part。"""
        current = self._repo.load_run(run_id)
        if current.version != expected_version:
            raise climate_error(
                "CLIMATE_VERSION_CONFLICT",
                "Context 版本冲突",
                details={
                    "expected_version": expected_version,
                    "actual_version": current.version,
                    "field": "run",
                },
                workspace=self._repo.workspace,
            )

        now = _utc_now()
        interrupted = ClimateErrorObject(
            code="CLIMATE_INTERRUPTED",
            message="进程恢复：残留 running step 已中断",
            retryable=True,
            details={},
        )
        changed = False
        new_steps: list[Step] = []
        new_events = list(current.events)
        for step in current.steps:
            if step.status != "running":
                new_steps.append(step)
                continue
            changed = True
            new_steps.append(
                step.model_copy(
                    update={
                        "status": "failed",
                        "finished_at": now,
                        "error": interrupted,
                    }
                )
            )
            new_events.append(
                Event(
                    sequence=len(new_events) + 1,
                    timestamp=now,
                    type="interrupted_recovered",
                    step_id=step.step_id,
                    data={},
                )
            )
            self._cleanup_part_files(run_id)

        if not changed:
            return current

        updated = current.model_copy(
            update={
                "steps": new_steps,
                "events": new_events,
                "updated_at": now,
                "last_error": interrupted,
            }
        )
        return self._persist(updated, expected_version=expected_version)

    def _persist(self, context: RunContext, *, expected_version: int) -> RunContext:
        """单次委托 Repository；写失败原样抛出，不递归记录。"""
        return self._repo.save_run(context, expected_version=expected_version)

    def _can_resume(self, context: RunContext) -> bool:
        retryable = (
            context.last_error is not None and context.last_error.retryable
        ) or any(s.error is not None and s.error.retryable for s in context.steps)
        if not retryable and context.last_error is not None and not context.last_error.retryable:
            return False
        return any(s.status not in {"succeeded", "skipped"} for s in context.steps)

    def _cleanup_part_files(self, run_id: str) -> None:
        for root_name in ("data", "output"):
            root = self._repo.workspace / ".climate" / root_name / run_id
            if not root.is_dir():
                continue
            for path in root.rglob("*.part"):
                if path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        continue


def is_plan_confirmed(context: RunContext) -> bool:
    """最新 plan_created 之后是否存在 sequence 更大的 plan_confirmed。"""
    created_seq: int | None = None
    confirmed_after = False
    for event in context.events:
        if event.type == "plan_created":
            created_seq = event.sequence
            confirmed_after = False
        elif (
            event.type == "plan_confirmed"
            and created_seq is not None
            and event.sequence > created_seq
        ):
            confirmed_after = True
    return confirmed_after


def _find_step(
    context: RunContext, step_id: str, *, workspace: Path
) -> Step:
    for step in context.steps:
        if step.step_id == step_id:
            return step
    raise climate_error(
        "CLIMATE_INVALID_INPUT",
        "step 不存在",
        details={"step_id": step_id},
        workspace=workspace,
    )


def _coerce_error(
    error: dict[str, Any] | ClimateErrorObject,
    *,
    workspace: Path,
) -> ClimateErrorObject:
    if isinstance(error, ClimateErrorObject):
        return error
    try:
        return ClimateErrorObject.model_validate(error)
    except Exception as exc:
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "error 对象无效",
            details={"field": "error"},
            workspace=workspace,
        ) from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
