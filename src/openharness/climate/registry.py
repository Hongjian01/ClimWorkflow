"""Climate 独立 ToolRegistry；默认注册核心七工具加只读第八工具。"""

from __future__ import annotations

from openharness.climate.tools import (
    ClimateAcquireDataTool,
    ClimateAnalyzePlotTool,
    ClimateInitWorkflowTool,
    ClimateInspectDatasetTool,
    ClimatePlanStepsTool,
    ClimateQueryKnowledgeTool,
    ClimateReadContextTool,
    ClimateValidateArtifactsTool,
    ClimateWriteReportTool,
)
from openharness.tools.base import BaseTool, ToolRegistry


class ClimateToolRegistry(ToolRegistry):
    """拒绝同名覆盖，避免静默替换已注册工具。"""

    def register(self, tool: BaseTool) -> None:
        if self.get(tool.name) is not None:
            raise ValueError(f"工具名称已注册: {tool.name}")
        super().register(tool)


def create_climate_tool_registry(
    *,
    include_validate: bool = True,
    include_knowledge: bool = False,
) -> ClimateToolRegistry:
    """默认返回 8 个 Climate 工具；知识工具仅 include_knowledge=True 时注册。"""
    registry = ClimateToolRegistry()
    for tool in (
        ClimateInitWorkflowTool(),
        ClimatePlanStepsTool(),
        ClimateAcquireDataTool(),
        ClimateInspectDatasetTool(),
        ClimateAnalyzePlotTool(),
        ClimateWriteReportTool(),
        ClimateReadContextTool(),
    ):
        registry.register(tool)
    if include_validate:
        registry.register(ClimateValidateArtifactsTool())
    if include_knowledge:
        registry.register(ClimateQueryKnowledgeTool())
    return registry
