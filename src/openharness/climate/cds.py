"""G4 CDS 下载 adapter：optional cdsapi、有界重试、``.part`` 原子发布。

凭证只由 cdsapi 标准外部配置读取；本模块不接受、不记录、不打印 API key。
下载层永不 fallback；显式 sample fallback 由 pipeline 复用 sample 公共服务编排。
Day 21：retrieve 墙钟超时 + 稳定合法 ``.part`` 发布；不在此修改 QueryEngine。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from openharness.climate.errors import ClimateError, climate_error
from openharness.climate.formats import CDS_DATA_FORMAT, DATASET_VARIABLES, SUPPORTED_DATASETS, SUPPORTED_FORMATS
from openharness.climate.models import CdsRequestInput

log = logging.getLogger(__name__)

MAX_RETRIEVE_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)
# DEC-G4-002：retrieve 墙钟与 .part 稳定窗口；测试可 monkeypatch。
RETRIEVE_TIMEOUT_SECONDS = 180.0
MAX_RETRIEVE_TIMEOUT_SECONDS = 600.0
PART_STABLE_SECONDS = 2.0
PART_POLL_INTERVAL_SECONDS = 0.25
FORMAT_EXTENSION = {"netcdf": ".nc", "grib": ".grib"}
MEDIA_TYPES = {"netcdf": "application/x-netcdf", "grib": "application/x-grib"}
_RETRYABLE_CODES = frozenset({"CLIMATE_EXTERNAL_TIMEOUT", "CLIMATE_EXTERNAL_RATE_LIMIT"})
_STABLE_PERMANENT_KINDS = frozenset({"auth", "invalid_request", "server_permanent", "unclassified"})


class CdsTimeout(Exception):
    """可重试超时；消息不含凭证。"""


class CdsRateLimit(Exception):
    """可重试限流；仅表示 HTTP 429。"""

    def __init__(self, status: int = 429) -> None:
        if status != 429:
            raise ValueError("CdsRateLimit 仅表示 HTTP 429")
        self.status = status
        super().__init__("rate_limit")


class CdsPermanentError(Exception):
    """不可重试：认证、非法请求、服务端永久错误。"""

    def __init__(self, kind: str, status: int | None = None) -> None:
        self.kind = kind if kind in _STABLE_PERMANENT_KINDS else "unclassified"
        self.status = status
        super().__init__(self.kind)


class CdsClientProtocol(Protocol):
    """便于 fake/mock 的检索协议。"""

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
        """将结果写入 target 路径；不得回传凭证。"""


SAMPLE_FALLBACK_ERROR_CODES = frozenset(
    {"CLIMATE_EXTERNAL_TIMEOUT", "CLIMATE_EXTERNAL_RATE_LIMIT"}
)


def allow_sample_fallback(request: CdsRequestInput, error: ClimateError) -> bool:
    """CDS-004：仅显式开关且错误属于冻结集合时才允许 sample fallback。"""
    return request.allow_sample_fallback is True and error.code in SAMPLE_FALLBACK_ERROR_CODES


class CdsApiAdapter:
    """把 cdsapi 异常映射为明确类型，不把原始异常文本带入上层。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
        try:
            self._inner.retrieve(dataset, request, target)
        except ClimateError:
            raise
        except Exception as exc:
            raise _wrap_cdsapi_exception(exc) from None


def cdsapi_available() -> bool:
    """检测 optional cdsapi；测试可 monkeypatch。默认不 import。"""
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        return False
    return True


def build_cds_client() -> CdsClientProtocol:
    """仅在需要真实客户端时 import cdsapi；凭证走其外部配置。"""
    if not cdsapi_available():
        raise climate_error(
            "CLIMATE_DEPENDENCY_MISSING",
            "缺少可选依赖 cdsapi",
            details={"field": "format", "reason": "cdsapi"},
        )
    import cdsapi

    # quiet/progress：避免客户端把 URL 或本机路径打到日志。
    return CdsApiAdapter(cdsapi.Client(quiet=True, progress=False))


def parse_cds_request(data: dict[str, Any] | CdsRequestInput) -> CdsRequestInput:
    """校验 cds_request；错误 details 只含字段名/允许值，不回显原始内容。"""
    if isinstance(data, CdsRequestInput):
        return data
    if not isinstance(data, dict):
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "cds_request 必须是对象",
            details={"field": "cds_request"},
        )
    try:
        return CdsRequestInput.model_validate(data)
    except ValidationError as exc:
        raise _climate_error_from_validation(exc) from None


