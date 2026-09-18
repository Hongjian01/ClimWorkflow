"""DEC-G4-001：格式 magic/allowlist/optional 依赖与 integration marker。"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import pytest

from openharness.climate.errors import ClimateError
from openharness.climate.formats import (
    ALLOWLIST_SOURCE,
    DATASET_VARIABLES,
    SUPPORTED_DATASETS,
    climate_integration_skip_reason,
    detect_magic,
    eccodes_available,
    netcdf4_available,
    read_bounded_profile,
    validate_cds_allowlist,
    validate_published_artifact,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.filterwarnings("ignore:numpy.ndarray size changed:RuntimeWarning"),
    pytest.mark.filterwarnings("ignore:Setting the shape on a NumPy array:DeprecationWarning"),
]


def test_allowlist_source_and_era5_variables() -> None:
    """allowlist 绑定 CDS catalogue form（2026-08-30），不含浪场变量。"""
    assert "reanalysis-era5-single-levels" in ALLOWLIST_SOURCE
    assert "2026-08-30" in ALLOWLIST_SOURCE
    assert SUPPORTED_DATASETS == {"reanalysis-era5-single-levels"}
    variables = DATASET_VARIABLES["reanalysis-era5-single-levels"]
    assert "2m_temperature" in variables
    assert "mean_wave_direction" not in variables
    validate_cds_allowlist(
        dataset="reanalysis-era5-single-levels",
        variables=["2m_temperature"],
        fmt="netcdf",
    )
    with pytest.raises(ClimateError) as exc_info:
        validate_cds_allowlist(
            dataset="reanalysis-era5-pressure-levels",
            variables=["2m_temperature"],
            fmt="netcdf",
        )
    assert exc_info.value.code == "CLIMATE_INVALID_INPUT"
    with pytest.raises(ClimateError) as fmt_err:
        validate_cds_allowlist(
            dataset="reanalysis-era5-single-levels",
            variables=["2m_temperature"],
            fmt="zip",
        )
    assert fmt_err.value.code == "CLIMATE_INVALID_INPUT"
    with pytest.raises(ClimateError) as var_err:
        validate_cds_allowlist(
            dataset="reanalysis-era5-single-levels",
            variables=["mean_wave_direction"],
            fmt="grib",
        )
    assert var_err.value.code == "CLIMATE_INVALID_INPUT"


def test_netcdf_fixture_reads_variables_dims_coords() -> None:
    """Windows/CI 可读最小合成 NetCDF；不访问 CDS。"""
    profile = read_bounded_profile(FIXTURES / "minimal_t2m.nc", "netcdf")
    assert profile["format"] == "netcdf"
    assert profile["variables"] == ["t2m"]
    assert profile["dimensions"] == {"time": 2, "latitude": 2, "longitude": 2}
    assert profile["coordinates"]["latitude"] == [40.0, 39.0]
    assert profile["coordinates"]["longitude"] == [116.0, 116.25]
    assert profile["coordinates"]["time"] == [0.0, 1.0]
    assert (FIXTURES / "minimal_t2m.nc").stat().st_size < 20_000


def test_grib_fixture_reads_variables_dims_coords() -> None:
    """eccodes 读取最小合成 GRIB；坐标来自网格角点，不展开全网格。"""
    profile = read_bounded_profile(FIXTURES / "minimal.grib", "grib")
    assert profile["format"] == "grib"
    assert profile["variables"] == ["t"]
    assert profile["dimensions"]["latitude"] == 2
    assert profile["dimensions"]["longitude"] == 2
    assert profile["coordinates"]["latitude"] == [60.0, 0.0]
    assert profile["coordinates"]["longitude"] == [0.0, 30.0]
    assert profile["coordinates"]["time"] == ["20070323T1200"]
    assert (FIXTURES / "minimal.grib").stat().st_size < 1024


@pytest.mark.parametrize(
    ("name", "claimed", "reason"),
    [
        ("truncated.nc", "netcdf", "parser_rejected"),
        ("truncated.grib", "grib", "parser_rejected"),
        ("random_bytes.nc", "netcdf", "unknown_magic"),
        ("grib_magic.nc", "netcdf", "magic_extension_mismatch"),
        ("netcdf_magic.grib", "grib", "magic_extension_mismatch"),
    ],
)
def test_truncated_and_masquerade_files_are_rejected(
    name: str, claimed: str, reason: str
) -> None:
    with pytest.raises(ClimateError) as exc_info:
        validate_published_artifact(FIXTURES / name, claimed)
    err = exc_info.value
    assert err.code == "CLIMATE_DATA_INVALID"
    assert err.details["reason"] == reason
    assert "C:\\" not in err.message
    assert "sk-" not in err.message.lower()


def test_magic_bytes_match_unidata_and_grib() -> None:
    nc = (FIXTURES / "minimal_t2m.nc").read_bytes()[:8]
    grib = (FIXTURES / "minimal.grib").read_bytes()[:4]
    assert detect_magic(nc) == "netcdf"
    assert nc.startswith(b"\x89HDF\r\n\x1a\n")
    assert detect_magic(grib) == "grib"
    assert grib == b"GRIB"


def test_optional_netcdf_missing_is_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openharness.climate.formats.netcdf4_available", lambda: False
    )
    with pytest.raises(ClimateError) as exc_info:
        read_bounded_profile(FIXTURES / "minimal_t2m.nc", "netcdf")
    err = exc_info.value
    assert err.code == "CLIMATE_DEPENDENCY_MISSING"
    assert err.details["reason"] == "netCDF4"
    assert "C:\\" not in err.message
    assert ".cdsapirc" not in err.message


def test_optional_eccodes_missing_is_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openharness.climate.formats.eccodes_available", lambda: False
    )
    with pytest.raises(ClimateError) as exc_info:
        read_bounded_profile(FIXTURES / "minimal.grib", "grib")
    err = exc_info.value
    assert err.code == "CLIMATE_DEPENDENCY_MISSING"
    assert err.details["reason"] == "eccodes"
    assert "token" not in err.message.lower()


def test_eccodes_available_false_when_native_library_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺 libeccodes 时 import 抛 RuntimeError，必须视为未安装，不能打穿测试。"""
    monkeypatch.delitem(sys.modules, "eccodes", raising=False)
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "eccodes":
            raise RuntimeError("Cannot find the ecCodes library")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert eccodes_available() is False


