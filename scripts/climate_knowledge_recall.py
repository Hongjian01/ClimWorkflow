"""EVAL-007 / EVAL-008：知识检索召回表 + 离线可观测性。

禁网；不写 Context；不调用 CDS；不读取凭证；不上 Embedding HTTP API。
hash_dense 列为 DeterministicHashEmbedding 对照，不得当作语义 Embedding 结论。
检索评测无 LLM 生成，token 费用为 null。n≈20，非生产准确率。
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "climate_knowledge"
QUERIES_PATH = ROOT / "evals" / "climate" / "knowledge_queries.yaml"
GATES_PATH = ROOT / "evals" / "climate" / "knowledge_gates.yaml"
TOP_K = 5
ALLOWED_TYPES = frozenset({"exact_short", "en_near", "zh_near", "should_miss"})


def _forbid_network() -> None:
    def _blocked(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("climate_knowledge_recall 禁止网络")

    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.socket.connect = _blocked  # type: ignore[method-assign]


def load_queries(path: Path = QUERIES_PATH) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("queries"), list):
        raise ValueError("knowledge_queries.yaml 必须含 queries 列表")
    return payload


def load_gates(path: Path = GATES_PATH) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("knowledge_gates.yaml 必须是对象")
    return payload


def _copy_fixture(workspace: Path, rel: str = "corpus") -> str:
    dest = workspace / rel
    dest.mkdir(parents=True, exist_ok=True)
    for src in sorted(FIXTURE_DIR.glob("*.md")):
        shutil.copy2(src, dest / src.name)
    return rel


def _load_chunks(workspace: Path) -> dict[str, dict[str, Any]]:
    path = workspace / ".climate" / "knowledge" / "chunks.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[str(row["chunk_id"])] = row
    return rows


def parent_dedup(
    ranked: list[tuple[str, float]],
    chunks: dict[str, dict[str, Any]],
    *,
    top_k: int = TOP_K,
) -> list[dict[str, Any]]:
    """与 hybrid_search 相同：按 parent_id 去重后再取 Top-k。"""
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk_id, score in ranked:
        row = chunks[chunk_id]
        parent_id = str(row["parent_id"])
        if parent_id in seen:
            continue
        seen.add(parent_id)
        hits.append(
            {
                "chunk_id": str(row["chunk_id"]),
                "parent_id": parent_id,
                "parent_text": str(row["parent_text"]),
                "source": str(row["source"]),
                "score": score,
            }
        )
        if len(hits) >= top_k:
            break
    return hits


def hit_is_gold(hit: dict[str, Any], item: dict[str, Any]) -> bool:
    text = hit["parent_text"]
    source = hit["source"]
    cds = str(item.get("canonical_cds") or "")
    grib = str(item.get("canonical_grib") or "")
    gold_src = str(item.get("gold_source") or "")
    name_ok = bool((grib and grib in text) or (cds and cds in text))
    source_ok = (not gold_src) or gold_src in source or Path(source).name == gold_src
    return name_ok and source_ok


def gold_rank(hits: list[dict[str, Any]], item: dict[str, Any]) -> int | None:
    for index, hit in enumerate(hits, start=1):
        if hit_is_gold(hit, item):
            return index
    return None


def must_not_before_gold(hits: list[dict[str, Any]], item: dict[str, Any]) -> bool:
    needle = str(item.get("must_not_grib") or "")
    if not needle:
        return False
    gold = gold_rank(hits, item)
    for index, hit in enumerate(hits, start=1):
        if needle in hit["parent_text"] and (gold is None or index < gold):
            return True
    return False


def _compact_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits[:TOP_K], start=1):
        compact.append(
            {
                "rank": rank,
                "chunk_id": hit.get("chunk_id"),
                "source": hit.get("source"),
                "score": hit.get("score"),
            }
        )
    return compact


def evaluate_item(
    workspace: Path,
    item: dict[str, Any],
    chunks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from openharness.climate.errors import ClimateError
    from openharness.climate.knowledge import bm25_search, dense_search, hybrid_search, query_knowledge

    query = str(item["query"])
    started = time.perf_counter()
    bm25_ranked = bm25_search(workspace, query, top_k=max(len(chunks), TOP_K))
    dense_ranked = dense_search(workspace, query, top_k=max(len(chunks), TOP_K))
    bm25_hits = parent_dedup(bm25_ranked, chunks)
    hash_dense_hits = parent_dedup(dense_ranked, chunks)
    hybrid_hits = hybrid_search(workspace, query, top_k=TOP_K)
    latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
    qtype = str(item["type"])
    trace = {
        "id": item["id"],
        "query": query,
        "type": qtype,
        "latency_ms": latency_ms,
        "bm25": _compact_hits(bm25_hits),
        "hash_dense": _compact_hits(hash_dense_hits),
        "hybrid": _compact_hits(hybrid_hits),
        "embedding": "deterministic_hash",
        "llm_prompt": None,
        "llm_response": None,
        "token_cost": None,
        "note": "检索评测无生成步；无 token 费用。",
    }
    if qtype == "should_miss":
        miss_ok = False
        miss_code = ""
        try:
            query_knowledge(workspace, query=query, top_k=TOP_K)
        except ClimateError as exc:
            miss_ok = exc.code == "CLIMATE_KNOWLEDGE_MISS"
            miss_code = exc.code
        hybrid_empty = hybrid_hits == []
        return {
            "id": item["id"],
            "type": qtype,
            "query": query,
            "bm25_hit": False,
            "hybrid_hit": False,
            "hash_dense_hit": False,
            "bm25_rank": None,
            "hybrid_rank": None,
            "hash_dense_rank": None,
            "misorder": False,
            "miss_ok": miss_ok and hybrid_empty,
            "miss_code": miss_code,
            "hybrid_empty": hybrid_empty,
            "latency_ms": latency_ms,
            "trace": trace,
        }
    return {
        "id": item["id"],
        "type": qtype,
        "query": query,
        "bm25_hit": gold_rank(bm25_hits, item) is not None,
        "hybrid_hit": gold_rank(hybrid_hits, item) is not None,
        "hash_dense_hit": gold_rank(hash_dense_hits, item) is not None,
        "bm25_rank": gold_rank(bm25_hits, item),
        "hybrid_rank": gold_rank(hybrid_hits, item),
        "hash_dense_rank": gold_rank(hash_dense_hits, item),
        "misorder": must_not_before_gold(hybrid_hits, item),
        "miss_ok": False,
        "miss_code": "",
        "hybrid_empty": hybrid_hits == [],
        "latency_ms": latency_ms,
        "trace": trace,
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(math.floor(rank))
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def run_recall(workspace: Path) -> dict[str, Any]:
    from openharness.climate.knowledge import rebuild_knowledge_index

    source_dir = _copy_fixture(workspace)
    rebuild_knowledge_index(workspace, source_dir)
    chunks = _load_chunks(workspace)
    payload = load_queries()
    rows = [evaluate_item(workspace, item, chunks) for item in payload["queries"]]
    scored = [row for row in rows if row["type"] != "should_miss"]
    misses = [row for row in rows if row["type"] == "should_miss"]
    near = [row for row in scored if row["type"] in {"en_near", "zh_near"}]
    n_scored = max(len(scored), 1)
    latencies = [float(row["latency_ms"]) for row in rows]
    leaked = sum(1 for row in misses if not row["miss_ok"])
    summary = {
        "n": len(rows),
        "n_scored": len(scored),
        "n_should_miss": len(misses),
        "bm25_recall_at_5": sum(1 for row in scored if row["bm25_hit"]) / n_scored,
        "hybrid_recall_at_5": sum(1 for row in scored if row["hybrid_hit"]) / n_scored,
        "hash_dense_recall_at_5": sum(1 for row in scored if row["hash_dense_hit"]) / n_scored,
        "hybrid_misorder": sum(1 for row in scored if row["misorder"]),
        "should_miss_ok": sum(1 for row in misses if row["miss_ok"]),
        "near_hybrid_hits": sum(1 for row in near if row["hybrid_hit"]),
        "citation_coverage": sum(1 for row in scored if row["hybrid_hit"]) / n_scored,
        "unsupported_answer_rate": (leaked / len(misses)) if misses else 0.0,
        "latency_p50_ms": round(_percentile(latencies, 50), 3),
        "latency_p95_ms": round(_percentile(latencies, 95), 3),
        "token_cost": None,
        "note": (
            "n≈20，非生产准确率；hash_dense 为哈希对照，不是语义 Embedding；"
            "无 LLM 生成故 token_cost=null；citation_coverage=hybrid Recall@5；"
            "unsupported_answer_rate=应 miss 却未 MISS 的比例。"
        ),
    }
    return {"disclaimer": payload.get("disclaimer", ""), "summary": summary, "rows": rows}


def check_gates(report: dict[str, Any], gates: dict[str, Any] | None = None) -> list[str]:
    """返回未过门的原因；空列表表示通过。"""
    gates = gates or load_gates()
    summary = report["summary"]
    failures: list[str] = []
    min_recall = float(gates["hybrid_recall_at_5_min"])
    if float(summary["hybrid_recall_at_5"]) < min_recall:
        failures.append(
            f"hybrid_recall_at_5={summary['hybrid_recall_at_5']:.2f} < {min_recall}"
        )
    if gates.get("should_miss_all") and int(summary["should_miss_ok"]) < int(
        summary["n_should_miss"]
    ):
        failures.append(
            f"should_miss_ok={summary['should_miss_ok']} < {summary['n_should_miss']}"
        )
    min_near = int(gates["near_hybrid_hits_min"])
    if int(summary["near_hybrid_hits"]) < min_near:
        failures.append(f"near_hybrid_hits={summary['near_hybrid_hits']} < {min_near}")
    return failures


def write_observe_artifacts(report: dict[str, Any], out_dir: Path) -> None:
    """第一阶段 JSONL + 第二阶段摘要，供本地看板；目录默认 gitignore。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    traces_path = out_dir / "traces.jsonl"
    with traces_path.open("w", encoding="utf-8") as handle:
        for row in report["rows"]:
            handle.write(json.dumps(row["trace"], ensure_ascii=False) + "\n")
    serializable_rows = [{k: v for k, v in row.items() if k != "trace"} for row in report["rows"]]
    summary_payload = {
        "disclaimer": report["disclaimer"],
        "summary": report["summary"],
        "rows": serializable_rows,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    lines = [
        "# 知识检索离线观测",
        "",
        "不是 Langfuse 云看板；不是 Bench-85。hash_dense ≠ 语义 Embedding。",
        "",
        f"- n={summary['n']} scored={summary['n_scored']} should_miss={summary['n_should_miss']}",
        f"- BM25 Recall@5={summary['bm25_recall_at_5']:.2f}",
        f"- hybrid Recall@5={summary['hybrid_recall_at_5']:.2f}（citation_coverage 同此）",
        f"- hash_dense Recall@5={summary['hash_dense_recall_at_5']:.2f}（哈希对照）",
        f"- 误召 misorder={summary['hybrid_misorder']}",
        f"- 无依据泄漏率={summary['unsupported_answer_rate']:.2f}",
        f"- 延迟 P50={summary['latency_p50_ms']}ms P95={summary['latency_p95_ms']}ms",
        f"- token_cost={summary['token_cost']}",
        "",
        summary["note"],
        "",
    ]
    (out_dir / "dashboard.md").write_text("\n".join(lines), encoding="utf-8")


def _fmt_hit(flag: bool, rank: int | None) -> str:
    if flag and rank is not None:
        return f"Y@{rank}"
    return "N"


def print_table(report: dict[str, Any]) -> None:
    print("EVAL-007 BM25 vs hybrid vs hash_dense（父段去重 Top-5）")
    print("声明：不是 Bench-85 / 不是企业知识库准确率。hash_dense ≠ 语义 Embedding。")
    header = (
        f"{'id':<5} {'type':<12} {'bm25':<6} {'hybrid':<8} {'hash_dense':<10} "
        f"{'misorder':<8} {'miss_ok':<8}"
    )
    print(header)
    print("-" * len(header))
    for row in report["rows"]:
        miss = "Y" if row["miss_ok"] else ("-" if row["type"] != "should_miss" else "N")
        print(
            f"{row['id']:<5} {row['type']:<12} "
            f"{_fmt_hit(row['bm25_hit'], row['bm25_rank']):<6} "
            f"{_fmt_hit(row['hybrid_hit'], row['hybrid_rank']):<8} "
            f"{_fmt_hit(row['hash_dense_hit'], row['hash_dense_rank']):<10} "
            f"{('Y' if row['misorder'] else 'N'):<8} {miss:<8}"
        )
    summary = report["summary"]
    print("-" * len(header))
    print(
        "摘要 n={n} scored={n_scored} miss={n_should_miss} "
        "BM25@5={bm25_recall_at_5:.2f} hybrid@5={hybrid_recall_at_5:.2f} "
        "hash_dense@5={hash_dense_recall_at_5:.2f}（哈希对照） "
        "misorder={hybrid_misorder} should_miss_ok={should_miss_ok} "
        "P50={latency_p50_ms}ms P95={latency_p95_ms}ms".format(**summary)
    )
    print(summary["note"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Climate 知识召回对照表与离线观测（禁网）")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="写入 traces.jsonl / summary.json / dashboard.md",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="按 knowledge_gates.yaml 失败则退出码 1",
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    _forbid_network()
    with tempfile.TemporaryDirectory(prefix="climate-knowledge-recall-") as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        report = run_recall(workspace)
    print_table(report)
    if args.out_dir is not None:
        write_observe_artifacts(report, args.out_dir)
        print(f"已写入 {args.out_dir}")
    if args.gate:
        failures = check_gates(report)
        if failures:
            print("门禁失败：" + "; ".join(failures))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