def build_retrieve_payload(request: CdsRequestInput) -> dict[str, Any]:
    """G4 固定 product_type/download_format；日期展开为官方 year/month/day/time。"""
    years, months, days = _expand_era5_ymd(request.date_start, request.date_end)
    return {
        "product_type": ["reanalysis"],
        "variable": list(request.variables),
        "year": years,
        "month": months,
        "day": days,
        "time": [f"{hour:02d}:00" for hour in range(24)],
        "area": [float(item) for item in request.area],
        "data_format": CDS_DATA_FORMAT[request.format],
        "download_format": "unarchived",
    }


def _expand_era5_ymd(date_start: str, date_end: str) -> tuple[list[str], list[str], list[str]]:
    """把闭区间日期展开为 CDS form 的 year/month/day 列表。"""
    start = date.fromisoformat(date_start)
    end = date.fromisoformat(date_end)
    years: set[str] = set()
    months: set[str] = set()
    days: set[str] = set()
    cursor = start
    while cursor <= end:
        years.add(f"{cursor.year:04d}")
        months.add(f"{cursor.month:02d}")
        days.add(f"{cursor.day:02d}")
        cursor += timedelta(days=1)
    return sorted(years), sorted(months), sorted(days)


def classify_cds_exception(exc: BaseException) -> ClimateError:
    """按明确类型/状态码分类；未知错误视为永久失败，不重试。"""
    if isinstance(exc, ClimateError):
        return exc
    if isinstance(exc, (CdsTimeout, TimeoutError)):
        return climate_error(
            "CLIMATE_EXTERNAL_TIMEOUT",
            "CDS 请求超时",
            details={"reason": "timeout"},
        )
    if isinstance(exc, CdsRateLimit) and exc.status == 429:
        return climate_error(
            "CLIMATE_EXTERNAL_RATE_LIMIT",
            "CDS 请求被限流",
            details={"reason": "rate_limit"},
        )
    if isinstance(exc, CdsPermanentError):
        return climate_error(
            "CLIMATE_EXTERNAL_FAILED",
            "CDS 请求失败",
            details={"reason": exc.kind},
        )
    wrapped = _wrap_cdsapi_exception(exc)
    if wrapped is exc:
        return climate_error(
            "CLIMATE_EXTERNAL_FAILED",
            "CDS 请求失败",
            details={"reason": "unclassified"},
        )
    return classify_cds_exception(wrapped)


def download_cds_dataset(
    request: CdsRequestInput,
    dest_path: Path,
    *,
    client: CdsClientProtocol | None = None,
) -> Path:
    """下载到同目录唯一 ``.part``，校验后 fsync/os.replace；失败清理且不 fallback。"""
    if client is None:
        client = build_cds_client()
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = build_retrieve_payload(request)
    last_error: ClimateError | None = None
    seen_parts: list[Path] = []
    try:
        for attempt in range(1, MAX_RETRIEVE_ATTEMPTS + 1):
            part_path = dest.parent / f".{dest.name}.{uuid.uuid4()}.part"
            seen_parts.append(part_path)
            try:
                log.info(
                    "cds retrieve attempt %s/%s dataset=%s",
                    attempt,
                    MAX_RETRIEVE_ATTEMPTS,
                    request.dataset,
                )
                published = _retrieve_with_timeout_and_stable_publish(
                    client,
                    request.dataset,
                    payload,
                    part_path,
                    dest,
                    request.format,
                )
                return published
            except Exception as exc:
                classified = classify_cds_exception(exc)
                last_error = classified
                log.warning("cds retrieve failed code=%s attempt=%s", classified.code, attempt)
                if classified.code not in _RETRYABLE_CODES or attempt >= MAX_RETRIEVE_ATTEMPTS:
                    raise classified from None
                time.sleep(BACKOFF_SECONDS[attempt - 1])
            finally:
                if not dest.is_file():
                    _cleanup_part(part_path)
    finally:
        for leftover in seen_parts:
            _cleanup_part(leftover)
    assert last_error is not None
    raise last_error