def test_readers_are_optional_and_currently_installed() -> None:
    """dev extra 安装后默认 CI 应能读取；缺库路径由上两项覆盖。"""
    assert netcdf4_available() is True
    assert eccodes_available() is True


def test_pyproject_registers_climate_integration_marker() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "climate_integration" in text
    assert "markers" in text
    # uv 依赖 PyPI index metadata；必须显式钉 eccodeslib，Linux CI 才能找到原生库。
    assert "eccodeslib>=2.48.0; sys_platform != 'win32'" in text


def test_default_skip_reason_has_no_credentials_or_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLIMATE_INTEGRATION", raising=False)
    monkeypatch.setenv("CDSAPI_KEY", "cds-token-should-never-appear")
    reason = climate_integration_skip_reason()
    assert reason == "climate_integration 默认跳过；需 CLIMATE_INTEGRATION=1"
    assert "cds-token-should-never-appear" not in reason
    assert ".cdsapirc" not in reason
    assert "C:\\" not in reason


def test_enabled_without_credentials_skip_reason_has_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLIMATE_INTEGRATION", "1")
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    reason = climate_integration_skip_reason()
    assert reason == "climate_integration skipped: credentials not provided"
    assert ".cdsapirc" not in reason
    assert "C:\\" not in reason
    assert "C:\\Users" not in reason


def test_formats_module_does_not_import_cdsapi() -> None:
    """默认测试路径不得把 cdsapi 变成硬依赖。"""
    import sys

    import openharness.climate.formats as formats_mod

    assert formats_mod.SUPPORTED_FORMATS == {"grib", "netcdf"}
    assert "cdsapi" not in sys.modules


def _write_netcdf(path: Path, *, kind: str) -> None:
    """在临时目录生成非法/边界 NetCDF；不写入仓库 fixture。"""
    from netCDF4 import Dataset

    with Dataset(path, "w", format="NETCDF4") as ds:
        if kind == "missing_variable":
            ds.createDimension("time", 1)
            times = ds.createVariable("time", "f8", ("time",))
            times[:] = [0.0]
            return
        if kind == "empty_time":
            ds.createDimension("time", 0)
            ds.createDimension("latitude", 1)
            ds.createDimension("longitude", 1)
            ds.createVariable("time", "f8", ("time",))
            lat = ds.createVariable("latitude", "f4", ("latitude",))
            lon = ds.createVariable("longitude", "f4", ("longitude",))
            ds.createVariable("t2m", "f4", ("time", "latitude", "longitude"))
            lat[:] = [40.0]
            lon[:] = [116.0]
            return
        if kind == "illegal_coordinate":
            ds.createDimension("time", 1)
            ds.createDimension("latitude", 2)
            ds.createDimension("longitude", 1)
            times = ds.createVariable("time", "f8", ("time",))
            lat = ds.createVariable("latitude", "f4", ("latitude",))
            lon = ds.createVariable("longitude", "f4", ("longitude",))
            t2m = ds.createVariable("t2m", "f4", ("time", "latitude", "longitude"))
            times[:] = [0.0]
            lat[:] = [95.0, 100.0]
            lon[:] = [116.0]
            t2m[0, 0, 0] = 273.15
            t2m[0, 1, 0] = 273.15
            return
        raise AssertionError(kind)


