"""RAG-001～006 / TEST-008：知识索引、混合检索与只读第九工具。默认禁网。"""

from __future__ import annotations

import ast
import json
import shutil
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from openharness.climate.errors import ERROR_RETRYABLE, ClimateError
from openharness.climate.metadata import validate_cds_request_against_catalog
from openharness.climate.models import ClimateQueryKnowledgeInput
from openharness.climate.prompts import TOOL_DESCRIPTIONS
from openharness.climate.registry import create_climate_tool_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_PATH = ROOT / "src" / "openharness" / "climate" / "knowledge.py"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "climate_knowledge"
RUN_ID = "0e8e6eb4-93f2-4ce7-8d22-91a28fa99314"
OBJECTIVE = "分析示例温度序列并生成报告"
STANDARD_STEPS = [
    {"step_id": "acquire", "action": "acquire_data", "title": "获取数据", "depends_on": []},
    {"step_id": "inspect", "action": "inspect_dataset", "title": "检查数据", "depends_on": ["acquire"]},
    {"step_id": "plot", "action": "analyze_plot", "title": "绘制图表", "depends_on": ["inspect"]},
    {
        "step_id": "report",
        "action": "write_report",
        "title": "撰写报告",
        "depends_on": ["inspect", "plot"],
    },
]


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("knowledge 单元测试禁止网络")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=False)


def _workspace(tmp_path: Path) -> Path:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _copy_fixture(workspace: Path, *, rel: str = "corpus") -> str:
    dest = workspace / rel
    dest.mkdir(parents=True, exist_ok=True)
    for src in FIXTURE_DIR.glob("*.md"):
        shutil.copy2(src, dest / src.name)
    return rel


def _rebuild(workspace: Path, source_dir: str = "corpus"):
    from openharness.climate.knowledge import rebuild_knowledge_index

    _copy_fixture(workspace, rel=source_dir)
    return rebuild_knowledge_index(workspace, source_dir)


async def _invoke(tool: BaseTool, workspace: Path, **kwargs: object) -> tuple[ToolResult, dict]:
    arguments = tool.input_model.model_validate(kwargs)
    result = await tool.execute(arguments, ToolExecutionContext(cwd=workspace))
    payload = json.loads(result.output)
    assert payload["ok"] is (not result.is_error)
    return result, payload


def test_rebuild_rejects_unsafe_source_dir(tmp_path: Path) -> None:
    from openharness.climate.knowledge import rebuild_knowledge_index

    workspace = _workspace(tmp_path)
    for source_dir in ("..", "../outside", "C:/Windows", "~/.secret", "/tmp/knowledge"):
        with pytest.raises(ClimateError) as exc_info:
            rebuild_knowledge_index(workspace, source_dir)
        assert exc_info.value.code == "CLIMATE_INVALID_PATH"
        dumped = json.dumps(exc_info.value.to_error_object(), ensure_ascii=False)
        assert str(workspace) not in dumped


