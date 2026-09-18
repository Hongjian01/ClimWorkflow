"""STATE-001/002/003、IDEM-001、TEST-003：纯状态机转换表测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openharness.climate.errors import ClimateError
from openharness.climate.models import RunContext
from openharness.climate.repository import ContextRepository
from openharness.climate.state import WorkflowStateMachine
from openharness.utils import fs as fs_mod

RUN_ID = "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314"
CREATED = "2026-08-22T14:00:00Z"
HASH_A = "sha256:" + ("a" * 64)
HASH_B = "sha256:" + ("b" * 64)


def _run(**overrides: Any) -> RunContext:
    payload: dict[str, Any] = {
        "schema_version": 2,
        "version": 1,
        "run_id": RUN_ID,
        "objective": "分析示例温度序列并生成报告",
        "status": "initialized",
        "created_at": CREATED,
        "updated_at": CREATED,
        "steps": [],
        "artifacts": [],
        "events": [
            {
                "sequence": 1,
                "timestamp": CREATED,
                "type": "run_created",
                "step_id": None,
                "data": {},
            }
        ],
        "last_error": None,
    }
    payload.update(overrides)
    return RunContext.model_validate(payload)


def _step(
    step_id: str = "acquire",
    *,
    action: str = "acquire_data",
    status: str = "pending",
    attempts: int = 0,
    input_hash: str | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "action": action,
        "title": step_id,
        "depends_on": depends_on or [],
        "status": status,
        "attempts": attempts,
        "input_hash": input_hash,
        "started_at": CREATED if status != "pending" else None,
        "finished_at": CREATED if status in {"succeeded", "failed", "skipped"} else None,
        "result": result,
        "error": error,
    }


def _standard_plan_steps(
    *,
    statuses: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    st = statuses or {}
    return [
        _step("acquire", action="acquire_data", status=st.get("acquire", "pending")),
        _step(
            "inspect",
            action="inspect_dataset",
            status=st.get("inspect", "pending"),
            depends_on=["acquire"],
        ),
        _step(
            "plot",
            action="analyze_plot",
            status=st.get("plot", "pending"),
            depends_on=["inspect"],
        ),
        _step(
            "report",
            action="write_report",
            status=st.get("report", "pending"),
            depends_on=["inspect", "plot"],
        ),
    ]


def _repo_with_run(
    tmp_path: Path, context: RunContext
) -> tuple[ContextRepository, Path, RunContext]:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    repo = ContextRepository(workspace)
    repo.ensure_layout()
    saved = repo.save_run(context, expected_version=None)
    return repo, workspace, saved


# ---------------------------------------------------------------------------
# STATE-001：转换表
# ---------------------------------------------------------------------------

_LEGAL_RUN = [
    ("initialized", "plan_accepted", "running"),
    ("running", "report_succeeded", "completed"),
    ("initialized", "fatal_failure", "failed"),
    ("running", "fatal_failure", "failed"),
    ("failed", "explicit_resume", "running"),
    ("completed", "read_replay", "completed"),
]

_ILLEGAL_RUN = [
    ("initialized", "report_succeeded"),
    ("initialized", "explicit_resume"),
    ("initialized", "read_replay"),
    ("running", "plan_accepted"),
    ("running", "explicit_resume"),
    ("running", "read_replay"),
    ("failed", "plan_accepted"),
    ("failed", "report_succeeded"),
    ("failed", "fatal_failure"),
    ("failed", "read_replay"),
    ("completed", "plan_accepted"),
    ("completed", "report_succeeded"),
    ("completed", "fatal_failure"),
    ("completed", "explicit_resume"),
]

_LEGAL_STEP = [
    ("pending", "start", "running", True),
    ("failed", "retry", "running", True),
    ("running", "success", "succeeded", False),
    ("running", "operation_error", "failed", False),
    ("pending", "explicit_skip", "skipped", False),
    ("succeeded", "same_input_replay", "succeeded", False),
]

_ILLEGAL_STEP = [
    ("pending", "success"),
    ("pending", "operation_error"),
    ("pending", "retry"),
    ("pending", "same_input_replay"),
    ("running", "start"),
    ("running", "retry"),
    ("running", "explicit_skip"),
    ("running", "same_input_replay"),
    ("succeeded", "start"),
    ("succeeded", "retry"),
    ("succeeded", "success"),
    ("succeeded", "operation_error"),
    ("succeeded", "explicit_skip"),
    ("failed", "start"),
    ("failed", "success"),
    ("failed", "operation_error"),
    ("failed", "explicit_skip"),
    ("failed", "same_input_replay"),
    ("skipped", "start"),
    ("skipped", "retry"),
    ("skipped", "success"),
    ("skipped", "operation_error"),
    ("skipped", "explicit_skip"),
    ("skipped", "same_input_replay"),
]


@pytest.mark.parametrize("from_status,event,to_status", _LEGAL_RUN)
def test_transition_table_legal_run(
    tmp_path: Path, from_status: str, event: str, to_status: str
) -> None:
    """STATE-001：合法 run 转换。"""
    steps = _standard_plan_steps()
    if from_status == "running" and event == "report_succeeded":
        steps = _standard_plan_steps(
            statuses={
                "acquire": "succeeded",
                "inspect": "succeeded",
                "plot": "succeeded",
                "report": "succeeded",
            }
        )
    if from_status == "failed":
        steps = _standard_plan_steps(
            statuses={
                "acquire": "failed",
                "inspect": "pending",
                "plot": "pending",
                "report": "pending",
            }
        )
        # 可恢复错误（CLIMATE_INTERRUPTED）以满足 resume 条件
        steps[0]["error"] = {
            "code": "CLIMATE_INTERRUPTED",
            "message": "中断",
            "retryable": True,
            "details": {},
        }
    ctx = _run(
        status=from_status,
        steps=steps,
        version=1,
        last_error={
            "code": "CLIMATE_INTERRUPTED",
            "message": "中断",
            "retryable": True,
            "details": {},
        }
        if from_status == "failed"
        else None,
    )
    repo, workspace, saved = _repo_with_run(tmp_path, ctx)
    sm = WorkflowStateMachine(repo)
    path = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    before = path.read_bytes()

    result = sm.apply_run_event(RUN_ID, event, expected_version=saved.version)
    assert result.status == to_status
    if event == "read_replay":
        assert result.version == saved.version
        assert path.read_bytes() == before
    else:
        assert result.version == saved.version + 1


@pytest.mark.parametrize("from_status,event", _ILLEGAL_RUN)
def test_transition_table_illegal_run(
    tmp_path: Path, from_status: str, event: str
) -> None:
    """STATE-001：非法 run 转换零副作用。"""
    steps = _standard_plan_steps()
    ctx = _run(status=from_status, steps=steps)
    repo, workspace, saved = _repo_with_run(tmp_path, ctx)
    path = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    before = path.read_bytes()
    sm = WorkflowStateMachine(repo)

    with pytest.raises(ClimateError) as exc_info:
        sm.apply_run_event(RUN_ID, event, expected_version=saved.version)
    assert exc_info.value.code == "CLIMATE_INVALID_TRANSITION"
    assert path.read_bytes() == before
    loaded = repo.load_run(RUN_ID)
    assert loaded.version == saved.version
    assert loaded.status == from_status
    assert len(loaded.events) == len(saved.events)


@pytest.mark.parametrize("from_status,event,to_status,attempts_inc", _LEGAL_STEP)
def test_transition_table_legal_step(
    tmp_path: Path,
    from_status: str,
    event: str,
    to_status: str,
    attempts_inc: bool,
) -> None:
    """STATE-001：合法 step 转换。"""
    step = _step(status=from_status, attempts=2 if from_status != "pending" else 0)
    if from_status == "succeeded":
        step["input_hash"] = HASH_A
        step["result"] = {"ok": True}
    ctx = _run(status="running", steps=[step])
    repo, _workspace, saved = _repo_with_run(tmp_path, ctx)
    sm = WorkflowStateMachine(repo)
    kwargs: dict[str, Any] = {}
    if event in {"start", "retry", "same_input_replay"}:
        kwargs["input_hash"] = HASH_A
    if event == "success":
        kwargs["result"] = {"ok": True}
        kwargs["input_hash"] = HASH_A
    if event == "operation_error":
        kwargs["error"] = {
            "code": "CLIMATE_DATA_INVALID",
            "message": "数据无效",
            "retryable": False,
            "details": {},
        }

    result = sm.apply_step_event(
        RUN_ID, "acquire", event, expected_version=saved.version, **kwargs
    )
    got = next(s for s in result.steps if s.step_id == "acquire")
    assert got.status == to_status
    base_attempts = 2 if from_status != "pending" else 0
    if attempts_inc:
        assert got.attempts == base_attempts + 1
    else:
        assert got.attempts == base_attempts
    if event == "same_input_replay":
        assert result.version == saved.version
    else:
        assert result.version == saved.version + 1


@pytest.mark.parametrize("from_status,event", _ILLEGAL_STEP)
def test_transition_table_illegal_step(
    tmp_path: Path, from_status: str, event: str
) -> None:
    """STATE-001：非法 step 转换零副作用。"""
    step = _step(status=from_status, attempts=1, input_hash=HASH_A)
    ctx = _run(status="running", steps=[step])
    repo, workspace, saved = _repo_with_run(tmp_path, ctx)
    path = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    before = path.read_bytes()
    sm = WorkflowStateMachine(repo)

    with pytest.raises(ClimateError) as exc_info:
        sm.apply_step_event(
            RUN_ID,
            "acquire",
            event,
            expected_version=saved.version,
            input_hash=HASH_A,
        )
    assert exc_info.value.code == "CLIMATE_INVALID_TRANSITION"
    assert path.read_bytes() == before


def test_transition_table(tmp_path: Path) -> None:
    """STATE-001 聚合入口（SPEC 追踪矩阵 node）。"""
    ctx = _run(status="initialized", steps=_standard_plan_steps())
    repo, workspace, saved = _repo_with_run(tmp_path, ctx)
    sm = WorkflowStateMachine(repo)
    running = sm.apply_run_event(RUN_ID, "plan_accepted", expected_version=saved.version)
    assert running.status == "running"

    completed = running.model_copy(update={"status": "completed"})
    saved_done = repo.save_run(completed, expected_version=running.version)
    path = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    before = path.read_bytes()
    with pytest.raises(ClimateError) as exc_info:
        sm.apply_run_event(RUN_ID, "plan_accepted", expected_version=saved_done.version)
    assert exc_info.value.code == "CLIMATE_INVALID_TRANSITION"
    assert path.read_bytes() == before


def test_attempt_and_event_sequence_rules(tmp_path: Path) -> None:
    """STATE-002：attempts 仅在进入 running 时 +1；真实转换 event sequence +1。"""
    ctx = _run(
        status="running",
        steps=[_step(status="pending", attempts=0)],
    )
    repo, _ws, saved = _repo_with_run(tmp_path, ctx)
    sm = WorkflowStateMachine(repo)

    started = sm.apply_step_event(
        RUN_ID, "acquire", "start", expected_version=saved.version, input_hash=HASH_A
    )
    assert started.steps[0].status == "running"
    assert started.steps[0].attempts == 1
    assert started.events[-1].sequence == 2
    assert started.events[-1].type == "step_started"

    ok = sm.apply_step_event(
        RUN_ID,
        "acquire",
        "success",
        expected_version=started.version,
        result={"ok": True},
        input_hash=HASH_A,
    )
    assert ok.steps[0].attempts == 1
    assert ok.events[-1].sequence == 3
    assert ok.events[-1].type == "step_succeeded"

    interrupted_ctx = _run(
        status="running",
        version=1,
        steps=[_step(status="running", attempts=1)],
    )
    repo2, ws2, saved2 = _repo_with_run(tmp_path / "int", interrupted_ctx)
    part = ws2 / ".climate" / "data" / RUN_ID / ".sample.csv.part"
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_text("partial", encoding="utf-8")
    sm2 = WorkflowStateMachine(repo2)
    recovered = sm2.recover_interrupted_steps(RUN_ID, expected_version=saved2.version)
    assert recovered.steps[0].status == "failed"
    assert recovered.steps[0].error is not None
    assert recovered.steps[0].error.code == "CLIMATE_INTERRUPTED"
    assert recovered.events[-1].type == "interrupted_recovered"
    assert not part.exists()


def test_error_recording_preserves_original_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STATE-003：可写时记录 step.error；不可写返回原持久化错误，不递归记录。"""
    ctx = _run(status="running", steps=[_step(status="running", attempts=1)])
    repo, _workspace, saved = _repo_with_run(tmp_path, ctx)
    sm = WorkflowStateMachine(repo)

    failed = sm.apply_step_event(
        RUN_ID,
        "acquire",
        "operation_error",
        expected_version=saved.version,
        error={
            "code": "CLIMATE_DATA_INVALID",
            "message": "列缺失",
            "retryable": False,
            "details": {"field": "temperature_c"},
        },
    )
    assert failed.steps[0].status == "failed"
    assert failed.steps[0].error is not None
    assert failed.steps[0].error.code == "CLIMATE_DATA_INVALID"

    ctx2 = _run(status="running", steps=[_step(status="running", attempts=1)])
    repo2, ws2, saved2 = _repo_with_run(tmp_path / "nw", ctx2)
    path = ws2 / ".climate" / "runs" / RUN_ID / "context.json"
    before = path.read_bytes()
    write_calls: list[Path] = []

    def boom(path_arg: Any, data: str, **kwargs: Any) -> None:
        write_calls.append(Path(path_arg))
        raise OSError("simulated unwritable")

    monkeypatch.setattr("openharness.climate.repository.atomic_write_text", boom)
    sm2 = WorkflowStateMachine(repo2)
    with pytest.raises(ClimateError) as exc_info:
        sm2.apply_step_event(
            RUN_ID,
            "acquire",
            "operation_error",
            expected_version=saved2.version,
            error={
                "code": "CLIMATE_DATA_INVALID",
                "message": "列缺失",
                "retryable": False,
                "details": {},
            },
        )
    assert exc_info.value.code == "CLIMATE_WRITE_FAILED"
    assert len(write_calls) == 1
    monkeypatch.setattr(
        "openharness.climate.repository.atomic_write_text", fs_mod.atomic_write_text
    )
    assert path.read_bytes() == before


