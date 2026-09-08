"""RAG-001～006 / TEST-008 / TEST-009 / EVAL-008：知识索引、离线召回、可观测门禁与只读第九工具。默认禁网。"""

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
QUERIES_PATH = ROOT / "evals" / "climate" / "knowledge_queries.yaml"
RECALL_SCRIPT = ROOT / "scripts" / "climate_knowledge_recall.py"
CDS_GRIB = {
    "2m_temperature": "t2m",
    "2m_dewpoint_temperature": "d2m",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
    "mean_sea_level_pressure": "msl",
    "surface_pressure": "sp",
    "total_precipitation": "tp",
    "sea_surface_temperature": "sst",
}
ALLOWED_QUERY_TYPES = frozenset({"exact_short", "en_near", "zh_near", "should_miss"})
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
    assert any("t2m" in item["parent_text"] for item in hits)
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


def _fixture_blob() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(FIXTURE_DIR.glob("*.md")))


def _load_eval_queries() -> list[dict]:
    import yaml

    payload = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    queries = payload.get("queries")
    assert isinstance(queries, list)
    return queries


def test_corpus_covers_catalog_eight_variables() -> None:
    """TEST-009 / CORPUS-001：fixture 与目录 8 变量对齐，含 CDS 长名与 GRIB 短名。"""
    from openharness.climate.formats import DATASET_VARIABLES

    allowed = DATASET_VARIABLES["reanalysis-era5-single-levels"]
    assert allowed == frozenset(CDS_GRIB)
    files = sorted(FIXTURE_DIR.glob("*.md"))
    assert len(files) == 8
    blob = _fixture_blob()
    found_cds: set[str] = set()
    for cds_name, grib_name in CDS_GRIB.items():
        assert cds_name in blob
        assert grib_name in blob
        found_cds.add(cds_name)
        matches = [path for path in files if f"`{cds_name}`" in path.read_text(encoding="utf-8")]
        assert len(matches) == 1, cds_name
        text = matches[0].read_text(encoding="utf-8")
        assert f"`{cds_name}`" in text
        assert f"**{grib_name}**" in text
        assert "source_url:" in text
        assert "retrieved:" in text
        assert "单位" in text
        assert "局限" in text
    assert found_cds == set(allowed)
    names = {path.name.lower() for path in FIXTURE_DIR.iterdir() if path.is_file()}
    assert "skill.md" not in names
    assert not any("day_" in name and name.endswith(".md") for name in names)


def test_eval_queries_do_not_leak_into_corpus() -> None:
    """TEST-009 / EVAL-006：问句整句不得出现在说明书正文。"""
    blob = _fixture_blob()
    queries = _load_eval_queries()
    assert 16 <= len(queries) <= 24
    for item in queries:
        query = item["query"]
        assert query not in blob, item["id"]


def test_knowledge_queries_yaml_schema_and_disclaims_bench85() -> None:
    """EVAL-006：字段、类型、应 miss 条数；声明不是 Bench-85。"""
    import yaml

    from evals.climate.runner import _REAL_OFFLINE_ORDER

    raw = QUERIES_PATH.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw)
    text = raw.lower()
    assert "bench-85" in text or "bench85" in text.replace("-", "")
    assert "企业知识库" in raw
    assert "92%" in raw
    queries = payload["queries"]
    miss_count = 0
    ids: list[str] = []
    for item in queries:
        for key in ("id", "query", "type", "canonical_cds", "canonical_grib", "gold_source", "must_not_grib"):
            assert key in item, key
        assert item["type"] in ALLOWED_QUERY_TYPES
        ids.append(item["id"])
        if item["type"] == "should_miss":
            miss_count += 1
            assert not item["canonical_cds"]
            assert not item["canonical_grib"]
            assert not item["gold_source"]
        else:
            assert item["canonical_cds"] in CDS_GRIB
            assert item["canonical_grib"] == CDS_GRIB[item["canonical_cds"]]
            assert str(item["gold_source"]).endswith(".md")
    assert miss_count >= 3
    assert len(ids) == len(set(ids))
    assert "knowledge_queries" not in _REAL_OFFLINE_ORDER
    smoke_path = ROOT / "evals" / "climate" / "scenarios" / "knowledge_alias_smoke.yaml"
    smoke = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
    assert smoke["expected_tool_sequence"] == ["climate_query_knowledge"] * 3
    assert len(smoke["tool_invocations"]) == 3


