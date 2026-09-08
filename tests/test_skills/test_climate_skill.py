"""SKILL-001：climate-ds 必须能被现有项目级 Skill loader 加载。"""

from __future__ import annotations

from pathlib import Path

from openharness.config.settings import Settings
from openharness.skills import load_skill_registry
from openharness.skills._frontmatter import parse_skill_metadata

ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = ROOT / ".openharness" / "skills" / "climate-ds" / "SKILL.md"

REQUIRED_TOOLS = [
    "climate_init_workflow",
    "climate_plan_steps",
    "climate_acquire_data",
    "climate_inspect_dataset",
    "climate_analyze_plot",
    "climate_write_report",
    "climate_read_context",
]


def test_climate_skill_loads_from_project_directory(tmp_path: Path, monkeypatch) -> None:
    """SKILL-001：loader 从 `.openharness/skills/climate-ds/SKILL.md` 发现并加载。"""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert SKILL_PATH.is_file()

    registry = load_skill_registry(ROOT, settings=Settings())
    skill = registry.get("climate-ds")

    assert skill is not None
    assert skill.source == "project"
    assert skill.command_name == "climate-ds"
    assert skill.path
    assert Path(skill.path).resolve() == SKILL_PATH.resolve()


def test_climate_skill_frontmatter_and_guidance() -> None:
    """frontmatter 合法；内容覆盖工具顺序、Context 恢复、凭证与 G0～G3 范围。"""
    content = SKILL_PATH.read_text(encoding="utf-8")
    metadata = parse_skill_metadata("climate-ds", content, fallback_template="Skill: {name}")
    assert metadata["name"] == "climate-ds"
    description = str(metadata["description"])
    assert description.strip()
    assert "climate" in description.lower() or "气候" in description

    lowered = content.lower()
    positions = [lowered.find(name) for name in REQUIRED_TOOLS]
    assert all(pos >= 0 for pos in positions), "Skill 必须列出全部 7 个工具"
    assert positions == sorted(positions), "Skill 必须按依赖顺序说明 7 个工具"

    assert "climate_read_context" in lowered
    assert "权威" in content or "authoritative" in lowered
    assert "compact" in lowered or "压缩" in content
    assert "不得" in content or "must not" in lowered or "do not" in lowered
    assert "猜" in content or "guess" in lowered

    assert "cdsapirc" in lowered or "api key" in lowered or "凭证" in content
    assert "日志" in content or "log" in lowered
    assert "context" in lowered
    assert "输入" in content or "input" in lowered

    assert "cds" in lowered
    assert "g0" in lowered and "g3" in lowered
    assert "不调用" in content or "must not call" in lowered or "do not call" in lowered

    assert "指导" in content or "guidance" in lowered
    assert "业务" in content or "business" in lowered or "implement" in lowered


def test_climate_skill_natural_language_to_four_actions_and_forbids_free_plan() -> None:
    """SKILL-002：自然语言映射到四类动作；禁止新 action 与任意代码执行。"""
    content = SKILL_PATH.read_text(encoding="utf-8")
    lowered = content.lower()
    assert "自然语言" in content
    assert "objective" in lowered or "目标" in content
    assert "acquire_data" in lowered
    assert "inspect_dataset" in lowered
    assert "analyze_plot" in lowered
    assert "write_report" in lowered
    assert "sample" in lowered and "local" in lowered and "cds" in lowered
    assert "histogram" in lowered
    assert "y=t2m" in lowered or "y = t2m" in lowered
    assert "climate_read_context" in lowered
    assert "spi" in lowered
    assert "ivt" in lowered
    assert "tc" in lowered or "热带气旋" in content
    assert "禁止" in content or "不得" in content
    assert "python" in lowered
    assert "不得发明" in content or "不得增加" in content or "禁止" in content
    assert "第五类" in content or "新 action" in lowered or "自由" in content
    assert "exec(" not in lowered
    assert "subprocess" not in lowered
    assert "Plan-Agent" in content
    assert "Data-Agent" in content
    assert "Coding-Agent" in content
    assert "静态 CDS 元数据目录" in content
    assert "最多 3 个候选" in content
