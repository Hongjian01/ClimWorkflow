"""REG-001（部分）、PERM-001、TEST-004：独立 Climate registry 与 schema 契约。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from openharness.climate.registry import create_climate_tool_registry
from openharness.tools import create_default_tool_registry
from openharness.tools.base import BaseTool

RUN_ID = "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314"

_STANDARD_STEPS: list[dict[str, Any]] = [
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

_CORE_TOOL_NAMES = (
    "climate_init_workflow",
    "climate_plan_steps",
    "climate_acquire_data",
    "climate_inspect_dataset",
    "climate_analyze_plot",
    "climate_write_report",
    "climate_read_context",
)
_TODAY_TOOL_NAMES = _CORE_TOOL_NAMES + ("climate_validate_artifacts",)


def _minimal_args(tool_name: str) -> dict[str, Any]:
    if tool_name == "climate_init_workflow":
        return {"objective": "分析示例温度序列并生成报告"}
    if tool_name == "climate_plan_steps":
        return {"steps": _STANDARD_STEPS}
    if tool_name == "climate_acquire_data":
        return {"step_id": "acquire", "mode": "sample"}
    if tool_name == "climate_inspect_dataset":
        return {"step_id": "inspect"}
    if tool_name == "climate_analyze_plot":
        return {
            "step_id": "plot",
            "chart_type": "line",
            "x": "date",
            "y": "temperature_c",
        }
    if tool_name == "climate_write_report":
        return {"step_id": "report", "title": "示例气候报告", "summary": "摘要"}
    if tool_name == "climate_read_context":
        return {}
    if tool_name == "climate_validate_artifacts":
        return {}
    raise AssertionError(f"未知工具: {tool_name}")


def test_climate_registry_names_unique_and_schema_exportable() -> None:
    """默认 8 个工具名称唯一，schema 可被 to_api_schema 导出。"""
    registry = create_climate_tool_registry()
    tools = registry.list_tools()
    names = [tool.name for tool in tools]
    assert names == list(_TODAY_TOOL_NAMES)
    assert len(set(names)) == len(names)

    schemas = registry.to_api_schema()
    assert len(schemas) == 8
    for tool, schema in zip(tools, schemas, strict=True):
        assert isinstance(tool, BaseTool)
        assert schema["name"] == tool.name
        assert schema["description"]
        assert "properties" in schema["input_schema"]
        dumped = tool.input_model.model_json_schema()
        if tool.name == "climate_acquire_data":
            # SCHEMA-001：API schema 注入内层 CdsRequestInput；运行时字段仍是 dict。
            assert (
                dumped["properties"]["cds_request"]
                != schema["input_schema"]["properties"]["cds_request"]
            )
        else:
            assert dumped == schema["input_schema"]
        assert dumped.get("additionalProperties") is False
        assert schema["input_schema"].get("additionalProperties") is False


def test_rejects_extra_fields_and_invalid_uuid_and_mode() -> None:
    """输入多余字段、错误 UUID、非法 mode 在 schema 层被拒绝。"""
    registry = create_climate_tool_registry()

    extra_cases: list[tuple[str, dict[str, Any]]] = [
        ("climate_init_workflow", {**_minimal_args("climate_init_workflow"), "unexpected": 1}),
        ("climate_plan_steps", {**_minimal_args("climate_plan_steps"), "unexpected": True}),
        ("climate_acquire_data", {**_minimal_args("climate_acquire_data"), "unexpected": "x"}),
        ("climate_inspect_dataset", {**_minimal_args("climate_inspect_dataset"), "unexpected": []}),
        ("climate_analyze_plot", {**_minimal_args("climate_analyze_plot"), "unexpected": True}),
        ("climate_write_report", {**_minimal_args("climate_write_report"), "unexpected": 1}),
        ("climate_read_context", {"include_events": False, "unexpected": {}}),
        ("climate_validate_artifacts", {**_minimal_args("climate_validate_artifacts"), "unexpected": 1}),
    ]
    for tool_name, payload in extra_cases:
        tool = registry.get(tool_name)
        assert tool is not None
        with pytest.raises(ValidationError):
            tool.input_model.model_validate(payload)

    init = registry.get("climate_init_workflow")
    assert init is not None
    with pytest.raises(ValidationError):
        init.input_model.model_validate(
            {"objective": "分析示例温度序列并生成报告", "run_id": "not-a-uuid"}
        )
    with pytest.raises(ValidationError):
        init.input_model.model_validate(
            {
                "objective": "分析示例温度序列并生成报告",
                "run_id": "0E8E6EB4-93F2-4CE7-8D22-91A28FA99314",
            }
        )
    with pytest.raises(ValidationError):
        init.input_model.model_validate(
            {
                "objective": "分析示例温度序列并生成报告",
                "run_id": "00000000-0000-0000-0000-000000000000",
            }
        )

    acquire = registry.get("climate_acquire_data")
    assert acquire is not None
    with pytest.raises(ValidationError):
        acquire.input_model.model_validate({"step_id": "acquire", "mode": "network"})
    with pytest.raises(ValidationError):
        acquire.input_model.model_validate(
            {"step_id": "acquire", "mode": "sample", "path": "data.csv"}
        )
    with pytest.raises(ValidationError):
        acquire.input_model.model_validate(
            {
                "step_id": "acquire",
                "mode": "sample",
                "cds_request": {"dataset": "reanalysis-era5-single-levels"},
            }
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
    with pytest.raises(ValidationError):
        acquire.input_model.model_validate(
            {"step_id": "acquire", "mode": "cds", "path": "obs.csv"}
        )
    acquire.input_model.model_validate({"step_id": "acquire", "mode": "local", "path": "obs.csv"})

    read = registry.get("climate_read_context")
    assert read is not None
    with pytest.raises(ValidationError):
        read.input_model.model_validate({"event_limit": 0})
    with pytest.raises(ValidationError):
        read.input_model.model_validate({"event_limit": 1001})
    read.input_model.model_validate({"run_id": RUN_ID, "include_events": True, "event_limit": 1})


def test_independent_registry_does_not_overwrite_same_name() -> None:
    """独立 Climate registry 拒绝同名覆盖；不修改全局 ToolRegistry 语义。"""
    registry = create_climate_tool_registry()
    existing = registry.get("climate_init_workflow")
    assert existing is not None
    with pytest.raises(ValueError, match="climate_init_workflow"):
        registry.register(existing)
    assert registry.get("climate_init_workflow") is existing
    assert [tool.name for tool in registry.list_tools()] == list(_TODAY_TOOL_NAMES)


def test_climate_tool_names_do_not_collide_with_default_registry() -> None:
    """REG-001：接入默认 registry 前先确认 Climate 名称与既有工具无交集。"""
    default_names = {
        tool.name
        for tool in create_default_tool_registry().list_tools()
        if not tool.name.startswith("climate_")
    }
    climate_names = {tool.name for tool in create_climate_tool_registry().list_tools()}
    assert climate_names == set(_TODAY_TOOL_NAMES)
    overlap = default_names & climate_names
    assert overlap == set()


def test_default_registry_has_exact_climate_tools() -> None:
    """REG-001：默认 registry 恰好各注册一个 8 个 Climate 工具，且不静默覆盖。"""
    registry = create_default_tool_registry()
    names = [tool.name for tool in registry.list_tools()]
    assert len(names) == len(set(names))
    climate = [name for name in names if name.startswith("climate_")]
    assert climate == list(_TODAY_TOOL_NAMES)
    for name in _TODAY_TOOL_NAMES:
        tool = registry.get(name)
        assert tool is not None
        schema = tool.to_api_schema()
        assert schema["name"] == name
        assert schema["description"]
        assert schema["input_schema"].get("additionalProperties") is False

    from openharness.tools import _register_climate_tools

    with pytest.raises(ValueError, match="climate_init_workflow"):
        _register_climate_tools(registry)


def test_read_only_classification() -> None:
    """PERM-001：read_context 与 validate 为只读；inspect 因写 Context 仍是 mutation。"""
    registry = create_climate_tool_registry()
    read_only = {"climate_read_context", "climate_validate_artifacts"}
    for tool in registry.list_tools():
        parsed = tool.input_model.model_validate(_minimal_args(tool.name))
        if tool.name in read_only:
            assert tool.is_read_only(parsed) is True
        else:
            assert tool.is_read_only(parsed) is False

    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    plot = registry.get("climate_analyze_plot")
    assert acquire is not None and inspect is not None
    for tool in (acquire, inspect):
        properties = tool.input_model.model_json_schema()["properties"]
        assert "path" in properties
    assert plot is not None
    assert "path" in plot.input_model.model_json_schema()["properties"]


def test_default_registry_includes_validate_and_keeps_core_seven() -> None:
    """VAL-001 / REG-001：默认注册第八工具；include_validate=False 仍可组装核心七工具。"""
    default = create_climate_tool_registry()
    names = [tool.name for tool in default.list_tools()]
    assert names == list(_TODAY_TOOL_NAMES)
    assert names[:7] == list(_CORE_TOOL_NAMES)
    assert names[7] == "climate_validate_artifacts"
    core = create_climate_tool_registry(include_validate=False)
    assert [tool.name for tool in core.list_tools()] == list(_CORE_TOOL_NAMES)
    default_climate = [
        tool.name
        for tool in create_default_tool_registry().list_tools()
        if tool.name.startswith("climate_")
    ]
    assert default_climate == list(_TODAY_TOOL_NAMES)
    assert "climate_validate_artifacts" in default_climate


def test_optional_knowledge_tool_does_not_replace_core_eight() -> None:
    """RAG-003：默认仍八工具；include_knowledge=True 可装第九工具且不覆盖同名。"""
    default = create_climate_tool_registry()
    names = [tool.name for tool in default.list_tools()]
    assert "climate_query_knowledge" not in names
    assert names == list(_TODAY_TOOL_NAMES)
    enabled = create_climate_tool_registry(include_knowledge=True)
    enabled_names = [tool.name for tool in enabled.list_tools()]
    assert enabled_names[:8] == list(_TODAY_TOOL_NAMES)
    assert enabled_names[8] == "climate_query_knowledge"
    parsed = enabled.get("climate_query_knowledge")
    assert parsed is not None
    assert parsed.is_read_only(parsed.input_model.model_validate({"query": "t2m"})) is True
    with pytest.raises(ValueError, match="climate_query_knowledge"):
        enabled.register(parsed)
    default_climate = [
        tool.name
        for tool in create_default_tool_registry().list_tools()
        if tool.name.startswith("climate_")
    ]
    assert "climate_query_knowledge" not in default_climate
    assert default_climate == list(_TODAY_TOOL_NAMES)
