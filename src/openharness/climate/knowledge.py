"""G6 文档检索：切块、BM25、哈希稠密向量、RRF。默认禁网、无模型文件。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openharness.climate.errors import ClimateError, climate_error, redact_secrets
from openharness.climate.paths import WriteZone, knowledge_dir, to_workspace_relative_posix, validate_write_zone
from openharness.utils.fs import atomic_write_text

INDEX_SCHEMA_VERSION = 1
EMBEDDING_DIM = 64
RRF_K = 60
DEFAULT_TOP_K = 5
MAX_TOP_K = 10
CHILD_WINDOW = 180
CHILD_OVERLAP = 60
COSINE_QUALIFY = 0.25
MANIFEST_NAME = "manifest.json"
CHUNKS_NAME = "chunks.jsonl"
SPARSE_NAME = "sparse.json"
DENSE_NAME = "dense.jsonl"

_TOKEN_RE = re.compile(
    r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*|[0-9]+[a-z]+|[0-9]+|[\u4e00-\u9fff]+",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"^[\u4e00-\u9fff]+$")


@dataclass(frozen=True)
class Chunk:
    """父段 + 子块；检索打在 child_text，返回 parent_text。"""

    chunk_id: str
    parent_id: str
    parent_text: str
    child_text: str
    source: str


class DeterministicHashEmbedding:
    """无网、无模型文件的稳定哈希向量；不得宣称语义 Embedding 生产就绪。"""

    dim = EMBEDDING_DIM

    def embed(self, tokens: list[str]) -> list[float]:
        vec = [0.0] * self.dim
        if not tokens:
            return vec
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            for i in range(0, 32, 2):
                idx = digest[i] % self.dim
                sign = 1.0 if digest[i + 1] < 128 else -1.0
                vec[idx] += sign
        return _l2_normalize(vec)


def tokenize(text: str) -> list[str]:
    """ASCII 词 + CJK 整词/单字/双字，便于 `t2m` 与「2 米气温」命中。"""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        tok = match.group(0)
        if _CJK_RE.fullmatch(tok):
            tokens.append(tok)
            tokens.extend(list(tok))
            tokens.extend(tok[i : i + 2] for i in range(len(tok) - 1))
        else:
            tokens.append(tok)
    return tokens


def split_parent_child(markdown: str, source: str) -> list[Chunk]:
    """按 `##` 标题切父段，再切固定窗口子块。"""
    text = markdown.replace("\r\n", "\n").strip()
    if not text:
        return []
    raw_parts = re.split(r"(?m)^(?=## )", text)
    chunks: list[Chunk] = []
    for parent_idx, part in enumerate(raw_parts):
        parent_text = part.strip()
        if not parent_text:
            continue
        parent_id = f"{_source_key(source)}-p{parent_idx}"
        windows = _child_windows(parent_text)
        for child_idx, child_text in enumerate(windows):
            chunks.append(
                Chunk(
                    chunk_id=f"{parent_id}-c{child_idx}",
                    parent_id=parent_id,
                    parent_text=parent_text,
                    child_text=child_text,
                    source=source,
                )
            )
    return chunks


def rebuild_knowledge_index(workspace: Path, source_dir: str) -> dict[str, Any]:
    """从 sandbox 内 source_dir 重建索引；幂等（先清后写）。"""
    src = _resolve_source_dir(workspace, source_dir)
    files = _list_markdown_files(src, workspace)
    chunks: list[Chunk] = []
    for path in files:
        relative = to_workspace_relative_posix(workspace, path)
        body = path.read_text(encoding="utf-8")
        chunks.extend(split_parent_child(body, relative))
    root = knowledge_dir(workspace)
    _clear_index_dir(root)
    root.mkdir(parents=True, exist_ok=True)
    embedder = DeterministicHashEmbedding()
    sparse = _build_sparse(chunks)
    dense_rows = [
        {"chunk_id": chunk.chunk_id, "vector": embedder.embed(tokenize(chunk.child_text))}
        for chunk in chunks
    ]
    manifest = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "embedding": "deterministic_hash",
        "dim": EMBEDDING_DIM,
        "rrf_k": RRF_K,
        "chunk_count": len(chunks),
        "parent_count": len({chunk.parent_id for chunk in chunks}),
        "source_dir": source_dir,
    }
    _write_index_file(workspace, root / MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    chunk_lines = [
        json.dumps(
            {
                "chunk_id": chunk.chunk_id,
                "parent_id": chunk.parent_id,
                "parent_text": chunk.parent_text,
                "child_text": chunk.child_text,
                "source": chunk.source,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for chunk in chunks
    ]
    _write_index_file(workspace, root / CHUNKS_NAME, "\n".join(chunk_lines) + ("\n" if chunk_lines else ""))
    _write_index_file(
        workspace,
        root / SPARSE_NAME,
        json.dumps(sparse, ensure_ascii=False, sort_keys=True) + "\n",
    )
    dense_text = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in dense_rows)
    _write_index_file(workspace, root / DENSE_NAME, dense_text + ("\n" if dense_text else ""))
    return manifest


def bm25_search(
    workspace: Path,
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[tuple[str, float]]:
    """纯稀疏检索；契约断言仍打在 hybrid。"""
    index = _load_index(workspace)
    tokens = tokenize(query)
    scored = _bm25_scores(index, tokens)
    ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)
    return [(chunk_id, score) for chunk_id, score in ranked if score > 0][:top_k]


def dense_search(
    workspace: Path,
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[tuple[str, float]]:
    """纯稠密检索（哈希向量）；契约断言仍打在 hybrid。"""
    index = _load_index(workspace)
    query_vec = DeterministicHashEmbedding().embed(tokenize(query))
    scored = [
        (chunk_id, _cosine(query_vec, vector)) for chunk_id, vector in index["vectors"].items()
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


def hybrid_search(
    workspace: Path,
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """BM25 + 稠密 + RRF；返回合格父段命中，不合格则空列表。"""
    if not isinstance(query, str) or not query.strip():
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "query 不能为空",
            details={"field": "query"},
            workspace=workspace,
        )
    limit = _bound_top_k(top_k)
    index = _load_index(workspace)
    tokens = tokenize(query)
    bm25_scores = _bm25_scores(index, tokens)
    query_vec = DeterministicHashEmbedding().embed(tokens)
    dense_scores = {
        chunk_id: _cosine(query_vec, vector) for chunk_id, vector in index["vectors"].items()
    }
    bm25_rank = _rank_ids(bm25_scores, drop_zero=True)
    dense_rank = _rank_ids(dense_scores, drop_zero=False)
    rrf_scores: dict[str, float] = {}
    for rank_list in (bm25_rank, dense_rank):
        for rank, chunk_id in enumerate(rank_list, start=1):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    fused = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
    hits: list[dict[str, Any]] = []
    seen_parents: set[str] = set()
    for chunk_id, score in fused:
        bm25 = bm25_scores.get(chunk_id, 0.0)
        cosine = dense_scores.get(chunk_id, 0.0)
        if bm25 <= 0.0 and cosine < COSINE_QUALIFY:
            continue
        chunk = index["chunks"][chunk_id]
        if chunk.parent_id in seen_parents:
            continue
        seen_parents.add(chunk.parent_id)
        hits.append(
            {
                "chunk_id": chunk.chunk_id,
                "parent_text": redact_secrets(chunk.parent_text, workspace=workspace),
                "source": chunk.source,
                "score": round(score, 6),
            }
        )
        if len(hits) >= limit:
            break
    return hits


def query_knowledge(
    workspace: Path,
    *,
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """工具层入口：缺索引 / 损坏抛错；无合格命中抛 MISS。"""
    hits = hybrid_search(workspace, query, top_k=top_k)
    if not hits:
        raise climate_error(
            "CLIMATE_KNOWLEDGE_MISS",
            "知识索引无合格命中",
            details={"hit_count": 0, "field": "query"},
            workspace=workspace,
        )
    return hits


def _resolve_source_dir(workspace: Path, source_dir: str) -> Path:
    from openharness.climate.paths import resolve_workspace_path

    try:
        resolved = resolve_workspace_path(workspace, source_dir)
    except ClimateError:
        raise
    if not resolved.is_dir():
        raise climate_error(
            "CLIMATE_INVALID_PATH",
            "source_dir 必须是 workspace 内目录",
            details={"path": source_dir},
            workspace=workspace,
        )
    return resolved


def _list_markdown_files(source_dir: Path, workspace: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(source_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() != ".md":
            continue
        to_workspace_relative_posix(workspace, path)
        files.append(path)
    return files


def _clear_index_dir(root: Path) -> None:
    if not root.exists():
        return
    if root.is_file():
        root.unlink()
        return
    for child in root.iterdir():
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def _write_index_file(workspace: Path, path: Path, text: str) -> None:
    validate_write_zone(workspace, path, WriteZone.KNOWLEDGE)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


def _source_key(source: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-") or "src"


def _child_windows(parent_text: str) -> list[str]:
    if len(parent_text) <= CHILD_WINDOW:
        return [parent_text]
    windows: list[str] = []
    start = 0
    step = max(CHILD_WINDOW - CHILD_OVERLAP, 1)
    while start < len(parent_text):
        windows.append(parent_text[start : start + CHILD_WINDOW])
        if start + CHILD_WINDOW >= len(parent_text):
            break
        start += step
    return windows


def _build_sparse(chunks: list[Chunk]) -> dict[str, Any]:
    postings: dict[str, dict[str, int]] = {}
    doc_lengths: dict[str, int] = {}
    for chunk in chunks:
        tokens = tokenize(chunk.child_text)
        doc_lengths[chunk.chunk_id] = len(tokens)
        tf: dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1
        for tok, count in tf.items():
            bucket = postings.setdefault(tok, {})
            bucket[chunk.chunk_id] = count
    n_docs = max(len(chunks), 1)
    avgdl = (sum(doc_lengths.values()) / n_docs) if chunks else 0.0
    return {"n_docs": len(chunks), "avgdl": avgdl, "doc_lengths": doc_lengths, "postings": postings}


def _load_index(workspace: Path) -> dict[str, Any]:
    root = knowledge_dir(workspace)
    paths = {
        "manifest": root / MANIFEST_NAME,
        "chunks": root / CHUNKS_NAME,
        "sparse": root / SPARSE_NAME,
        "dense": root / DENSE_NAME,
    }
    if not all(path.is_file() for path in paths.values()):
        raise climate_error(
            "CLIMATE_KNOWLEDGE_NOT_FOUND",
            "知识索引不存在",
            details={"path": ".climate/knowledge"},
            workspace=workspace,
        )
    try:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        sparse = json.loads(paths["sparse"].read_text(encoding="utf-8"))
        chunk_rows = _read_jsonl(paths["chunks"])
        dense_rows = _read_jsonl(paths["dense"])
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise climate_error(
            "CLIMATE_KNOWLEDGE_CORRUPT",
            "知识索引无法读取",
            details={"path": ".climate/knowledge", "reason": type(exc).__name__},
            workspace=workspace,
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise climate_error(
            "CLIMATE_KNOWLEDGE_CORRUPT",
            "知识索引清单不一致",
            details={"path": ".climate/knowledge", "reason": "schema"},
            workspace=workspace,
        )
    expected = manifest.get("chunk_count")
    if expected != len(chunk_rows) or expected != len(dense_rows):
        raise climate_error(
            "CLIMATE_KNOWLEDGE_CORRUPT",
            "知识索引 chunk 与向量数量不一致",
            details={"path": ".climate/knowledge", "reason": "count"},
            workspace=workspace,
        )
    chunks: dict[str, Chunk] = {}
    for row in chunk_rows:
        chunk = Chunk(
            chunk_id=str(row["chunk_id"]),
            parent_id=str(row["parent_id"]),
            parent_text=str(row["parent_text"]),
            child_text=str(row["child_text"]),
            source=str(row["source"]),
        )
        chunks[chunk.chunk_id] = chunk
    vectors: dict[str, list[float]] = {}
    for row in dense_rows:
        chunk_id = str(row["chunk_id"])
        vector = row.get("vector")
        if chunk_id not in chunks or not isinstance(vector, list):
            raise climate_error(
                "CLIMATE_KNOWLEDGE_CORRUPT",
                "知识索引向量与 chunk 不一致",
                details={"chunk_id": chunk_id, "reason": "vector"},
                workspace=workspace,
            )
        vectors[chunk_id] = [float(item) for item in vector]
    if set(vectors) != set(chunks):
        raise climate_error(
            "CLIMATE_KNOWLEDGE_CORRUPT",
            "知识索引向量与 chunk 不一致",
            details={"reason": "id_mismatch"},
            workspace=workspace,
        )
    return {"manifest": manifest, "chunks": chunks, "sparse": sparse, "vectors": vectors}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("jsonl 行必须是对象", line, 0)
        rows.append(payload)
    return rows


def _bm25_scores(index: dict[str, Any], tokens: list[str]) -> dict[str, float]:
    sparse = index["sparse"]
    n_docs = int(sparse.get("n_docs") or 0)
    avgdl = float(sparse.get("avgdl") or 0.0)
    doc_lengths = sparse.get("doc_lengths") or {}
    postings = sparse.get("postings") or {}
    scores: dict[str, float] = {chunk_id: 0.0 for chunk_id in index["chunks"]}
    if n_docs <= 0:
        return scores
    k1 = 1.5
    b = 0.75
    query_tf: dict[str, int] = {}
    for tok in tokens:
        query_tf[tok] = query_tf.get(tok, 0) + 1
    for tok, _qtf in query_tf.items():
        df_map = postings.get(tok) or {}
        df = len(df_map)
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
        for chunk_id, tf in df_map.items():
            dl = float(doc_lengths.get(chunk_id) or 0.0)
            denom = tf + k1 * (1.0 - b + b * (dl / avgdl if avgdl else 0.0))
            if denom <= 0:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * (tf * (k1 + 1.0)) / denom
    return scores


def _rank_ids(scores: dict[str, float], *, drop_zero: bool) -> list[str]:
    items = [(chunk_id, score) for chunk_id, score in scores.items() if not drop_zero or score > 0]
    items.sort(key=lambda item: item[1], reverse=True)
    return [chunk_id for chunk_id, _score in items]


def _bound_top_k(top_k: int) -> int:
    if not isinstance(top_k, int):
        return DEFAULT_TOP_K
    return max(1, min(top_k, MAX_TOP_K))


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(item * item for item in vec))
    if norm == 0:
        return vec
    return [item / norm for item in vec]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))