def expand_cds_candidates(request: CdsRequestInput) -> list[tuple[str, CdsRequestInput]]:
    """同一科学意图下展开 ≤3 个已登记合法变体；保持 format，不改 fallback 开关。"""
    from openharness.climate.metadata import MAX_CANDIDATES, expand_area_variants

    variants: list[tuple[str, CdsRequestInput]] = [("identity", request)]
    seen = {tuple(float(item) for item in request.area)}
    for label, area in expand_area_variants(list(request.area)):
        key = tuple(float(item) for item in area)
        if key in seen:
            continue
        seen.add(key)
        variants.append((label, request.model_copy(update={"area": area})))
        if len(variants) >= MAX_CANDIDATES:
            break
    return variants


def download_cds_dataset_with_candidates(
    request: CdsRequestInput,
    dest_path: Path,
    *,
    client: CdsClientProtocol | None = None,
) -> tuple[Path, dict[str, Any]]:
    """顺序尝试目录登记的候选；每候选内遵守 CDS-003；不隐含 sample fallback。"""
    from openharness.climate.metadata import validate_cds_request_against_catalog

    rejected = validate_cds_request_against_catalog(request)
    if rejected is not None:
        raise rejected
    if client is None:
        client = build_cds_client()
    candidates = expand_cds_candidates(request)
    last_error: ClimateError | None = None
    for index, (label, candidate) in enumerate(candidates):
        try:
            published = download_cds_dataset(candidate, dest_path, client=client)
            return published, {
                "candidate_count": len(candidates),
                "candidate_index": index,
                "winning_candidate": label,
            }
        except ClimateError as exc:
            last_error = exc
            continue
    assert last_error is not None
    last_error.details["candidate_count"] = len(candidates)
    last_error.details["candidate_index"] = len(candidates) - 1
    raise last_error


def _resolve_retrieve_timeout() -> float:
    """墙钟超时：测试 monkeypatch 常量优先；生产可用环境变量，上限 600s。"""
    value = float(RETRIEVE_TIMEOUT_SECONDS)
    if abs(value - 180.0) < 1e-9:
        raw = os.environ.get("CLIMATE_CDS_RETRIEVE_TIMEOUT")
        if raw is not None and raw.strip() != "":
            try:
                parsed = float(raw)
            except ValueError:
                parsed = value
            if parsed > 0:
                value = parsed
    if value <= 0:
        value = 180.0
    return min(value, MAX_RETRIEVE_TIMEOUT_SECONDS)


def _retrieve_with_timeout_and_stable_publish(
    client: CdsClientProtocol,
    dataset: str,
    payload: dict[str, Any],
    part_path: Path,
    dest_path: Path,
    claimed_format: str,
) -> Path:
    """在工作线程跑 retrieve；墙钟超时或稳定合法 .part 即结束，不得双发。"""
    timeout_seconds = _resolve_retrieve_timeout()
    stable_seconds = float(PART_STABLE_SECONDS)
    poll_seconds = max(float(PART_POLL_INTERVAL_SECONDS), 0.01)
    retrieve_error: list[BaseException] = []
    retrieve_done = threading.Event()
    publish_lock = threading.Lock()
    published = False

    def _retrieve() -> None:
        try:
            client.retrieve(dataset, payload, str(part_path))
        except BaseException as exc:
            retrieve_error.append(exc)
        finally:
            retrieve_done.set()

    def _try_publish() -> bool:
        nonlocal published
        with publish_lock:
            if published or dest_path.is_file():
                published = True
                return True
            try:
                _validate_part(part_path, dest_path, claimed_format)
                _fsync_replace(part_path, dest_path)
            except ClimateError:
                # 半文件、非法 magic，或 Windows 上文件仍被写入句柄锁住：不发布。
                return False
            published = True
            log.info("cds retrieve published stable part dataset=%s", dataset)
            return True

    worker = threading.Thread(
        target=_retrieve,
        name="climate-cds-retrieve",
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + timeout_seconds
    last_size = -1
    stable_since: float | None = None

    while True:
        now = time.monotonic()
        timed_out = now >= deadline
        if part_path.is_file():
            size = part_path.stat().st_size
            if size != last_size:
                last_size = size
                stable_since = now
            elif (
                size > 0
                and stable_since is not None
                and (now - stable_since) >= stable_seconds
            ):
                if _try_publish():
                    return dest_path
        if published or dest_path.is_file():
            return dest_path
        if retrieve_done.is_set() or timed_out:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        retrieve_done.wait(timeout=min(poll_seconds, remaining))

    if published or dest_path.is_file():
        return dest_path

    if retrieve_done.is_set() and not retrieve_error:
        if _try_publish():
            return dest_path
        _validate_part(part_path, dest_path, claimed_format)
        _fsync_replace(part_path, dest_path)
        return dest_path

    if retrieve_error:
        raise retrieve_error[0]

    if part_path.is_file() and _try_publish():
        return dest_path
    raise CdsTimeout()


def _validate_part(part_path: Path, dest_path: Path, claimed_format: str) -> None:
    from openharness.climate.formats import validate_published_artifact

    validate_published_artifact(part_path, claimed_format, suffix_path=dest_path)


def _fsync_replace(part_path: Path, dest_path: Path) -> None:
    """Windows 上 fsync 需要可写句柄；失败映射为稳定写入错误，不重试 retrieve。"""
    try:
        with part_path.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part_path, dest_path)
    except OSError as exc:
        raise climate_error(
            "CLIMATE_WRITE_FAILED",
            "原子发布数据产物失败",
            details={"reason": type(exc).__name__},
        ) from None


