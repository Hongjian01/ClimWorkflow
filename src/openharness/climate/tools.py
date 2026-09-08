"""Climate 工具：schema、BaseTool 实现与统一 JSON envelope。"""

from __future__ import annotations

import re
from abc import ABC
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openharness.climate.errors import (
    ClimateError,
    encode_tool_result_json,
    failure_envelope,
    success_envelope,
)
from openharness.climate.pipeline import (
    acquire_data,
    analyze_plot,
    init_workflow,
    inspect_dataset,
    plan_steps,
    read_context,
    validate_artifacts,
    write_report,
)
from openharness.climate.prompts import FIELD_DESCRIPTIONS, TOOL_DESCRIPTIONS
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_STEP_ID = re.compile(r"^[a-z0-9-]{1,64}$")


def _optional_uuid_v4(value: str | None) -> str | None:
    if value is None:
        return None
    if not _UUID_V4.fullmatch(value):
        raise ValueError("必须是规范小写 UUID v4")
    return value


class ClimateInitWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description=FIELD_DESCRIPTIONS["objective"],
    )
    run_id: str | None = None
    resume_run_id: str | None = None

    @field_validator("run_id", "resume_run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)

    @model_validator(mode="after")
    def _exclusive(self) -> ClimateInitWorkflowInput:
        if self.run_id is not None and self.resume_run_id is not None:
            raise ValueError("run_id 与 resume_run_id 互斥")
        if self.resume_run_id is not None:
            if self.objective is not None:
                raise ValueError("resume 时不得提供 objective")
        elif self.objective is None:
            raise ValueError("新建 run 必须提供 objective")
        return self


class ClimatePlanStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    action: Literal["acquire_data", "inspect_dataset", "analyze_plot", "write_report"]
    title: str = Field(
        min_length=1,
        max_length=200,
        description=FIELD_DESCRIPTIONS["plan_title"],
    )
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("depends_on")
    @classmethod
    def _deps(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _STEP_ID.fullmatch(item):
                raise ValueError("depends_on 必须是合法 step_id")
        return value


class ClimatePlanStepsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    steps: list[ClimatePlanStepInput] = Field(min_length=4, max_length=32)

    @field_validator("run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)


class ClimateAcquireDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    mode: Literal["sample", "local", "cds"]
    path: str | None = None
    cds_request: dict[str, Any] | None = Field(
        default=None,
        description=FIELD_DESCRIPTIONS["cds_request"],
    )

    @field_validator("run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)

    @model_validator(mode="after")
    def _mode_fields(self) -> ClimateAcquireDataInput:
        if self.mode == "sample" and (self.path is not None or self.cds_request is not None):
            raise ValueError("sample 模式不得提供 path 或 cds_request")
        if self.mode == "local":
            if self.path is None:
                raise ValueError("local 模式必须提供 path")
            if self.cds_request is not None:
                raise ValueError("local 模式不得提供 cds_request")
        if self.mode == "cds":
            if self.path is not None:
                raise ValueError("cds 模式不得提供 path")
            if self.cds_request is None:
                raise ValueError("cds 模式必须提供 cds_request")
        return self


class ClimateInspectDatasetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    path: str | None = None

    @field_validator("run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)


class ClimateAnalyzePlotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    path: str | None = None
    chart_type: Literal["line", "bar", "histogram"]
    x: str | None = None
    y: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)

    @field_validator("run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)

    @model_validator(mode="after")
    def _xy_rules(self) -> ClimateAnalyzePlotInput:
        if self.chart_type in {"line", "bar"} and not self.x:
            raise ValueError("line/bar 需要 x 与 y")
        if self.chart_type == "histogram" and self.x is not None:
            raise ValueError("histogram 只使用 y")
        return self


class ClimateWriteReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(
        min_length=1,
        max_length=12000,
        description=FIELD_DESCRIPTIONS["report_summary"],
    )

    @field_validator("run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)


class ClimateReadContextInput(BaseModel):

    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    include_events: bool = False
    event_limit: int = Field(default=100, ge=1, le=1000)

    @field_validator("run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)


class ClimateValidateArtifactsInput(BaseModel):
    """只读产物校验；禁止 code/shell/expr 等自由执行字段。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None

    @field_validator("run_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return _optional_uuid_v4(value)


class ClimateTool(BaseTool, ABC):
    """统一捕获 ClimateError 并编码 JSON envelope。"""

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    def _result(self, runner: Any) -> ToolResult:
        run_id: str | None = None
        version: int | None = None
        try:
            data, run_id, version = runner()
            payload = success_envelope(data, run_id=run_id, context_version=version)
            return ToolResult(output=encode_tool_result_json(payload), is_error=False)
        except ClimateError as exc:
            rid = run_id
            if rid is None and isinstance(exc.details.get("run_id"), str):
                rid = exc.details["run_id"]
            payload = failure_envelope(exc, run_id=rid, context_version=version)
            return ToolResult(output=encode_tool_result_json(payload), is_error=True)


class ClimateInitWorkflowTool(ClimateTool):
    name = "climate_init_workflow"
    description = TOOL_DESCRIPTIONS["climate_init_workflow"]
    input_model = ClimateInitWorkflowInput

    async def execute(
        self, arguments: ClimateInitWorkflowInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: init_workflow(
                workspace,
                objective=arguments.objective,
                run_id=arguments.run_id,
                resume_run_id=arguments.resume_run_id,
            ),
        )


class ClimatePlanStepsTool(ClimateTool):
    name = "climate_plan_steps"
    description = TOOL_DESCRIPTIONS["climate_plan_steps"]
    input_model = ClimatePlanStepsInput

    async def execute(
        self, arguments: ClimatePlanStepsInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: plan_steps(
                workspace,
                run_id=arguments.run_id,
                steps=list(arguments.steps),
            ),
        )


class ClimateAcquireDataTool(ClimateTool):
    name = "climate_acquire_data"
    description = TOOL_DESCRIPTIONS["climate_acquire_data"]
    input_model = ClimateAcquireDataInput

    async def execute(
        self, arguments: ClimateAcquireDataInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: acquire_data(
                workspace,
                run_id=arguments.run_id,
                step_id=arguments.step_id,
                mode=arguments.mode,
                path=arguments.path,
                cds_request=arguments.cds_request,
            ),
        )


class ClimateInspectDatasetTool(ClimateTool):
    name = "climate_inspect_dataset"
    description = TOOL_DESCRIPTIONS["climate_inspect_dataset"]
    input_model = ClimateInspectDatasetInput

    async def execute(
        self, arguments: ClimateInspectDatasetInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: inspect_dataset(
                workspace,
                run_id=arguments.run_id,
                step_id=arguments.step_id,
                path=arguments.path,
            ),
        )


class ClimateAnalyzePlotTool(ClimateTool):
    name = "climate_analyze_plot"
    description = TOOL_DESCRIPTIONS["climate_analyze_plot"]
    input_model = ClimateAnalyzePlotInput

    async def execute(
        self, arguments: ClimateAnalyzePlotInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: analyze_plot(
                workspace,
                run_id=arguments.run_id,
                step_id=arguments.step_id,
                path=arguments.path,
                chart_type=arguments.chart_type,
                x=arguments.x,
                y=arguments.y,
                title=arguments.title,
            ),
        )


class ClimateWriteReportTool(ClimateTool):
    name = "climate_write_report"
    description = TOOL_DESCRIPTIONS["climate_write_report"]
    input_model = ClimateWriteReportInput

    async def execute(
        self, arguments: ClimateWriteReportInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: write_report(
                workspace,
                run_id=arguments.run_id,
                step_id=arguments.step_id,
                title=arguments.title,
                summary=arguments.summary,
            ),
        )


class ClimateReadContextTool(ClimateTool):


    name = "climate_read_context"
    description = TOOL_DESCRIPTIONS["climate_read_context"]
    input_model = ClimateReadContextInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ClimateReadContextInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: read_context(
                workspace,
                run_id=arguments.run_id,
                include_events=arguments.include_events,
                event_limit=arguments.event_limit,
            ),
        )


class ClimateValidateArtifactsTool(ClimateTool):
    name = "climate_validate_artifacts"
    description = TOOL_DESCRIPTIONS["climate_validate_artifacts"]
    input_model = ClimateValidateArtifactsInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ClimateValidateArtifactsInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = Path(context.cwd).resolve()
        return self._result(
            lambda: validate_artifacts(
                workspace,
                run_id=arguments.run_id,
            ),
        )
