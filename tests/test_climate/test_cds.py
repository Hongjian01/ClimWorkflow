"""Day 12 / CDS-001～003、SEC-002、TEST-006：CdsRequestInput、mock 下载与凭证脱敏。

Day 21 / TEST-011：假 retrieve 挂起的墙钟超时与稳定 ``.part`` 发布。
Day 22 / TEST-010：acquire API schema 前置；非法载荷仍 ``CLIMATE_INVALID_INPUT``。
默认用例使用 fake client、禁网；不读取、不打印、不写入 .cdsapirc。
真实 CDS 仅 ``climate_integration`` 且 CLIMATE_INTEGRATION=1、CDSAPI_KEY 存在时运行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openharness.climate.errors import ClimateError, encode_tool_result_json, failure_envelope
from openharness.climate.models import CdsRequestInput, dumps_climate_json, loads_run_context
from openharness.climate.registry import create_climate_tool_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_SECRET = "cds-token-LEAK-should-never-appear"
FAKE_API_KEY = "sk-not-a-real-key-DAY12"

RUN_ID = "2a7c1d8e-4b5f-4a21-9c0d-7e6f5a4b3c21"
OBJECTIVE = "用 ERA5 单层再分析做离线 mock 下载"

STANDARD_STEPS: list[dict[str, Any]] = [
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

_FORBIDDEN_CREDENTIAL_FIELDS = (
    "api_key",
    "token",
    "key",
    "password",
    "secret",
    "username",
    "authorization",
    "cdsapirc",
    "cdsapi_key",
    "CDSAPI_KEY",
    "url",
)


def _valid_request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset": "reanalysis-era5-single-levels",
        "variables": ["2m_temperature"],
        "area": [40.0, 116.0, 39.0, 116.25],
        "date_start": "2025-01-01",
        "date_end": "2025-01-02",
        "format": "netcdf",
    }
    payload.update(overrides)
    return payload


def _workspace(tmp_path: Path) -> Path:
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


async def _invoke(tool: BaseTool, workspace: Path, **kwargs: Any) -> tuple[ToolResult, dict[str, Any]]:
    arguments = tool.input_model.model_validate(kwargs)
    result = await tool.execute(arguments, ToolExecutionContext(cwd=workspace))
    payload = json.loads(result.output)
    assert payload["ok"] is (not result.is_error)
    return result, payload


def _scan_for_secrets(*blobs: Any) -> None:
    """任何序列化表面都不得出现假凭证或 .cdsapirc 内容。"""
    text = json.dumps(blobs, default=str, ensure_ascii=False)
    lowered = text.lower()
    assert FAKE_SECRET not in text
    assert FAKE_API_KEY not in text
    assert FAKE_SECRET.lower() not in lowered
    assert "should-never-appear" not in lowered


@pytest.fixture(autouse=True)
def _forbid_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 CDS 单元测试禁网；真实 climate_integration 解除 socket 拦截。"""
    if request.node.get_closest_marker("climate_integration") is not None:
        return

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("CDS 单元测试禁止网络")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked, raising=False)


# --- CDS-001：CdsRequestInput ---


def test_cds_request_allowlist_dataset_and_variables() -> None:
    from openharness.climate.cds import parse_cds_request

    parsed = parse_cds_request(_valid_request())
    assert parsed.dataset == "reanalysis-era5-single-levels"
    assert parsed.variables == ["2m_temperature"]

    with pytest.raises(ClimateError) as ds_err:
        parse_cds_request(_valid_request(dataset="reanalysis-era5-pressure-levels"))
    assert ds_err.value.code == "CLIMATE_INVALID_INPUT"
    assert ds_err.value.details["field"] == "dataset"
    assert "reanalysis-era5-single-levels" in ds_err.value.details["allowed"]
    assert "pressure-levels" not in json.dumps(ds_err.value.details)

    with pytest.raises(ClimateError) as var_err:
        parse_cds_request(_valid_request(variables=["mean_wave_direction"]))
    assert var_err.value.code == "CLIMATE_INVALID_INPUT"
    assert var_err.value.details["field"] == "variables"
    assert "mean_wave_direction" not in json.dumps(var_err.value.to_error_object())


def test_cds_request_variables_nonempty_deduped_canonical_order() -> None:
    from openharness.climate.cds import parse_cds_request

    with pytest.raises(ClimateError) as empty_err:
        parse_cds_request(_valid_request(variables=[]))
    assert empty_err.value.code == "CLIMATE_INVALID_INPUT"
    assert empty_err.value.details["field"] == "variables"

    parsed = parse_cds_request(
        _valid_request(
            variables=[
                "total_precipitation",
                "2m_temperature",
                "2m_temperature",
                "total_precipitation",
            ]
        )
    )
    assert parsed.variables == ["2m_temperature", "total_precipitation"]
    assert len(parsed.variables) == len(set(parsed.variables))


def test_cds_request_area_bounds_and_north_gt_south() -> None:
    from openharness.climate.cds import parse_cds_request

    ok = parse_cds_request(_valid_request(area=[90.0, -180.0, -90.0, 180.0]))
    assert list(ok.area) == [90.0, -180.0, -90.0, 180.0]

    with pytest.raises(ClimateError) as order_err:
        parse_cds_request(_valid_request(area=[10.0, 0.0, 10.0, 1.0]))
    assert order_err.value.code == "CLIMATE_INVALID_INPUT"
    assert order_err.value.details["field"] == "area"

    with pytest.raises(ClimateError) as south_gt:
        parse_cds_request(_valid_request(area=[-10.0, 0.0, 10.0, 1.0]))
    assert south_gt.value.details["field"] == "area"

    with pytest.raises(ClimateError) as lat:
        parse_cds_request(_valid_request(area=[91.0, 0.0, 0.0, 1.0]))
    assert lat.value.details["field"] == "area"

    with pytest.raises(ClimateError) as lon:
        parse_cds_request(_valid_request(area=[10.0, -181.0, 0.0, 1.0]))
    assert lon.value.details["field"] == "area"

    with pytest.raises(ClimateError) as length:
        parse_cds_request(_valid_request(area=[10.0, 0.0, 0.0]))
    assert length.value.details["field"] == "area"


