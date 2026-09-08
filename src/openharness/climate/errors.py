"""Climate 结构化错误与 ToolResult JSON envelope。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 固定错误码 → retryable（SPEC §9）
ERROR_RETRYABLE: dict[str, bool] = {
    "CLIMATE_INVALID_INPUT": False,
    "CLIMATE_INVALID_PATH": False,
    "CLIMATE_RUN_NOT_FOUND": False,
    "CLIMATE_RUN_EXISTS": False,
    "CLIMATE_CONTEXT_CORRUPT": False,
    "CLIMATE_SCHEMA_UNSUPPORTED": False,
    "CLIMATE_MIGRATION_FAILED": False,
    "CLIMATE_LOCK_FAILED": True,
    "CLIMATE_WRITE_FAILED": True,
    "CLIMATE_VERSION_CONFLICT": True,
    "CLIMATE_INVALID_TRANSITION": False,
    "CLIMATE_DEPENDENCY_NOT_READY": False,
    "CLIMATE_IDEMPOTENCY_CONFLICT": False,
    "CLIMATE_INTERRUPTED": True,
    "CLIMATE_RECOVERY_REQUIRED": True,
    "CLIMATE_FORMAT_UNSUPPORTED": False,
    "CLIMATE_DEPENDENCY_MISSING": False,
    "CLIMATE_DATA_INVALID": False,
    "CLIMATE_EXTERNAL_TIMEOUT": True,
    "CLIMATE_EXTERNAL_RATE_LIMIT": True,
    "CLIMATE_EXTERNAL_FAILED": False,
    "CLIMATE_HOOK_BLOCKED": False,
    "CLIMATE_VALIDATION_FAILED": False,
    "CLIMATE_METADATA_REJECTED": False,
    "CLIMATE_KNOWLEDGE_NOT_FOUND": False,
    "CLIMATE_KNOWLEDGE_MISS": True,
    "CLIMATE_KNOWLEDGE_CORRUPT": False,
}

# details 允许的安全诊断键（ERR-002）
_DETAILS_ALLOWLIST: frozenset[str] = frozenset(
    {
        "path",
        "field",
        "status",
        "allowed",
        "reason",
        "step_id",
        "run_id",
        "code",
        "zone",
        "expected_version",
        "actual_version",
        "schema_version",
        "check",
        "candidate_index",
        "candidate_count",
        "winning_candidate",
        "chunk_id",
        "source",
        "hit_count",
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)cds-token-\S+"),
    re.compile(r"(?i)\.cdsapirc\s*[:=]\s*\S+"),
)


@dataclass
class ClimateError(Exception):
    """面向工具输出的结构化 Climate 错误（不含 traceback）。"""

    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in ERROR_RETRYABLE:
            raise ValueError(f"未知 Climate 错误码: {self.code}")
        self.retryable = ERROR_RETRYABLE[self.code]
        Exception.__init__(self, self.message)

    def to_error_object(self) -> dict[str, Any]:
        """返回 envelope 内的 error 对象。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


def climate_error(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
    workspace: Path | None = None,
) -> ClimateError:
    """构造已脱敏的 ClimateError。"""
    if code not in ERROR_RETRYABLE:
        raise ValueError(f"未知 Climate 错误码: {code}")
    safe_details = sanitize_details(dict(details or {}), workspace=workspace)
    safe_message = redact_secrets(message, workspace=workspace)
    return ClimateError(
        code=code,
        message=safe_message,
        retryable=ERROR_RETRYABLE[code],
        details=safe_details,
    )


def success_envelope(
    data: Mapping[str, Any] | None = None,
    *,
    run_id: str | None = None,
    context_version: int | None = None,
) -> dict[str, Any]:
    """成功 ToolResult JSON 对象。"""
    return {
        "ok": True,
        "data": dict(data or {}),
        "run_id": run_id,
        "context_version": context_version,
    }


def failure_envelope(
    error: ClimateError,
    *,
    run_id: str | None = None,
    context_version: int | None = None,
) -> dict[str, Any]:
    """失败 ToolResult JSON 对象。"""
    return {
        "ok": False,
        "error": error.to_error_object(),
        "run_id": run_id,
        "context_version": context_version,
    }


def encode_tool_result_json(payload: Mapping[str, Any]) -> str:
    """确定性序列化：UTF-8、稳定键顺序、紧凑分隔符。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sanitize_details(
    details: Mapping[str, Any],
    *,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """仅保留白名单键，并对字符串值脱敏。"""
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        if key not in _DETAILS_ALLOWLIST:
            continue
        if isinstance(value, str):
            cleaned[key] = redact_secrets(value, workspace=workspace)
        elif isinstance(value, (bool, int, float)) or value is None:
            cleaned[key] = value
        elif isinstance(value, list):
            cleaned[key] = [
                redact_secrets(item, workspace=workspace) if isinstance(item, str) else item
                for item in value
                if isinstance(item, (str, bool, int, float)) or item is None
            ]
        # 嵌套 dict 一律丢弃，避免绝对路径潜入
    return cleaned


def redact_secrets(
    text: str,
    *,
    workspace: Path | None = None,
    catch_all_posix: bool = True,
) -> str:
    """从面向用户的文本中移除绝对路径、主目录与凭证片段。"""
    if not text:
        return text
    result = text

    candidates: list[str] = []
    if workspace is not None:
        try:
            candidates.append(str(workspace.resolve()))
        except OSError:
            candidates.append(str(workspace))
        candidates.append(str(workspace))

    for env_key in ("USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH"):
        value = os.environ.get(env_key)
        if value:
            candidates.append(value)

    try:
        candidates.append(str(Path.home()))
    except (RuntimeError, OSError):
        pass

    # 长串优先替换，避免部分替换残留
    for raw in sorted({c for c in candidates if c}, key=len, reverse=True):
        if raw in result:
            result = result.replace(raw, "<redacted>")
        posix = raw.replace("\\", "/")
        if posix in result:
            result = result.replace(posix, "<redacted>")

    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("<redacted>", result)

    # 兜底：形如盘符绝对路径与 POSIX 绝对路径
    result = re.sub(r"(?i)\b[A-Z]:[\\/][^\s\"']+", "<redacted>", result)
    if catch_all_posix:
        result = re.sub(r"(?<![\w.-])(/[^\s\"']+)", _redact_if_abs_posix, result)
    result = result.replace("Traceback (most recent call last):", "<redacted>")
    return result


def _redact_if_abs_posix(match: re.Match[str]) -> str:
    value = match.group(1)
    if value.startswith(("/", "//")):
        return "<redacted>"
    return value