@pytest.mark.parametrize(
    ("query_id", "grib"),
    [
        ("q09", "t2m"),
        ("q14", "t2m"),
    ],
)
def test_near_synonym_hybrid_hits_gold(tmp_path: Path, query_id: str, grib: str) -> None:
    """TEST-009：至少 2 条近义问句 hybrid Top-5 命中 gold。"""
    from openharness.climate.knowledge import hybrid_search

    item = next(row for row in _load_eval_queries() if row["id"] == query_id)
    assert item["type"] in {"en_near", "zh_near"}
    workspace = _workspace(tmp_path)
    _rebuild(workspace)
    hits = hybrid_search(workspace, item["query"], top_k=5)
    assert hits
    gold = [
        hit
        for hit in hits
        if item["canonical_grib"] in hit["parent_text"]
        and item["canonical_cds"] in hit["parent_text"]
        and item["gold_source"] in hit["source"]
    ]
    assert gold, (query_id, [hit["source"] for hit in hits])
    assert grib in gold[0]["parent_text"]


def test_should_miss_returns_knowledge_miss(tmp_path: Path) -> None:
    """TEST-009：至少 1 条应 miss → CLIMATE_KNOWLEDGE_MISS。"""
    from openharness.climate.knowledge import hybrid_search, query_knowledge

    item = next(row for row in _load_eval_queries() if row["id"] == "q18")
    assert item["type"] == "should_miss"
    workspace = _workspace(tmp_path)
    _rebuild(workspace)
    with pytest.raises(ClimateError) as exc_info:
        query_knowledge(workspace, query=item["query"])
    assert exc_info.value.code == "CLIMATE_KNOWLEDGE_MISS"
    assert hybrid_search(workspace, item["query"], top_k=5) == []


def _load_recall_mod():
    import importlib.util

    spec = importlib.util.spec_from_file_location("climate_knowledge_recall", RECALL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recall_script_is_offline_and_not_forbidden_stack() -> None:
    """EVAL-007：召回脚本存在、禁网、不引入禁止栈。"""
    source = RECALL_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    for banned in ("selenium", "playwright", "cdsapi", "langchain", "chromadb", "langfuse"):
        assert banned not in imported
    assert "hash_dense" in source
    assert "bm25_search" in source
    assert "hybrid_search" in source
    assert "_forbid_network" in source
    assert "_REAL_OFFLINE_ORDER" not in source
    assert "token_cost" in source
    assert "traces.jsonl" in source


def test_knowledge_observe_gate(tmp_path: Path) -> None:
    """EVAL-008：离线质量门；hybrid Recall 与应 miss 不过线则失败。"""
    module = _load_recall_mod()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    report = module.run_recall(workspace)
    failures = module.check_gates(report)
    assert failures == []
    summary = report["summary"]
    assert summary["hybrid_recall_at_5"] >= 0.85
    assert summary["should_miss_ok"] == summary["n_should_miss"]
    assert summary["near_hybrid_hits"] >= 2
    assert summary["token_cost"] is None
    assert "hash_dense" in summary["note"]


def test_knowledge_observe_writes_traces(tmp_path: Path) -> None:
    """EVAL-008：每条问句有检索轨迹与延迟，无伪造 LLM prompt。"""
    module = _load_recall_mod()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    out_dir = tmp_path / "observe"
    report = module.run_recall(workspace)
    module.write_observe_artifacts(report, out_dir)
    traces = (out_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(traces) == report["summary"]["n"]
    first = json.loads(traces[0])
    assert first["llm_prompt"] is None
    assert first["token_cost"] is None
    assert "hybrid" in first and "bm25" in first
    assert "latency_ms" in first
    dashboard = (out_dir / "dashboard.md").read_text(encoding="utf-8")
    assert "不是 Langfuse" in dashboard
    assert "P95" in dashboard