def test_cds_request_iso_dates_order_and_max_span() -> None:
    from openharness.climate.cds import parse_cds_request

    parse_cds_request(_valid_request(date_start="2024-01-01", date_end="2024-12-31"))

    with pytest.raises(ClimateError) as fmt_err:
        parse_cds_request(_valid_request(date_start="2025/01/01"))
    assert fmt_err.value.code == "CLIMATE_INVALID_INPUT"
    assert fmt_err.value.details["field"] == "date_start"

    with pytest.raises(ClimateError) as time_err:
        parse_cds_request(_valid_request(date_start="2025-01-01T00:00:00Z"))
    assert time_err.value.details["field"] == "date_start"

    with pytest.raises(ClimateError) as order_err:
        parse_cds_request(_valid_request(date_start="2025-02-01", date_end="2025-01-01"))
    assert order_err.value.details["field"] in {"date_start", "date_end"}

    with pytest.raises(ClimateError) as span_err:
        parse_cds_request(_valid_request(date_start="2024-01-01", date_end="2025-01-01"))
    assert span_err.value.code == "CLIMATE_INVALID_INPUT"
    dumped = json.dumps(span_err.value.to_error_object())
    assert "2024-01-01" not in dumped or span_err.value.details.get("reason") == "date_span"


def test_cds_request_format_allowlist() -> None:
    from openharness.climate.cds import parse_cds_request

    assert parse_cds_request(_valid_request(format="grib")).format == "grib"
    with pytest.raises(ClimateError) as zip_err:
        parse_cds_request(_valid_request(format="zip"))
    assert zip_err.value.code == "CLIMATE_INVALID_INPUT"
    assert zip_err.value.details["field"] == "format"
    assert "netcdf" in zip_err.value.details["allowed"]
    assert "grib" in zip_err.value.details["allowed"]
    assert "zip" not in json.dumps(zip_err.value.details)


def test_cds_request_rejects_unknown_and_credential_and_mode_fields() -> None:
    from openharness.climate.cds import parse_cds_request

    with pytest.raises(ClimateError) as unknown:
        parse_cds_request(_valid_request(product_type="ensemble"))
    assert unknown.value.code == "CLIMATE_INVALID_INPUT"
    assert unknown.value.details["field"] == "product_type"
    assert "ensemble" not in json.dumps(unknown.value.to_error_object())

    with pytest.raises(ClimateError) as mode_err:
        parse_cds_request(_valid_request(mode="sample"))
    assert mode_err.value.details["field"] == "mode"

    for field in _FORBIDDEN_CREDENTIAL_FIELDS:
        with pytest.raises(ClimateError) as cred_err:
            parse_cds_request(_valid_request(**{field: FAKE_SECRET}))
        err = cred_err.value
        assert err.code == "CLIMATE_INVALID_INPUT"
        assert err.details["field"] == field
        blob = json.dumps(err.to_error_object())
        assert FAKE_SECRET not in blob
        assert FAKE_SECRET not in err.message

    with pytest.raises(ValidationError):
        CdsRequestInput.model_validate(_valid_request(api_key=FAKE_API_KEY))


def test_cds_request_serialization_contains_no_secrets() -> None:
    from openharness.climate.cds import parse_cds_request

    parsed = parse_cds_request(_valid_request(allow_sample_fallback=False))
    dumped = parsed.model_dump(mode="json")
    text = json.dumps(dumped, ensure_ascii=False)
    for field in _FORBIDDEN_CREDENTIAL_FIELDS:
        assert field not in dumped
    assert "api_key" not in text
    assert "token" not in text
    assert FAKE_SECRET not in text
    assert dumped["allow_sample_fallback"] is False
    # dumps_climate_json 走 pydantic dump，同样不得含凭证键
    encoded = json.dumps(parsed.model_dump(mode="json"), sort_keys=True)
    assert "cdsapirc" not in encoded.lower()


# --- CDS-001/003：fake client 状态机 ---


class FakeCdsClient:
    """协议兼容的假客户端：按预定错误序列失败，成功时写入源文件字节。"""

    def __init__(
        self,
        *,
        source: Path | None = None,
        errors: list[BaseException] | None = None,
        payload: bytes | None = None,
    ) -> None:
        self.source = source
        self.payload = payload
        self.errors = list(errors or [])
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
        self.calls.append((dataset, dict(request), target))
        if self.errors:
            raise self.errors.pop(0)
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.payload is not None:
            path.write_bytes(self.payload)
        elif self.source is not None:
            path.write_bytes(self.source.read_bytes())
        else:
            path.write_bytes(b"")


def test_cdsapi_missing_is_dependency_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod, "cdsapi_available", lambda: False)
    dest = tmp_path / "era5.nc"
    with pytest.raises(ClimateError) as exc_info:
        cds_mod.download_cds_dataset(CdsRequestInput.model_validate(_valid_request()), dest)
    err = exc_info.value
    assert err.code == "CLIMATE_DEPENDENCY_MISSING"
    assert err.retryable is False
    assert not dest.exists()
    assert list(tmp_path.glob("*.part")) == []
    assert FAKE_SECRET not in err.message


def test_download_success_uses_part_then_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    client = FakeCdsClient(source=FIXTURES / "minimal_t2m.nc")
    dest = tmp_path / "era5.nc"
    replaced: list[tuple[str, str]] = []
    real_replace = cds_mod.os.replace

    def _spy_replace(src: str | Path, dst: str | Path) -> None:
        replaced.append((str(src), str(dst)))
        assert str(src).endswith(".part")
        assert Path(src).is_file()
        assert Path(src).stat().st_size > 0
        real_replace(src, dst)

    monkeypatch.setattr(cds_mod.os, "replace", _spy_replace)
    published = cds_mod.download_cds_dataset(
        CdsRequestInput.model_validate(_valid_request()),
        dest,
        client=client,
    )
    assert published == dest
    assert dest.is_file()
    assert dest.stat().st_size > 0
    assert dest.read_bytes()[:8] == (FIXTURES / "minimal_t2m.nc").read_bytes()[:8]
    assert replaced == [(replaced[0][0], str(dest))]
    assert list(tmp_path.glob("**/*.part")) == []
    assert client.calls
    dataset, request, target = client.calls[0]
    assert dataset == "reanalysis-era5-single-levels"
    assert request["product_type"] == ["reanalysis"]
    assert request["variable"] == ["2m_temperature"]
    assert request["year"] == ["2025"]
    assert request["month"] == ["01"]
    assert request["day"] == ["01", "02"]
    assert request["time"][0] == "00:00"
    assert request["time"][-1] == "23:00"
    assert request["data_format"] == "netcdf"
    assert request["download_format"] == "unarchived"
    assert "date" not in request
    assert "ensemble" not in json.dumps(request)
    assert target.endswith(".part")