def test_rebuild_is_idempotent_and_writes_knowledge_dir(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    first = _rebuild(workspace)
    root = workspace / ".climate" / "knowledge"
    assert (root / "manifest.json").is_file()
    assert (root / "chunks.jsonl").is_file()
    assert (root / "sparse.json").is_file()
    assert (root / "dense.jsonl").is_file()
    assert first["chunk_count"] >= 2
    second = _rebuild(workspace)
    assert second["chunk_count"] == first["chunk_count"]
    assert second["embedding"] == "deterministic_hash"


@pytest.mark.parametrize(
    "query",
    ["2m temperature", "2 metre temperature", "2 米气温", "t2m"],
)
def test_hybrid_search_recalls_t2m_parent(tmp_path: Path, query: str) -> None:
    from openharness.climate.knowledge import bm25_search, dense_search, hybrid_search

    workspace = _workspace(tmp_path)
    _rebuild(workspace)
    bm25 = bm25_search(workspace, query, top_k=5)
    dense = dense_search(workspace, query, top_k=5)
    assert bm25 or dense
    hits = hybrid_search(workspace, query, top_k=5)
    assert hits
    blob = "\n".join(item["parent_text"] for item in hits)
    assert "t2m" in blob
    for item in hits:
        assert "chunk_id" in item and "source" in item and "score" in item
        assert not Path(item["source"]).is_absolute()
        assert "\\" not in item["source"]
        assert str(workspace) not in item["parent_text"]


def test_hybrid_is_not_always_first_paragraph(tmp_path: Path) -> None:
    from openharness.climate.knowledge import hybrid_search

    workspace = _workspace(tmp_path)
    _rebuild(workspace)
    hits = hybrid_search(workspace, "u10", top_k=5)
    assert hits
    assert "u10" in hits[0]["parent_text"]


def test_knowledge_module_does_not_import_forbidden_stack() -> None:
    from openharness.climate import knowledge as knowledge_mod

    assert callable(knowledge_mod.rebuild_knowledge_index)
    assert callable(knowledge_mod.hybrid_search)
    source = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    for banned in ("selenium", "playwright", "cdsapi", "langchain", "chromadb"):
        assert banned not in imported
    lowered = source.lower()
    assert "bm25" in lowered
    assert "rrf" in lowered
    assert "hybrid_search" in source
    assert "DeterministicHashEmbedding" in source


def test_query_knowledge_input_forbids_code_and_extra_fields() -> None:
    ClimateQueryKnowledgeInput.model_validate({"query": "t2m"})
    ClimateQueryKnowledgeInput.model_validate({"query": "2m temperature", "top_k": 3})
    with pytest.raises(ValidationError):
        ClimateQueryKnowledgeInput.model_validate({"query": "t2m", "code": "print(1)"})
    with pytest.raises(ValidationError):
        ClimateQueryKnowledgeInput.model_validate({"query": "t2m", "shell": "rm -rf /"})
    with pytest.raises(ValidationError):
        ClimateQueryKnowledgeInput.model_validate({"query": "t2m", "expr": "1+1"})
    with pytest.raises(ValidationError):
        ClimateQueryKnowledgeInput.model_validate({"query": "t2m", "path": "/abs"})
    with pytest.raises(ValidationError):
        ClimateQueryKnowledgeInput.model_validate({"query": "t2m", "unexpected": 1})
    with pytest.raises(ValidationError):
        ClimateQueryKnowledgeInput.model_validate({"query": "t2m", "top_k": 0})
    with pytest.raises(ValidationError):
        ClimateQueryKnowledgeInput.model_validate({"query": "t2m", "top_k": 11})
    schema = ClimateQueryKnowledgeInput.model_json_schema()
    assert schema.get("additionalProperties") is False
    assert set(schema["properties"]) == {"query", "top_k"}


@pytest.mark.asyncio
async def test_query_knowledge_tool_is_read_only_and_does_not_create_run(
    tmp_path: Path,
) -> None:
    from openharness.climate.tools import ClimateQueryKnowledgeTool

    workspace = _workspace(tmp_path)
    _rebuild(workspace)
    tool = ClimateQueryKnowledgeTool()
    parsed = tool.input_model.model_validate({"query": "2m temperature"})
    assert tool.is_read_only(parsed) is True
    assert tool.description == TOOL_DESCRIPTIONS["climate_query_knowledge"]
    result, payload = await _invoke(tool, workspace, query="2m temperature")
    assert result.is_error is False
    assert payload["ok"] is True
    assert payload["run_id"] is None
    assert payload["context_version"] is None
    assert payload["data"]["hit_count"] >= 1
    hits = payload["data"]["hits"]
    assert "t2m" in hits[0]["parent_text"]
    assert not (workspace / ".climate" / "index.json").exists()
    dumped = json.dumps(payload, ensure_ascii=False)
    assert str(workspace) not in dumped
    assert "sk-" not in dumped


def test_default_registry_excludes_knowledge_tool() -> None:
    default = create_climate_tool_registry()
    names = [tool.name for tool in default.list_tools()]
    assert "climate_query_knowledge" not in names
    assert len(names) == 8
    enabled = create_climate_tool_registry(include_knowledge=True)
    enabled_names = [tool.name for tool in enabled.list_tools()]
    assert enabled_names.count("climate_query_knowledge") == 1
    assert enabled_names[-1] == "climate_query_knowledge"
    assert len(enabled_names) == 9
    existing = enabled.get("climate_query_knowledge")
    assert existing is not None
    with pytest.raises(ValueError, match="climate_query_knowledge"):
        enabled.register(existing)


def test_retrieval_does_not_bypass_cds_catalog(tmp_path: Path) -> None:
    from openharness.climate.knowledge import hybrid_search

    workspace = _workspace(tmp_path)
    _rebuild(workspace)
    hits = hybrid_search(workspace, "2m temperature", top_k=5)
    assert any("t2m" in item["parent_text"] for item in hits)
    err = validate_cds_request_against_catalog(
        {
            "dataset": "reanalysis-era5-single-levels",
            "variables": ["not_a_real_era5_variable"],
            "area": [40.0, 116.0, 39.0, 116.25],
            "date_start": "2025-01-01",
            "date_end": "2025-01-02",
            "format": "netcdf",
        }
    )
    assert isinstance(err, ClimateError)
    assert err.code == "CLIMATE_METADATA_REJECTED"


@pytest.mark.asyncio
async def test_query_knowledge_does_not_repair_wal_or_read_context(tmp_path: Path) -> None:
    from openharness.climate.tools import ClimateQueryKnowledgeTool

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    assert init and plan
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _rebuild(workspace)
    tx_id = "2a0b1c2d-3e4f-4a5b-8c9d-0e1f2a3b4c5d"
    marker = workspace / ".climate" / "transactions" / f"active-run-{tx_id}.json"
    marker.write_text(
        json.dumps(
            {
                "transaction_id": tx_id,
                "old_active_run_id": None,
                "new_active_run_id": RUN_ID,
                "run_context_written": True,
                "index_written": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    ctx = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    before_ctx = ctx.read_bytes()
    before_marker = marker.read_bytes()
    tool = ClimateQueryKnowledgeTool()
    result, payload = await _invoke(tool, workspace, query="t2m")
    assert result.is_error is False
    assert payload["ok"] is True
    assert payload["run_id"] is None
    assert payload["context_version"] is None
    assert marker.read_bytes() == before_marker
    assert ctx.read_bytes() == before_ctx


def test_missing_index_and_miss_codes(tmp_path: Path) -> None:
    from openharness.climate.knowledge import hybrid_search, query_knowledge

    workspace = _workspace(tmp_path)
    with pytest.raises(ClimateError) as missing:
        query_knowledge(workspace, query="t2m")
    assert missing.value.code == "CLIMATE_KNOWLEDGE_NOT_FOUND"
    assert missing.value.retryable is False
    assert ERROR_RETRYABLE["CLIMATE_KNOWLEDGE_NOT_FOUND"] is False
    _rebuild(workspace)
    with pytest.raises(ClimateError) as miss:
        query_knowledge(workspace, query="zzzznotavariableqqqxyz")
    assert miss.value.code == "CLIMATE_KNOWLEDGE_MISS"
    assert miss.value.retryable is True
    assert ERROR_RETRYABLE["CLIMATE_KNOWLEDGE_MISS"] is True
    assert miss.value.details.get("hit_count") == 0
    empty = hybrid_search(workspace, "zzzznotavariableqqqxyz", top_k=5)
    assert empty == []


def test_corrupt_index_returns_stable_error(tmp_path: Path) -> None:
    from openharness.climate.knowledge import query_knowledge

    workspace = _workspace(tmp_path)
    _rebuild(workspace)
    manifest = workspace / ".climate" / "knowledge" / "manifest.json"
    manifest.write_text(
        '{"schema_version": 1, "chunk_count": 99, "embedding": "deterministic_hash"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ClimateError) as exc_info:
        query_knowledge(workspace, query="t2m")
    assert exc_info.value.code == "CLIMATE_KNOWLEDGE_CORRUPT"
    assert exc_info.value.retryable is False
