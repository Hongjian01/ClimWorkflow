"""Climate Context Schema：WorkspaceIndex 与 RunContext v2。"""

from __future__ import annotations

import json
import re
from collections import deque

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openharness.climate.errors import ERROR_RETRYABLE

# UUID v4：规范小写，version=4，variant=8/9/a/b
_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
# UTC RFC3339，强制 Z 后缀（不含偏移量形式）
_UTC_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

RunStatus = Literal["initialized", "running", "completed", "failed"]
StepAction = Literal["acquire_data", "inspect_dataset", "analyze_plot", "write_report"]
StepStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
ArtifactKind = Literal["dataset", "profile", "plot", "report"]
EventType = Literal[
    "run_created",
    "active_run_changed",
    "plan_created",
    "step_started",
    "step_succeeded",
    "step_failed",
    "step_skipped",
    "run_completed",
    "run_failed",
    "run_resumed",
    "migration_completed",
    "interrupted_recovered",
]


def _require_uuid_v4(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _UUID_V4.fullmatch(value):
        raise ValueError(f"{field} 必须是规范小写 UUID v4")
    return value


def _require_utc(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _UTC_RFC3339.fullmatch(value):
        raise ValueError(f"{field} 必须是 UTC RFC3339（以 Z 结尾）")
    return value


def _require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} 必须是 sha256:<64 小写十六进制>")
    return value


def _is_safe_relative_posix(path: str) -> bool:
    """词法级 workspace 相对 POSIX 路径（模型层，不做真实路径解析）。"""
    if not isinstance(path, str) or path.strip() == "" or path != path.strip():
        return False
    if "\\" in path or "\x00" in path or path.startswith(("~", "/")):
        return False
    if re.match(r"^[A-Za-z]:", path) or path.startswith("//"):
        return False
    parts = path.split("/")
    return not any(part == "" or part in {".", ".."} for part in parts)


def _json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_safe(v) for k, v in value.items())
    return False