def test_replay_same_input_and_conflict_on_different_input(tmp_path: Path) -> None:
    """IDEM-001：同输入无版本变化；不同输入 IDEMPOTENCY_CONFLICT。"""
    step = _step(
        status="succeeded",
        attempts=1,
        input_hash=HASH_A,
        result={"artifact_ids": []},
    )
    ctx = _run(status="running", steps=[step])
    repo, workspace, saved = _repo_with_run(tmp_path, ctx)
    path = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    before = path.read_bytes()
    sm = WorkflowStateMachine(repo)

    replayed = sm.apply_step_event(
        RUN_ID,
        "acquire",
        "same_input_replay",
        expected_version=saved.version,
        input_hash=HASH_A,
    )
    assert replayed.version == saved.version
    assert replayed.steps[0].result == {"artifact_ids": []}
    assert path.read_bytes() == before

    with pytest.raises(ClimateError) as exc_info:
        sm.apply_step_event(
            RUN_ID,
            "acquire",
            "same_input_replay",
            expected_version=saved.version,
            input_hash=HASH_B,
        )
    assert exc_info.value.code == "CLIMATE_IDEMPOTENCY_CONFLICT"
    assert path.read_bytes() == before

    with pytest.raises(ClimateError) as exc_info2:
        sm.replay_or_start_step(
            RUN_ID,
            "acquire",
            input_hash=HASH_B,
            expected_version=saved.version,
        )
    assert exc_info2.value.code == "CLIMATE_IDEMPOTENCY_CONFLICT"