def _cleanup_part(part_path: Path) -> None:
    try:
        if part_path.is_file():
            part_path.unlink()
    except OSError:
        return


def _wrap_cdsapi_exception(exc: BaseException) -> BaseException:
    """窄映射：超时类型与 HTTP 状态；不复制原始异常文本。"""
    if isinstance(exc, (CdsTimeout, CdsRateLimit, CdsPermanentError, ClimateError)):
        return exc
    if isinstance(exc, TimeoutError):
        return CdsTimeout()
    name = type(exc).__name__
    if name in {"Timeout", "ReadTimeout", "ConnectTimeout", "ConnectTimeoutError"}:
        return CdsTimeout()
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status == 429:
        return CdsRateLimit(status=429)
    if status in {401, 403}:
        return CdsPermanentError(kind="auth", status=int(status))
    if status == 400:
        return CdsPermanentError(kind="invalid_request", status=400)
    if isinstance(status, int) and status >= 500:
        return CdsPermanentError(kind="server_permanent", status=status)
    return CdsPermanentError(kind="unclassified")


def _climate_error_from_validation(exc: ValidationError) -> ClimateError:
    errors = exc.errors()
    if not errors:
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "cds_request 校验失败",
            details={"field": "cds_request"},
        )
    err = errors[0]
    loc = err.get("loc") or ()
    field = str(loc[0]) if loc else "cds_request"
    msg = str(err.get("msg", ""))
    token = msg.rsplit(",", 1)[-1].strip()
    err_type = str(err.get("type", ""))
    known = {
        "dataset",
        "variables",
        "area",
        "date_start",
        "date_end",
        "format",
        "allow_sample_fallback",
        "cds_request",
    }
    if err_type == "extra_forbidden" or field not in known:
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "cds_request 含有未知或禁止字段",
            details={"field": field},
        )
    if "date_span" in msg or token == "date_span":
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "日期跨度超过 366 天",
            details={"field": "date_end", "reason": "date_span"},
        )
    if "date_order" in msg or token == "date_order":
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "date_start 不得晚于 date_end",
            details={"field": "date_end"},
        )
    if field == "dataset" or token == "dataset":
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "dataset 不在 G4 allowlist 内",
            details={"field": "dataset", "allowed": sorted(SUPPORTED_DATASETS)},
        )
    if field == "format" or token == "format":
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "format 仅允许 netcdf 或 grib",
            details={"field": "format", "allowed": sorted(SUPPORTED_FORMATS)},
        )
    if field == "variables" or token == "variables":
        allowed = sorted(DATASET_VARIABLES["reanalysis-era5-single-levels"])
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "variables 必须非空且全部位于该 dataset allowlist",
            details={"field": "variables", "allowed": allowed},
        )
    if field == "area" or token == "area" or "north" in msg or "纬度" in msg or "经度" in msg:
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "area 必须是合法的 north/west/south/east",
            details={"field": "area"},
        )
    if field in {"date_start", "date_end"}:
        return climate_error(
            "CLIMATE_INVALID_INPUT",
            "日期必须是 ISO 日期且构成合法闭区间",
            details={"field": field},
        )
    return climate_error(
        "CLIMATE_INVALID_INPUT",
        "cds_request 校验失败",
        details={"field": field},
    )