class ClimateErrorObject(BaseModel):
    """持久化在 Context 中的结构化错误对象。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_code_and_retryable(self) -> ClimateErrorObject:
        if self.code not in ERROR_RETRYABLE:
            raise ValueError(f"未知错误码: {self.code}")
        if self.retryable != ERROR_RETRYABLE[self.code]:
            raise ValueError(f"retryable 与错误码 {self.code} 不一致")
        if not _json_safe(self.details):
            raise ValueError("details 只允许 JSON 值")
        return self


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    action: StepAction
    title: str
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus
    attempts: int = Field(ge=0)
    input_hash: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: ClimateErrorObject | None = None

    @field_validator("step_id")
    @classmethod
    def _step_id_nonempty(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("step_id 不能为空或含首尾空白")
        return value

    @field_validator("input_hash")
    @classmethod
    def _input_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_sha256(value, field="input_hash")

    @field_validator("started_at", "finished_at")
    @classmethod
    def _optional_utc(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_utc(value, field="timestamp")

    @field_validator("result")
    @classmethod
    def _result_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not _json_safe(value):
            raise ValueError("result 只允许 JSON 值")
        return value

    @model_validator(mode="after")
    def _time_order(self) -> Step:
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at 不得早于 started_at")
        return self


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: ArtifactKind
    path: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    created_by_step: str
    created_at: str

    @field_validator("artifact_id", "created_by_step")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("标识符不能为空或含首尾空白")
        return value

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        if not _is_safe_relative_posix(value):
            raise ValueError("artifact.path 必须是安全的 workspace 相对 POSIX 路径")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha(cls, value: str) -> str:
        return _require_sha256(value, field="sha256")

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: str) -> str:
        return _require_utc(value, field="created_at")


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    timestamp: str
    type: EventType
    step_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _ts(cls, value: str) -> str:
        return _require_utc(value, field="timestamp")

    @field_validator("data")
    @classmethod
    def _data_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not _json_safe(value):
            raise ValueError("event.data 只允许 JSON 值")
        return value


class WorkspaceIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    version: int = Field(ge=1)
    active_run_id: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    updated_at: str

    @field_validator("updated_at")
    @classmethod
    def _updated(cls, value: str) -> str:
        return _require_utc(value, field="updated_at")

    @field_validator("run_ids")
    @classmethod
    def _run_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for run_id in value:
            _require_uuid_v4(run_id, field="run_ids")
            if run_id in seen:
                raise ValueError("run_ids 必须去重")
            seen.add(run_id)
        return value

    @field_validator("active_run_id")
    @classmethod
    def _active(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_uuid_v4(value, field="active_run_id")

    @model_validator(mode="after")
    def _active_in_run_ids(self) -> WorkspaceIndex:
        if self.active_run_id is not None and self.active_run_id not in self.run_ids:
            raise ValueError("active_run_id 必须出现在 run_ids 中")
        return self


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    version: int = Field(ge=1)
    run_id: str
    objective: str
    status: RunStatus
    created_at: str
    updated_at: str
    steps: list[Step] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    last_error: ClimateErrorObject | None = None

    @field_validator("run_id")
    @classmethod
    def _run_id(cls, value: str) -> str:
        return _require_uuid_v4(value, field="run_id")

    @field_validator("created_at", "updated_at")
    @classmethod
    def _times(cls, value: str) -> str:
        return _require_utc(value, field="timestamp")

    @model_validator(mode="after")
    def _invariants(self) -> RunContext:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不得早于 created_at")

        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id 必须唯一")
        step_id_set = set(step_ids)

        artifact_ids = [art.artifact_id for art in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact_id 必须唯一")
        artifact_id_set = set(artifact_ids)

        # depends_on 引用与无环
        graph: dict[str, list[str]] = {sid: [] for sid in step_ids}
        indegree: dict[str, int] = {sid: 0 for sid in step_ids}
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in step_id_set:
                    raise ValueError(f"depends_on 引用不存在的 step: {dep}")
                graph[dep].append(step.step_id)
                indegree[step.step_id] += 1
        queue: deque[str] = deque(sid for sid, deg in indegree.items() if deg == 0)
        seen = 0
        while queue:
            node = queue.popleft()
            seen += 1
            for nxt in graph[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if step_ids and seen != len(step_ids):
            raise ValueError("depends_on 必须构成无环图")

        for art in self.artifacts:
            if art.created_by_step not in step_id_set:
                raise ValueError(f"artifact.created_by_step 引用不存在: {art.created_by_step}")

        for step in self.steps:
            if step.result is None:
                continue
            ids = step.result.get("artifact_ids")
            if ids is None:
                continue
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("result.artifact_ids 必须是字符串列表")
            for aid in ids:
                if aid not in artifact_id_set:
                    raise ValueError(f"result 引用不存在的 artifact: {aid}")

        if self.events:
            for index, event in enumerate(self.events, start=1):
                if event.sequence != index:
                    raise ValueError("event.sequence 必须从 1 起连续递增")
                if event.step_id is not None and event.step_id not in step_id_set:
                    raise ValueError(f"event.step_id 引用不存在: {event.step_id}")

        return self


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_CDS_INCLUSIVE_DAYS = 366


class CdsRequestInput(BaseModel):
    """G4 cds_request 严格输入；不含凭证，extra=forbid。"""

    model_config = ConfigDict(extra="forbid")

    dataset: str
    variables: list[str]
    area: list[float] = Field(min_length=4, max_length=4)
    date_start: str
    date_end: str
    format: Literal["netcdf", "grib"]
    allow_sample_fallback: bool = False

    @field_validator("date_start", "date_end")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
            raise ValueError("必须是 ISO 日期 YYYY-MM-DD")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("必须是合法 ISO 日期") from exc
        return value

    @field_validator("area")
    @classmethod
    def _area_bounds(cls, value: list[float]) -> list[float]:
        if len(value) != 4:
            raise ValueError("area 必须是 north/west/south/east 四个数值")
        north, west, south, east = (float(item) for item in value)
        if not -90.0 <= north <= 90.0 or not -90.0 <= south <= 90.0:
            raise ValueError("纬度必须在 [-90, 90]")
        if not -180.0 <= west <= 180.0 or not -180.0 <= east <= 180.0:
            raise ValueError("经度必须在 [-180, 180]")
        if north <= south:
            raise ValueError("north 必须大于 south")
        return [north, west, south, east]

    @model_validator(mode="after")
    def _cross_field(self) -> CdsRequestInput:
        from openharness.climate.formats import (
            DATASET_VARIABLES,
            SUPPORTED_DATASETS,
            SUPPORTED_FORMATS,
        )

        if self.dataset not in SUPPORTED_DATASETS:
            raise ValueError("dataset")
        if self.format not in SUPPORTED_FORMATS:
            raise ValueError("format")
        allowed = DATASET_VARIABLES[self.dataset]
        if not self.variables:
            raise ValueError("variables")
        unknown = [item for item in self.variables if item not in allowed]
        if unknown:
            raise ValueError("variables")
        ordered = [name for name in sorted(allowed) if name in set(self.variables)]
        start = date.fromisoformat(self.date_start)
        end = date.fromisoformat(self.date_end)
        if start > end:
            raise ValueError("date_order")
        inclusive_days = (end - start).days + 1
        if inclusive_days > _MAX_CDS_INCLUSIVE_DAYS:
            raise ValueError("date_span")
        return self.model_copy(update={"variables": ordered})


def dumps_climate_json(model: BaseModel) -> str:
    """确定性序列化：UTF-8、两空格、稳定键顺序、末尾换行。"""
    data = model.model_dump(mode="json")
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def loads_workspace_index(text: str) -> WorkspaceIndex:
    """从 JSON 文本加载 WorkspaceIndex（严格校验）。"""
    return WorkspaceIndex.model_validate(json.loads(text))


def loads_run_context(text: str) -> RunContext:
    """从 JSON 文本加载 RunContext（严格校验）。"""
    return RunContext.model_validate(json.loads(text))


class ClimateQueryKnowledgeInput(BaseModel):
    """G6 只读知识查询；禁止 code/shell/expr 与绝对路径字段。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class KnowledgeHit(BaseModel):
    """脱敏检索命中：相对路径 source，不含密钥。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=256)
    parent_text: str = Field(min_length=1, max_length=8000)
    source: str = Field(min_length=1, max_length=512)
    score: float