def test_download_rejects_empty_and_magic_mismatch_and_cleans_part(tmp_path: Path) -> None:
    from openharness.climate import cds as cds_mod

    dest = tmp_path / "era5.nc"
    empty_client = FakeCdsClient(payload=b"")
    with pytest.raises(ClimateError) as empty_err:
        cds_mod.download_cds_dataset(
            CdsRequestInput.model_validate(_valid_request()),
            dest,
            client=empty_client,
        )
    assert empty_err.value.code == "CLIMATE_DATA_INVALID"
    assert not dest.exists()
    assert list(tmp_path.glob("**/*.part")) == []

    grib_as_nc = FakeCdsClient(source=FIXTURES / "minimal.grib")
    with pytest.raises(ClimateError) as magic_err:
        cds_mod.download_cds_dataset(
            CdsRequestInput.model_validate(_valid_request(format="netcdf")),
            dest,
            client=grib_as_nc,
        )
    assert magic_err.value.code == "CLIMATE_DATA_INVALID"
    assert not dest.exists()
    assert list(tmp_path.glob("**/*.part")) == []

    random_client = FakeCdsClient(source=FIXTURES / "random_bytes.nc")
    with pytest.raises(ClimateError) as random_err:
        cds_mod.download_cds_dataset(
            CdsRequestInput.model_validate(_valid_request()),
            dest,
            client=random_client,
        )
    assert random_err.value.code == "CLIMATE_DATA_INVALID"
    assert not dest.exists()
    assert list(tmp_path.glob("**/*.part")) == []