def test_confirm_plan_appends_event_and_replace_resets(tmp_path: Path) -> None:
    """PLAN-002：全 pending 时可确认；换 plan 后旧确认无效。"""
    from openharness.climate.models import Step
    from openharness.climate.state import is_plan_confirmed

    steps = _standard_plan_steps()
    ctx = _run(status="initialized", steps=[])
    repo, _ws, saved = _repo_with_run(tmp_path, ctx)
    sm = WorkflowStateMachine(repo)
    step_models = [Step.model_validate(item) for item in steps]
    topo = [item["step_id"] for item in steps]
    running = sm.accept_plan(
        RUN_ID,
        step_models,
        expected_version=saved.version,
        topological_ids=topo,
        confirmed=False,
    )
    assert running.status == "running"
    assert running.schema_version == 2
    assert not is_plan_confirmed(running)
    confirmed = sm.confirm_plan(
        RUN_ID, expected_version=running.version, topological_ids=topo
    )
    assert is_plan_confirmed(confirmed)
    again = sm.confirm_plan(
        RUN_ID, expected_version=confirmed.version, topological_ids=topo
    )
    assert again.version == confirmed.version
    replaced = sm.accept_plan(
        RUN_ID,
        step_models,
        expected_version=again.version,
        topological_ids=topo,
        confirmed=False,
    )
    assert not is_plan_confirmed(replaced)
    assert replaced.status == "running"