def test_netcdf_and_grib_profiles_include_bounded_statistics() -> None:
    """正常 fixture 必须给出变量/维度/坐标和有界 min/max/mean，不得展开全网格。"""
    nc = read_bounded_profile(FIXTURES / "minimal_t2m.nc", "netcdf")
    assert nc["format"] == "netcdf"
    assert nc["variables"] == ["t2m"]
    assert nc["dimensions"] == {"time": 2, "latitude": 2, "longitude": 2}
    stats = nc["statistics"]["t2m"]
    assert stats["min"] == pytest.approx(273.1, abs=1e-4)
    assert stats["max"] == pytest.approx(274.4, abs=1e-4)
    assert stats["mean"] == pytest.approx(273.75, abs=1e-4)
    assert stats["count"] == 8
    encoded = json.dumps(nc)
    assert "273.2" not in encoded or "values" not in nc
    assert "grid" not in nc
    assert encoded.count("273.") <= 6

    grib = read_bounded_profile(FIXTURES / "minimal.grib", "grib")
    assert grib["format"] == "grib"
    assert grib["variables"] == ["t"]
    gstats = grib["statistics"]["t"]
    assert gstats["min"] <= gstats["max"]
    assert isinstance(gstats["mean"], float)
    assert gstats["count"] >= 1
    assert "values" not in grib["statistics"]["t"]


def test_netcdf_rejects_missing_variable_empty_time_and_illegal_coords(
    tmp_path: Path,
) -> None:
    cases = [
        ("missing_variable", "missing_variable"),
        ("empty_time", "empty_time"),
        ("illegal_coordinate", "illegal_coordinate"),
    ]
    for kind, reason in cases:
        path = tmp_path / f"{kind}.nc"
        _write_netcdf(path, kind=kind)
        with pytest.raises(ClimateError) as exc_info:
            read_bounded_profile(path, "netcdf")
        err = exc_info.value
        assert err.code == "CLIMATE_DATA_INVALID"
        assert err.details["reason"] == reason
        assert "C:\\" not in err.message
        assert str(path) not in err.message


def test_profile_is_bounded_and_does_not_modify_source() -> None:
    """inspect/profile 不得改源文件 bytes，且序列化有界。"""
    import hashlib

    for name, claimed in (("minimal_t2m.nc", "netcdf"), ("minimal.grib", "grib")):
        path = FIXTURES / name
        before = path.read_bytes()
        digest = hashlib.sha256(before).hexdigest()
        mtime = path.stat().st_mtime_ns
        profile = read_bounded_profile(path, claimed)
        encoded = json.dumps(profile, ensure_ascii=False).encode("utf-8")
        assert len(encoded) < 16_384
        assert path.read_bytes() == before
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert path.stat().st_mtime_ns == mtime
        blob = encoded.decode("utf-8")
        assert "netCDF4.Dataset" not in blob
        assert "codes_handle" not in blob
        assert "numpy." not in blob


def test_extension_magic_and_parser_must_agree(tmp_path: Path) -> None:
    """三者一致才接受：不能只看扩展名。"""
    nc_bytes = (FIXTURES / "minimal_t2m.nc").read_bytes()
    grib_bytes = (FIXTURES / "minimal.grib").read_bytes()

    nc_as_grib = tmp_path / "copy.grib"
    nc_as_grib.write_bytes(nc_bytes)
    with pytest.raises(ClimateError) as mismatch:
        validate_published_artifact(nc_as_grib, "grib")
    assert mismatch.value.code == "CLIMATE_DATA_INVALID"
    assert mismatch.value.details["reason"] == "magic_extension_mismatch"

    grib_as_nc = tmp_path / "copy.nc"
    grib_as_nc.write_bytes(grib_bytes)
    with pytest.raises(ClimateError) as swapped:
        validate_published_artifact(grib_as_nc, "netcdf")
    assert swapped.value.details["reason"] == "magic_extension_mismatch"

    # 扩展名声称 netcdf，但 claimed_format=grib，即使内容是 GRIB 也拒绝
    grib_wrong_claim = tmp_path / "real.grib"
    grib_wrong_claim.write_bytes(grib_bytes)
    with pytest.raises(ClimateError) as claimed:
        validate_published_artifact(grib_wrong_claim, "netcdf")
    assert claimed.value.details["reason"] == "magic_extension_mismatch"


def test_netcdf_profile_opens_from_memory_not_workspace_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """in-process 路径必须走 memory=；默认 TUI 走子进程，本测试强制 in-process。"""
    import netCDF4

    monkeypatch.setenv("CLIMATE_NETCDF_INPROCESS", "1")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    original = netCDF4.Dataset

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    netCDF4.Dataset = _spy  # type: ignore[method-assign]
    try:
        profile = read_bounded_profile(FIXTURES / "minimal_t2m.nc", "netcdf")
    finally:
        netCDF4.Dataset = original  # type: ignore[method-assign]
    assert profile["variables"] == ["t2m"]
    assert calls
    assert any("memory" in kwargs and kwargs["memory"] for _args, kwargs in calls)