def test_retry_timeout_and_rate_limit_max_three_with_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    sleeps: list[float] = []
    monkeypatch.setattr(cds_mod.time, "sleep", lambda seconds: sleeps.append(float(seconds)))

    timeout_client = FakeCdsClient(
        source=FIXTURES / "minimal_t2m.nc",
        errors=[cds_mod.CdsTimeout(), cds_mod.CdsTimeout()],
    )
    dest = tmp_path / "era5.nc"
    cds_mod.download_cds_dataset(
        CdsRequestInput.model_validate(_valid_request()),
        dest,
        client=timeout_client,
    )
    assert dest.is_file()
    assert len(timeout_client.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert list(tmp_path.glob("**/*.part")) == []

    sleeps.clear()
    exhausted = FakeCdsClient(
        errors=[
            cds_mod.CdsRateLimit(status=429),
            cds_mod.CdsRateLimit(status=429),
            cds_mod.CdsRateLimit(status=429),
        ]
    )
    dest_rl = tmp_path / "limited.nc"
    with pytest.raises(ClimateError) as rl_err:
        cds_mod.download_cds_dataset(
            CdsRequestInput.model_validate(_valid_request()),
            dest_rl,
            client=exhausted,
        )
    assert rl_err.value.code == "CLIMATE_EXTERNAL_RATE_LIMIT"
    assert rl_err.value.retryable is True
    assert len(exhausted.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert not dest_rl.exists()
    assert list(tmp_path.glob("**/*.part")) == []


def test_permanent_errors_do_not_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openharness.climate import cds as cds_mod

    sleeps: list[float] = []
    monkeypatch.setattr(cds_mod.time, "sleep", lambda seconds: sleeps.append(float(seconds)))

    cases = [
        (
            cds_mod.CdsPermanentError(kind="auth", status=403),
            "CLIMATE_EXTERNAL_FAILED",
        ),
        (
            cds_mod.CdsPermanentError(kind="invalid_request", status=400),
            "CLIMATE_EXTERNAL_FAILED",
        ),
        (
            cds_mod.CdsPermanentError(kind="server_permanent", status=500),
            "CLIMATE_EXTERNAL_FAILED",
        ),
    ]
    for exc, code in cases:
        dest = tmp_path / f"{exc.kind}.nc"
        client = FakeCdsClient(errors=[exc, cds_mod.CdsTimeout()])
        with pytest.raises(ClimateError) as caught:
            cds_mod.download_cds_dataset(
                CdsRequestInput.model_validate(_valid_request()),
                dest,
                client=client,
            )
        assert caught.value.code == code
        assert caught.value.retryable is False
        assert len(client.calls) == 1
        assert not dest.exists()
        assert list(tmp_path.glob("**/*.part")) == []
    assert sleeps == []


def test_allow_sample_fallback_default_false_does_not_fallback(tmp_path: Path) -> None:
    from openharness.climate import cds as cds_mod

    parsed = CdsRequestInput.model_validate(_valid_request())
    assert parsed.allow_sample_fallback is False
    dest = tmp_path / "era5.nc"
    client = FakeCdsClient(errors=[cds_mod.CdsPermanentError(kind="auth", status=401)])
    with pytest.raises(ClimateError) as exc_info:
        cds_mod.download_cds_dataset(parsed, dest, client=client)
    assert exc_info.value.code == "CLIMATE_EXTERNAL_FAILED"
    assert not dest.exists()
    sample = tmp_path / "sample.csv"
    assert not sample.exists()


# --- 工具层：attempts / Context / ToolResult ---


@pytest.mark.asyncio
async def test_retries_do_not_increment_step_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    client = FakeCdsClient(
        source=FIXTURES / "minimal_t2m.nc",
        errors=[cds_mod.CdsTimeout(), cds_mod.CdsRateLimit(status=429)],
    )
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    result, payload = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_valid_request(),
    )
    assert result.is_error is False
    assert payload["ok"] is True
    context = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    step = next(item for item in context.steps if item.step_id == "acquire")
    assert step.status == "succeeded"
    assert step.attempts == 1
    assert len(client.calls) == 3
    published = workspace / ".climate" / "data" / RUN_ID
    assert list(published.rglob("*.part")) == []
    assert any(path.suffix == ".nc" for path in published.rglob("*") if path.is_file())


@pytest.mark.asyncio
async def test_credentials_never_enter_logs_context_trace_or_toolresult(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from evals.climate.models import EvalMode, TraceRecord
    from openharness.climate import cds as cds_mod

    leak = cds_mod.CdsPermanentError(kind="auth", status=403)
    leak_msg = f"authorization={FAKE_API_KEY} token={FAKE_SECRET} .cdsapirc=home-secret"
    leak.args = (leak_msg,)
    client = FakeCdsClient(errors=[leak])
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)

    with caplog.at_level(logging.DEBUG):
        result, payload = await _invoke(
            acquire,
            workspace,
            step_id="acquire",
            mode="cds",
            cds_request=_valid_request(),
        )
    assert result.is_error is True
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CLIMATE_EXTERNAL_FAILED"
    encoded = encode_tool_result_json(payload)
    assert FAKE_SECRET not in encoded
    assert FAKE_API_KEY not in encoded
    assert FAKE_SECRET not in caplog.text
    assert FAKE_API_KEY not in caplog.text

    context_path = workspace / ".climate" / "runs" / RUN_ID / "context.json"
    context_text = context_path.read_text(encoding="utf-8")
    assert FAKE_SECRET not in context_text
    assert FAKE_API_KEY not in context_text
    context = loads_run_context(context_text)
    acquire_step = next(item for item in context.steps if item.step_id == "acquire")
    assert acquire_step.error is not None
    assert acquire_step.error.code == "CLIMATE_EXTERNAL_FAILED"
    assert FAKE_SECRET not in dumps_climate_json(context)

    trace = TraceRecord.model_validate(
        {
            "suite_version": "g4-day12",
            "scenario_id": "cds-secret-scan",
            "run_id": RUN_ID,
            "mode": EvalMode.real_offline,
            "started_at": "2026-08-30T00:00:00Z",
            "finished_at": "2026-08-30T00:00:01Z",
            "duration_ms": 1,
            "tool_calls": [
                {
                    "sequence": 1,
                    "name": "climate_acquire_data",
                    "input_redacted": {"mode": "cds", "step_id": "acquire"},
                    "is_error": True,
                    "error_code": payload["error"]["code"],
                    "duration_ms": 1,
                }
            ],
            "hook_events": [],
            "final_run_status": context.status,
            "final_context_version": context.version,
            "artifact_manifest": [],
            "assertion_results": [],
            "synthetic": False,
            "tools_executed": True,
            "model_invoked": False,
            "counts_toward_real_pass_rate": True,
            "network_isolated": True,
        }
    )
    dumped_trace = json.dumps(trace.model_dump(mode="json"), ensure_ascii=False)
    envelope = failure_envelope(
        ClimateError(
            code=payload["error"]["code"],
            message=payload["error"]["message"],
            retryable=payload["error"]["retryable"],
            details=payload["error"]["details"],
        )
    )
    _scan_for_secrets(
        payload,
        envelope,
        context.model_dump(mode="json"),
        dumped_trace,
        caplog.text,
    )


def test_cds_module_does_not_import_cdsapi() -> None:
    import sys

    import openharness.climate.cds as cds_mod

    assert cds_mod.MAX_RETRIEVE_ATTEMPTS == 3
    assert "cdsapi" not in sys.modules


def test_default_tests_forbid_network() -> None:
    with pytest.raises(RuntimeError, match="禁止网络"):
        socket.create_connection(("203.0.113.1", 80), timeout=0.2)


def test_sample_fallback_codes_match_spec() -> None:
    """CDS-004：仅冻结 timeout/rate-limit；未列入的错误一律不得 fallback。"""
    from openharness.climate.cds import SAMPLE_FALLBACK_ERROR_CODES

    assert SAMPLE_FALLBACK_ERROR_CODES == frozenset(
        {"CLIMATE_EXTERNAL_TIMEOUT", "CLIMATE_EXTERNAL_RATE_LIMIT"}
    )


def test_download_layer_never_fallbacks_even_when_flag_true(tmp_path: Path) -> None:
    """下载层不得因 allow_sample_fallback 静默改写产物。"""
    from openharness.climate import cds as cds_mod

    parsed = CdsRequestInput.model_validate(_valid_request(allow_sample_fallback=True))
    dest = tmp_path / "era5.nc"
    client = FakeCdsClient(errors=[cds_mod.CdsTimeout(), cds_mod.CdsTimeout(), cds_mod.CdsTimeout()])
    with pytest.raises(ClimateError) as exc_info:
        cds_mod.download_cds_dataset(parsed, dest, client=client)
    assert exc_info.value.code == "CLIMATE_EXTERNAL_TIMEOUT"
    assert not dest.exists()
    assert list(tmp_path.glob("**/*.csv")) == []
    assert list(tmp_path.glob("**/*.part")) == []


def _audit_fields(blob: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(blob["requested_mode"]),
        str(blob["effective_mode"]),
        str(blob["fallback_reason"]),
    )


@pytest.mark.asyncio
async def test_fallback_is_explicit_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CDS-004：显式 fallback 在 ToolResult / Context / event 三处审计字段一致。"""
    from evals.climate.models import EvalMode, TraceRecord
    from openharness.climate import cds as cds_mod
    from openharness.climate.pipeline import build_sample_csv

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    client = FakeCdsClient(
        errors=[
            cds_mod.CdsTimeout(),
            cds_mod.CdsRateLimit(status=429),
            cds_mod.CdsTimeout(),
        ]
    )
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)

    corrupt_part = workspace / ".climate" / "data" / RUN_ID / ".cds-acquire.nc.dead.part"
    corrupt_part.parent.mkdir(parents=True, exist_ok=True)
    corrupt_part.write_bytes(b"not-a-sample")

    _, payload = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_valid_request(allow_sample_fallback=True),
    )
    assert payload["ok"] is True
    audit = _audit_fields(payload["data"])
    assert audit == ("cds", "sample", "CLIMATE_EXTERNAL_TIMEOUT")
    sample_path = workspace / payload["data"]["path"]
    assert sample_path.name == "sample.csv"
    assert sample_path.read_bytes() == build_sample_csv()
    assert sample_path.read_bytes() != b"not-a-sample"
    assert payload["data"]["media_type"] == "text/csv"
    assert not list((workspace / ".climate" / "data" / RUN_ID).rglob("*.part"))

    context = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    step = next(item for item in context.steps if item.step_id == "acquire")
    assert step.status == "succeeded"
    assert step.result is not None
    assert _audit_fields(step.result) == audit
    succeeded = [event for event in context.events if event.type == "step_succeeded"]
    assert succeeded
    assert _audit_fields(succeeded[-1].data) == audit

    trace = TraceRecord.model_validate(
        {
            "suite_version": "g4-day13",
            "scenario_id": "cds-explicit-fallback",
            "run_id": RUN_ID,
            "mode": EvalMode.real_offline,
            "started_at": "2026-08-30T00:00:00Z",
            "finished_at": "2026-08-30T00:00:01Z",
            "duration_ms": 1,
            "tool_calls": [
                {
                    "sequence": 1,
                    "name": "climate_acquire_data",
                    "input_redacted": {
                        "mode": "cds",
                        "step_id": "acquire",
                        "requested_mode": payload["data"]["requested_mode"],
                        "effective_mode": payload["data"]["effective_mode"],
                        "fallback_reason": payload["data"]["fallback_reason"],
                    },
                    "is_error": False,
                    "error_code": None,
                    "duration_ms": 1,
                }
            ],
            "hook_events": [],
            "final_run_status": context.status,
            "final_context_version": context.version,
            "artifact_manifest": [],
            "assertion_results": [],
            "synthetic": False,
            "tools_executed": True,
            "model_invoked": False,
            "counts_toward_real_pass_rate": True,
            "network_isolated": True,
        }
    )
    dumped_trace = json.dumps(trace.model_dump(mode="json"), ensure_ascii=False)
    assert dumped_trace.count("requested_mode") >= 1
    assert '"effective_mode": "sample"' in dumped_trace
    _scan_for_secrets(payload, context.model_dump(mode="json"), dumped_trace)


@pytest.mark.asyncio
async def test_fallback_false_returns_original_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    client = FakeCdsClient(
        errors=[cds_mod.CdsTimeout(), cds_mod.CdsTimeout(), cds_mod.CdsTimeout()]
    )
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _, payload = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_valid_request(),
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CLIMATE_EXTERNAL_TIMEOUT"
    assert "requested_mode" not in (payload.get("data") or {})
    data_dir = workspace / ".climate" / "data" / RUN_ID
    assert not (data_dir / "sample.csv").exists()
    context = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    step = next(item for item in context.steps if item.step_id == "acquire")
    assert step.status == "failed"
    assert step.error is not None
    assert step.error.code == "CLIMATE_EXTERNAL_TIMEOUT"


@pytest.mark.asyncio
async def test_fallback_rejects_errors_not_frozen_in_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """认证、校验、依赖、格式、写入和其他 external failure 不得 fallback。"""
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)

    real_build = cds_mod.build_cds_client
    disallowed: list[tuple[str, FakeCdsClient | None]] = [
        (
            "CLIMATE_EXTERNAL_FAILED",
            FakeCdsClient(errors=[cds_mod.CdsPermanentError(kind="auth", status=401)]),
        ),
        (
            "CLIMATE_EXTERNAL_FAILED",
            FakeCdsClient(errors=[cds_mod.CdsPermanentError(kind="invalid_request", status=400)]),
        ),
        (
            "CLIMATE_DATA_INVALID",
            FakeCdsClient(source=FIXTURES / "random_bytes.nc"),
        ),
        ("CLIMATE_DEPENDENCY_MISSING", None),
    ]
    for code, client in disallowed:
        if code == "CLIMATE_DEPENDENCY_MISSING":
            monkeypatch.setattr(cds_mod, "cdsapi_available", lambda: False)
            monkeypatch.setattr(cds_mod, "build_cds_client", real_build)
        else:
            assert client is not None
            monkeypatch.setattr(cds_mod, "cdsapi_available", lambda: True)
            monkeypatch.setattr(cds_mod, "build_cds_client", lambda client=client: client)
        _, payload = await _invoke(
            acquire,
            workspace,
            step_id="acquire",
            mode="cds",
            cds_request=_valid_request(allow_sample_fallback=True),
        )
        assert payload["ok"] is False, code
        assert payload["error"]["code"] == code
        assert not (workspace / ".climate" / "data" / RUN_ID / "sample.csv").exists()
        context = loads_run_context(
            (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(
                encoding="utf-8"
            )
        )
        step = next(item for item in context.steps if item.step_id == "acquire")
        assert step.status == "failed"


@pytest.mark.asyncio
async def test_fallback_switch_is_part_of_input_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    client = FakeCdsClient(
        errors=[cds_mod.CdsRateLimit(status=429)] * 3
    )
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    request = _valid_request(allow_sample_fallback=True)
    _, first = await _invoke(
        acquire, workspace, step_id="acquire", mode="cds", cds_request=request
    )
    assert first["ok"] is True
    context = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    version = context.version
    _, replay = await _invoke(
        acquire, workspace, step_id="acquire", mode="cds", cds_request=request
    )
    assert replay["ok"] is True
    replayed = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    assert replayed.version == version
    _, conflict = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_valid_request(allow_sample_fallback=False),
    )
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "CLIMATE_IDEMPOTENCY_CONFLICT"


def test_retrieve_payload_maps_iso_dates_to_era5_form() -> None:
    """真实 CDS form 使用 year/month/day/time，不把 ensemble 或 date 范围字符串送出。"""
    from openharness.climate.cds import build_retrieve_payload

    payload = build_retrieve_payload(
        CdsRequestInput.model_validate(_valid_request(date_end="2025-01-01"))
    )
    assert payload["product_type"] == ["reanalysis"]
    assert payload["variable"] == ["2m_temperature"]
    assert payload["year"] == ["2025"]
    assert payload["month"] == ["01"]
    assert payload["day"] == ["01"]
    assert len(payload["time"]) == 24
    assert payload["area"] == [40.0, 116.0, 39.0, 116.25]
    assert "date" not in payload
    assert "ensemble" not in json.dumps(payload)


def _smoke_cds_request() -> dict[str, Any]:
    """Day 14 最小真实请求：1 变量、1 天、约 1°、NetCDF、禁止 fallback。"""
    return {
        "dataset": "reanalysis-era5-single-levels",
        "variables": ["2m_temperature"],
        "area": [40.5, 116.0, 39.5, 117.0],
        "date_start": "2025-01-01",
        "date_end": "2025-01-01",
        "format": "netcdf",
        "allow_sample_fallback": False,
    }


def _assert_no_live_secrets(text: str) -> None:
    """扫描输出；不得回显凭证、主目录或 .cdsapirc。"""
    lowered = text.lower()
    assert ".cdsapirc" not in lowered
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if home:
        assert home not in text
        assert home.replace("\\", "/") not in text
    key = os.environ.get("CDSAPI_KEY")
    if key:
        assert key not in text


@pytest.mark.climate_integration
@pytest.mark.asyncio
async def test_real_cds_minimal_netcdf_smoke(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """最小真实 CDS：cds 模式、NetCDF magic、无 .part、无 fallback、输出脱敏。"""
    from openharness.climate.formats import detect_magic, read_bounded_profile

    caplog.set_level(logging.INFO)
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    inspect = registry.get("climate_inspect_dataset")
    assert init and plan and acquire and inspect

    _, init_payload = await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    _, plan_payload = await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _, acquired = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_smoke_cds_request(),
    )
    assert acquired["ok"] is True, json.dumps(acquired.get("error") or {}, ensure_ascii=False)
    data = acquired["data"]
    assert data["requested_mode"] == "cds"
    assert data["effective_mode"] == "cds"
    assert "fallback_reason" not in data
    assert data["media_type"] == "application/x-netcdf"
    relative = str(data["path"])
    dest = workspace / relative
    assert dest.is_file()
    assert dest.stat().st_size > 0
    assert dest.suffix == ".nc"
    assert detect_magic(dest.read_bytes()[:8]) == "netcdf"
    assert list(workspace.rglob("*.part")) == []

    profile = read_bounded_profile(dest, "netcdf")
    assert "t2m" in profile["variables"]

    _, inspected = await _invoke(inspect, workspace, step_id="inspect")
    assert inspected["ok"] is True
    assert inspected["data"]["format"] == "netcdf"

    dumped = json.dumps(
        [init_payload, plan_payload, acquired, inspected, profile],
        default=str,
        ensure_ascii=False,
    )
    _assert_no_live_secrets(dumped)
    _assert_no_live_secrets(caplog.text)
    context_text = (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(
        encoding="utf-8"
    )
    _assert_no_live_secrets(context_text)
    _scan_for_secrets(acquired, inspected)


def _offgrid_smoke_cds_request() -> dict[str, Any]:
    """真实多候选：1 天、小区域、area 偏离 0.25° 格点，禁止 fallback。"""
    return {
        "dataset": "reanalysis-era5-single-levels",
        "variables": ["2m_temperature"],
        "area": [40.6, 116.13, 39.47, 116.88],
        "date_start": "2025-01-01",
        "date_end": "2025-01-01",
        "format": "netcdf",
        "allow_sample_fallback": False,
    }


@pytest.mark.climate_integration
@pytest.mark.asyncio
async def test_real_cds_offgrid_candidates_are_audited(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """路径 A：真实 CDS 展开 ≥2 候选；审计字段脱敏；不得 fallback 为 sample。"""
    from openharness.climate.cds import expand_cds_candidates
    from openharness.climate.formats import detect_magic
    from openharness.climate.models import CdsRequestInput, loads_run_context

    parsed = CdsRequestInput.model_validate(_offgrid_smoke_cds_request())
    variants = expand_cds_candidates(parsed)
    assert 2 <= len(variants) <= 3

    caplog.set_level(logging.INFO)
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _, acquired = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_offgrid_smoke_cds_request(),
    )
    assert acquired["ok"] is True, json.dumps(acquired.get("error") or {}, ensure_ascii=False)
    data = acquired["data"]
    assert data["requested_mode"] == "cds"
    assert data["effective_mode"] == "cds"
    assert "fallback_reason" not in data
    assert int(data["candidate_count"]) >= 2
    assert int(data["candidate_index"]) >= 0
    assert data["winning_candidate"]
    dest = workspace / str(data["path"])
    assert dest.is_file()
    assert dest.suffix == ".nc"
    assert detect_magic(dest.read_bytes()[:8]) == "netcdf"
    assert list(workspace.rglob("*.part")) == []
    context = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    step = next(item for item in context.steps if item.step_id == "acquire")
    assert step.result is not None
    assert step.result["candidate_count"] == data["candidate_count"]
    assert step.result["winning_candidate"] == data["winning_candidate"]
    dumped = json.dumps([acquired, context.model_dump(mode="json")], ensure_ascii=False)
    _assert_no_live_secrets(dumped)
    _assert_no_live_secrets(caplog.text)
    _scan_for_secrets(acquired)


def _offgrid_request(**overrides: Any) -> dict[str, Any]:
    payload = _valid_request(
        area=[40.1, 116.13, 39.07, 116.37],
        date_start="2025-01-01",
        date_end="2025-01-02",
    )
    payload.update(overrides)
    return payload


def test_expand_cds_candidates_max_three_and_keeps_format() -> None:
    from openharness.climate.cds import expand_cds_candidates

    on_grid = CdsRequestInput.model_validate(_valid_request())
    aligned = expand_cds_candidates(on_grid)
    assert 1 <= len(aligned) <= 3
    assert aligned[0][0] == "identity"
    assert aligned[0][1].format == "netcdf"
    assert aligned[0][1].allow_sample_fallback is False

    off_grid = CdsRequestInput.model_validate(_offgrid_request())
    variants = expand_cds_candidates(off_grid)
    assert 2 <= len(variants) <= 3
    labels = [item[0] for item in variants]
    assert labels[0] == "identity"
    assert len(set(labels)) == len(labels)
    payloads = [item[1].model_dump(mode="json") for item in variants]
    areas = [tuple(item["area"]) for item in payloads]
    assert len(set(areas)) == len(areas)
    assert all(item["format"] == "netcdf" for item in payloads)
    assert all(item["allow_sample_fallback"] is False for item in payloads)


def test_candidate_first_permanent_fail_second_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    parsed = CdsRequestInput.model_validate(_offgrid_request())
    identity_area = [float(item) for item in parsed.area]

    class _SwitchClient:
        def __init__(self) -> None:
            self.calls: list[list[float]] = []

        def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
            del dataset
            area = [float(item) for item in request["area"]]
            self.calls.append(area)
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            if area == identity_area:
                raise cds_mod.CdsPermanentError("invalid_request")
            path.write_bytes((FIXTURES / "minimal_t2m.nc").read_bytes())

    dest = tmp_path / "era5.nc"
    published, audit = cds_mod.download_cds_dataset_with_candidates(
        parsed, dest, client=_SwitchClient()
    )
    assert published == dest
    assert dest.is_file()
    assert dest.stat().st_size > 0
    assert audit["candidate_count"] >= 2
    assert audit["candidate_index"] >= 1
    assert audit["winning_candidate"] != "identity"
    assert audit["winning_candidate"]
    dumped = json.dumps(audit)
    assert FAKE_SECRET not in dumped
    assert FAKE_API_KEY not in dumped
    assert "cdsapirc" not in dumped.lower()


def test_all_candidates_fail_returns_original_error_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    parsed = CdsRequestInput.model_validate(_offgrid_request())
    client = FakeCdsClient(errors=[cds_mod.CdsPermanentError("invalid_request")] * 9)
    dest = tmp_path / "era5.nc"
    with pytest.raises(ClimateError) as exc_info:
        cds_mod.download_cds_dataset_with_candidates(parsed, dest, client=client)
    err = exc_info.value
    assert err.code == "CLIMATE_EXTERNAL_FAILED"
    assert err.retryable is False
    assert not dest.exists()
    assert list(tmp_path.glob("**/*.part")) == []
    assert err.details.get("candidate_count") >= 2
    dumped = json.dumps(err.to_error_object())
    assert FAKE_SECRET not in dumped


@pytest.mark.asyncio
async def test_candidate_audit_is_in_toolresult_and_does_not_imply_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    parsed_area = [40.1, 116.13, 39.07, 116.37]

    class _SwitchClient:
        def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
            del dataset
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            area = [float(item) for item in request["area"]]
            if area == parsed_area:
                raise cds_mod.CdsPermanentError("invalid_request")
            path.write_bytes((FIXTURES / "minimal_t2m.nc").read_bytes())

    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: _SwitchClient())
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    _, payload = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_offgrid_request(),
    )
    assert payload["ok"] is True
    data = payload["data"]
    assert data["requested_mode"] == "cds"
    assert data["effective_mode"] == "cds"
    assert "fallback_reason" not in data
    assert data["candidate_count"] >= 2
    assert data["candidate_index"] >= 1
    assert data["winning_candidate"]
    _scan_for_secrets(payload)
    context = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    step = next(item for item in context.steps if item.step_id == "acquire")
    assert step.result is not None
    assert step.result["requested_mode"] == "cds"
    assert step.result["effective_mode"] == "cds"
    assert step.result["winning_candidate"] == data["winning_candidate"]
    succeeded = [event for event in context.events if event.type == "step_succeeded"]
    assert succeeded[-1].data["winning_candidate"] == data["winning_candidate"]
    dumped = json.dumps(context.model_dump(mode="json"), ensure_ascii=False)
    assert FAKE_SECRET not in dumped
    assert "sk-" not in dumped.lower()


# --- Day 21 / TEST-011：假 retrieve 挂起 ---


class HangingAfterWriteCdsClient:
    """写入预定字节后永不返回，模拟 cdsapi/multiurl 收尾挂起。"""

    def __init__(
        self,
        *,
        source: Path | None = None,
        payload: bytes | None = None,
        hang: bool = True,
    ) -> None:
        self.source = source
        self.payload = payload
        self.hang = hang
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.started = threading.Event()
        self._block = threading.Event()

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
        self.calls.append((dataset, dict(request), target))
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.payload is not None:
            path.write_bytes(self.payload)
        elif self.source is not None:
            path.write_bytes(self.source.read_bytes())
        self.started.set()
        if self.hang:
            self._block.wait()


class HangingWithOpenHandleCdsClient:
    """写入后保持写句柄不关闭、永不返回，模拟 Windows 上 cdsapi 占用 .part。"""

    def __init__(self, *, source: Path) -> None:
        self.source = source
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.started = threading.Event()
        self._block = threading.Event()
        self._handle: Any = None

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
        self.calls.append((dataset, dict(request), target))
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("wb")
        self._handle.write(self.source.read_bytes())
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self.started.set()
        self._block.wait()


def _short_retrieve_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod, "RETRIEVE_TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(cds_mod, "PART_STABLE_SECONDS", 0.15)
    monkeypatch.setattr(cds_mod, "PART_POLL_INTERVAL_SECONDS", 0.05)


def test_hanging_retrieve_publishes_stable_valid_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST-011 / CDS-008：写满合法 fixture 后挂起，仍须原子发布且无 .part 残留。"""
    from openharness.climate import cds as cds_mod

    _short_retrieve_bounds(monkeypatch)
    client = HangingAfterWriteCdsClient(source=FIXTURES / "minimal_t2m.nc")
    dest = tmp_path / "era5.nc"
    started = time.perf_counter()
    published = cds_mod.download_cds_dataset(
        CdsRequestInput.model_validate(_valid_request()),
        dest,
        client=client,
    )
    elapsed = time.perf_counter() - started
    assert published == dest
    assert dest.is_file()
    assert dest.stat().st_size > 0
    assert dest.read_bytes()[:8] == (FIXTURES / "minimal_t2m.nc").read_bytes()[:8]
    assert list(tmp_path.glob("**/*.part")) == []
    assert elapsed < 5.0
    assert client.started.is_set()
    assert len(client.calls) == 1


def test_hanging_retrieve_with_open_handle_still_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows 占用 .part 时仍须拷贝发布正式文件，不得干等 retrieve 返回。"""
    from openharness.climate import cds as cds_mod

    _short_retrieve_bounds(monkeypatch)
    client = HangingWithOpenHandleCdsClient(source=FIXTURES / "minimal_t2m.nc")
    dest = tmp_path / "era5.nc"
    started = time.perf_counter()
    published = cds_mod.download_cds_dataset(
        CdsRequestInput.model_validate(_valid_request()),
        dest,
        client=client,
    )
    elapsed = time.perf_counter() - started
    assert published == dest
    assert dest.is_file()
    assert dest.stat().st_size > 0
    assert dest.read_bytes()[:8] == (FIXTURES / "minimal_t2m.nc").read_bytes()[:8]
    assert elapsed < 5.0
    assert client.started.is_set()
    assert len(client.calls) == 1
    if client._handle is not None:
        client._handle.close()
        client._handle = None
    leftover_parts = [path for path in tmp_path.glob("**/*.part") if path.is_file()]
    for leftover in leftover_parts:
        leftover.unlink(missing_ok=True)


def test_stable_publish_does_not_open_netcdf4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发布不得调用 netCDF4：Windows 上 Dataset 打开 dest 会卡住 acquire。"""
    from openharness.climate import cds as cds_mod

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("publish 不得打开 NetCDF 解析器")

    monkeypatch.setattr(cds_mod, "_validate_part", _boom)
    _short_retrieve_bounds(monkeypatch)
    client = HangingWithOpenHandleCdsClient(source=FIXTURES / "minimal_t2m.nc")
    dest = tmp_path / "era5.nc"
    published = cds_mod.download_cds_dataset(
        CdsRequestInput.model_validate(_valid_request()),
        dest,
        client=client,
    )
    assert published == dest
    assert dest.is_file()
    if client._handle is not None:
        client._handle.close()
        client._handle = None


def test_existing_valid_dest_does_not_call_retrieve(
    tmp_path: Path,
) -> None:
    """正式 .nc 已在时不得再开 CDS retrieve，避免新的无 End 会话。"""
    from openharness.climate import cds as cds_mod

    dest = tmp_path / "era5.nc"
    dest.write_bytes((FIXTURES / "minimal_t2m.nc").read_bytes())
    client = FakeCdsClient(source=FIXTURES / "minimal_t2m.nc")
    published = cds_mod.download_cds_dataset(
        CdsRequestInput.model_validate(_valid_request()),
        dest,
        client=client,
    )
    assert published == dest
    assert client.calls == []


def test_hanging_retrieve_without_part_is_stable_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST-011 / CDS-006：retrieve 永不返回且无合法 .part → CLIMATE_EXTERNAL_TIMEOUT。"""
    from openharness.climate import cds as cds_mod

    _short_retrieve_bounds(monkeypatch)
    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    client = HangingAfterWriteCdsClient(payload=None, source=None)
    dest = tmp_path / "era5.nc"
    started = time.perf_counter()
    with pytest.raises(ClimateError) as exc_info:
        cds_mod.download_cds_dataset(
            CdsRequestInput.model_validate(_valid_request()),
            dest,
            client=client,
        )
    elapsed = time.perf_counter() - started
    err = exc_info.value
    assert err.code == "CLIMATE_EXTERNAL_TIMEOUT"
    assert err.details == {"reason": "timeout"}
    assert not dest.exists()
    assert list(tmp_path.glob("**/*.part")) == []
    assert elapsed < 8.0
    dumped = json.dumps(err.to_error_object(), ensure_ascii=False)
    assert str(tmp_path) not in dumped
    assert "http" not in dumped.lower()
    assert FAKE_SECRET not in dumped
    assert "cdsapirc" not in dumped.lower()


def test_hanging_retrieve_half_file_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST-011 / CDS-008：稳定但非法的半文件不得 os.replace。"""
    from openharness.climate import cds as cds_mod

    _short_retrieve_bounds(monkeypatch)
    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    dest = tmp_path / "era5.nc"
    truncated = (FIXTURES / "truncated.nc").read_bytes()
    client = HangingAfterWriteCdsClient(payload=truncated)
    with pytest.raises(ClimateError) as exc_info:
        cds_mod.download_cds_dataset(
            CdsRequestInput.model_validate(_valid_request()),
            dest,
            client=client,
        )
    err = exc_info.value
    assert err.code in {"CLIMATE_EXTERNAL_TIMEOUT", "CLIMATE_DATA_INVALID"}
    assert not dest.exists()
    assert list(tmp_path.glob("**/*.part")) == []
    dumped = json.dumps(err.to_error_object(), ensure_ascii=False)
    assert FAKE_SECRET not in dumped
    assert "http" not in dumped.lower()


@pytest.mark.asyncio
async def test_acquire_execute_offloads_hanging_cds_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST-011 / CDS-007：假挂起时事件循环仍能推进；成功路径无 .part。"""
    from openharness.climate import cds as cds_mod

    _short_retrieve_bounds(monkeypatch)
    client = HangingAfterWriteCdsClient(source=FIXTURES / "minimal_t2m.nc")
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)

    loop_progressed = False

    async def _mark_loop() -> None:
        nonlocal loop_progressed
        await asyncio.sleep(0.05)
        loop_progressed = True

    acquire_task = asyncio.create_task(
        _invoke(
            acquire,
            workspace,
            step_id="acquire",
            mode="cds",
            cds_request=_valid_request(),
        )
    )
    marker_task = asyncio.create_task(_mark_loop())
    await marker_task
    assert loop_progressed is True
    result, payload = await acquire_task
    assert result.is_error is False
    assert payload["ok"] is True
    published = workspace / ".climate" / "data" / RUN_ID
    assert any(path.suffix == ".nc" for path in published.rglob("*") if path.is_file())
    assert list(published.rglob("*.part")) == []
    context = loads_run_context(
        (workspace / ".climate" / "runs" / RUN_ID / "context.json").read_text(encoding="utf-8")
    )
    step = next(item for item in context.steps if item.step_id == "acquire")
    assert step.status == "succeeded"
    _scan_for_secrets(payload, context.model_dump(mode="json"))


# --- Day 22 / TEST-010：API schema 前置；非法载荷仍走 CLIMATE_INVALID_INPUT ---


def _cds_request_object_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    """取出 API schema 里 cds_request 的 object 合同（去掉 null 分支）。"""
    node = input_schema["properties"]["cds_request"]
    candidates = node.get("anyOf") or node.get("oneOf") or [node]
    objects = [
        item
        for item in candidates
        if isinstance(item, dict) and (item.get("type") == "object" or "properties" in item)
    ]
    assert len(objects) == 1, objects
    assert "$ref" not in objects[0]
    return objects[0]


def test_acquire_api_schema_exposes_catalog_long_names_not_t2m() -> None:
    """TEST-010：API schema 枚举 CDS 长名，不得把 t2m 列为 acquire 变量。"""
    from openharness.climate.formats import DATASET_VARIABLES
    from openharness.climate.registry import create_climate_tool_registry

    registry = create_climate_tool_registry()
    acquire = registry.get("climate_acquire_data")
    assert acquire is not None
    schema = acquire.to_api_schema()["input_schema"]
    cds_schema = _cds_request_object_schema(schema)
    allowed = sorted(DATASET_VARIABLES["reanalysis-era5-single-levels"])
    enum = cds_schema["properties"]["variables"]["items"]["enum"]
    assert enum == allowed
    assert "2m_temperature" in enum
    assert "t2m" not in enum
    required = set(cds_schema.get("required") or [])
    for key in ("dataset", "variables", "area", "date_start", "date_end", "format"):
        assert key in cds_schema["properties"]
        assert key in required
    assert "allow_sample_fallback" in cds_schema["properties"]
    assert "product_type" not in cds_schema["properties"]
    assert "time" not in cds_schema["properties"]
    runtime = acquire.input_model.model_fields["cds_request"].annotation
    assert runtime == dict[str, Any] | None


def test_acquire_api_schema_forbids_additional_cds_request_properties() -> None:
    """TEST-010：cds_request additionalProperties 为 false，与 extra=forbid 一致。"""
    registry = create_climate_tool_registry()
    acquire = registry.get("climate_acquire_data")
    assert acquire is not None
    cds_schema = _cds_request_object_schema(acquire.to_api_schema()["input_schema"])
    assert cds_schema.get("additionalProperties") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"product_type": "reanalysis"}, "product_type"),
        ({"time": ["00:00"]}, "time"),
        ({"variables": ["t2m"]}, "variables"),
    ],
)
async def test_acquire_illegal_payload_keeps_invalid_input_envelope(
    tmp_path: Path, override: dict[str, Any], field: str
) -> None:
    """TEST-010：门户多余键与 t2m 变量仍是 CLIMATE_INVALID_INPUT envelope，不是 QueryEngine 明文。"""
    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)

    raw = {
        "step_id": "acquire",
        "mode": "cds",
        "cds_request": _valid_request(**override),
    }
    arguments = acquire.input_model.model_validate(raw)
    result = await acquire.execute(arguments, ToolExecutionContext(cwd=workspace))
    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CLIMATE_INVALID_INPUT"
    assert payload["error"]["details"]["field"] == field
    if field == "variables":
        assert "2m_temperature" in payload["error"]["details"]["allowed"]
        assert "t2m" not in payload["error"]["details"]["allowed"]
    blob = json.dumps(payload)
    assert "Invalid input for climate_acquire_data" not in blob
    assert "Traceback" not in blob


@pytest.mark.asyncio
async def test_acquire_legal_seven_key_dict_still_succeeds_with_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST-010：合法七键 dict 仍走现有 mock acquire，不触网。"""
    from openharness.climate import cds as cds_mod

    monkeypatch.setattr(cds_mod.time, "sleep", lambda _seconds: None)
    client = FakeCdsClient(source=FIXTURES / "minimal_t2m.nc")
    monkeypatch.setattr(cds_mod, "build_cds_client", lambda: client)

    workspace = _workspace(tmp_path)
    registry = create_climate_tool_registry()
    init = registry.get("climate_init_workflow")
    plan = registry.get("climate_plan_steps")
    acquire = registry.get("climate_acquire_data")
    assert init and plan and acquire
    await _invoke(init, workspace, objective=OBJECTIVE, run_id=RUN_ID)
    await _invoke(plan, workspace, steps=STANDARD_STEPS)
    result, payload = await _invoke(
        acquire,
        workspace,
        step_id="acquire",
        mode="cds",
        cds_request=_valid_request(),
    )
    assert result.is_error is False
    assert payload["ok"] is True
    assert payload["data"]["requested_mode"] == "cds"
    assert payload["data"]["effective_mode"] == "cds"
    published = workspace / ".climate" / "data" / RUN_ID
    assert any(path.suffix == ".nc" for path in published.rglob("*") if path.is_file())
    assert list(published.rglob("*.part")) == []
    _scan_for_secrets(payload)
