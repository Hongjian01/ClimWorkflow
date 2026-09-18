"""PROMPT-001：论文对齐提示词合同，禁止自由 PLAN 与代码执行。"""

from __future__ import annotations

from pathlib import Path

from openharness.climate.prompts import (
    ALLOWED_ACTIONS,
    COMPACT_SYSTEM_PROMPT,
    PAPER_ROLE_MAP,
    SKILL_CONTRACT_PHRASES,
    TOOL_DESCRIPTIONS,
    build_eval_system_prompt,
)
from openharness.climate.registry import create_climate_tool_registry

ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = ROOT / ".openharness" / "skills" / "climate-ds" / "SKILL.md"


def test_prompt_maps_paper_roles_to_existing_tools_and_four_actions() -> None:
    """Plan/Data/Coding 必须映射到现有工具；动作面仍是四类。"""
    registry = create_climate_tool_registry()
    names = {tool.name for tool in registry.list_tools()}
    for role, tools in PAPER_ROLE_MAP.items():
        assert role
        for tool_name in tools:
            assert tool_name in names
    assert ALLOWED_ACTIONS == (
        "acquire_data",
        "inspect_dataset",
        "analyze_plot",
        "write_report",
    )


def test_prompt_forbids_free_plan_code_execution_and_browser_scraping() -> None:
    """运行时提示词禁止论文式自由拆步、代码执行与门户抓取。"""
    text = COMPACT_SYSTEM_PROMPT.lower()
    assert "ivt" in text and "spi" in text
    assert "第五类" in COMPACT_SYSTEM_PROMPT
    assert "python" in text
    assert "selenium" in text
    assert "沙箱" in COMPACT_SYSTEM_PROMPT
    assert "exec(" not in text
    assert "subprocess" not in text


def test_tool_descriptions_come_from_prompt_module() -> None:
    """工具 schema 描述必须使用 prompts.TOOL_DESCRIPTIONS。"""
    registry = create_climate_tool_registry()
    for tool in registry.list_tools():
        assert tool.description == TOOL_DESCRIPTIONS[tool.name]
        schema = tool.to_api_schema()
        assert schema["description"] == TOOL_DESCRIPTIONS[tool.name]


def test_eval_system_prompt_embeds_skill_and_permission() -> None:
    """real_agent 系统提示由模块组装，不再写死「只能用七个工具」。"""
    prompt = build_eval_system_prompt("skill-body", permission_mode="full_auto")
    assert "skill-body" in prompt
    assert "permission_mode=full_auto" in prompt
    assert "Climate 工具" in prompt
    assert "七个工具" not in prompt
    assert "Plan-Agent" in prompt
    assert "不得摘要" in prompt
    assert "默认未注册" in prompt
    enabled = build_eval_system_prompt(
        "skill-body", permission_mode="full_auto", include_knowledge=True
    )
    assert "已注册只读 climate_query_knowledge" in enabled
    assert "默认未注册" not in enabled
    from evals.climate.real_agent import _system_prompt

    source = _system_prompt.__code__.co_names
    assert "build_eval_system_prompt" in source


def test_skill_contains_prompt_contract_phrases() -> None:
    """Skill 正文必须包含 prompts 模块冻结的合同短语。"""
    content = SKILL_PATH.read_text(encoding="utf-8")
    for phrase in SKILL_CONTRACT_PHRASES:
        assert phrase in content, phrase


def test_cds_request_field_description_has_legal_sample_and_forbidden_fields() -> None:
    """SKILL-004：工具字段说明与 Skill 同样包含合法样例与禁止字段，避免漂移。"""
    from openharness.climate.prompts import FIELD_DESCRIPTIONS

    text = FIELD_DESCRIPTIONS["cds_request"]
    assert "reanalysis-era5-single-levels" in text
    assert "2m_temperature" in text
    assert "allow_sample_fallback" in text
    assert "product_type" in text
    assert "time" in text
    assert "t2m" in text
    assert "禁止" in text or "不要" in text
    assert "climate_analyze_plot" in text or "y=t2m" in text or "y = t2m" in text
