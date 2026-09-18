"""G4 科学数据格式契约（DEC-G4-001）：magic、allowlist、optional reader。

本模块不访问 CDS、不读取凭证。下载客户端属于 Day 12。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openharness.climate.errors import climate_error

# Unidata netCDF-C 文件格式规范：classic magic = 'C''D''F''\\x01'；64-bit 为 '\\x02'。
# NetCDF-4 使用 HDF5 存储层，文件以 HDF5 签名开头。
# 来源：https://docs.unidata.ucar.edu/netcdf-c/current/file_format_specifications.html
# 检索日期：2026-08-30。
NETCDF_CLASSIC_MAGIC = b"CDF\x01"
NETCDF_64BIT_MAGIC = b"CDF\x02"
HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
# WMO GRIB 消息以 ASCII "GRIB" 开头（edition 1/2）。
GRIB_MAGIC = b"GRIB"

NETCDF_EXTENSIONS = frozenset({".nc", ".nc4", ".netcdf"})
GRIB_EXTENSIONS = frozenset({".grib", ".grb", ".grib2"})

# CDS Catalogue form（2026-08-30）:
# https://cds.climate.copernicus.eu/api/catalogue/v1/collections/reanalysis-era5-single-levels
# form JSON: .../form_bf94874f55a3bdca2ed65f50da554348910044c37be17e4fd4a2ea0096174239.json
# DOI: 10.24381/cds.adbb2d47  许可：CC-BY-4.0
ALLOWLIST_SOURCE = (
    "cds-catalogue:reanalysis-era5-single-levels;retrieved=2026-08-30;"
    "form=form_bf94874f55a3bdca2ed65f50da554348910044c37be17e4fd4a2ea0096174239.json;"
    "doi=10.24381/cds.adbb2d47"
)

SUPPORTED_DATASETS = frozenset({"reanalysis-era5-single-levels"})
# 用户请求不含 product_type；G4 固定向 CDS 发送 reanalysis，拒绝 ensemble。
SUPPORTED_PRODUCT_TYPES = frozenset({"reanalysis"})
SUPPORTED_FORMATS = frozenset({"netcdf", "grib"})
# 仅冻结 Popular/大气单层常用变量；排除浪场（网格 0.5°，不得与大气 0.25° 混下）。
DATASET_VARIABLES: dict[str, frozenset[str]] = {
    "reanalysis-era5-single-levels": frozenset(
        {
            "2m_temperature",
            "2m_dewpoint_temperature",
            "10m_u_component_of_wind",
            "10m_v_component_of_wind",
            "mean_sea_level_pressure",
            "surface_pressure",
            "total_precipitation",
            "sea_surface_temperature",
        }
    )
}

# CDS 新 API 字段名为 data_format；ClimWorkflow cds_request.format 映射到此。
CDS_DATA_FORMAT = {"netcdf": "netcdf", "grib": "grib"}
# 官方 form 将 netcdf 标为 "NetCDF4 (Experimental)"，默认值为 grib。G4 两者都支持。
CDS_NETCDF_LABEL = "NetCDF4 (Experimental)"


def netcdf4_available() -> bool:
    """检测 NetCDF 读取库；测试可 monkeypatch。

    用 find_spec，避免在 TUI 进程里 import netCDF4/HDF5（Windows 上会占住 GIL）。
    """
    import importlib.util

    return importlib.util.find_spec("netCDF4") is not None


def eccodes_available() -> bool:
    """检测 GRIB 读取库；测试可 monkeypatch。

    Python 包在、原生 libeccodes 不在时，import 抛 RuntimeError 而非 ImportError。
    """
    try:
        import eccodes  # noqa: F401
    except (ImportError, RuntimeError, OSError):
        return False
    return True


def detect_magic(header: bytes) -> str | None:
    """根据文件头识别格式；无法识别时返回 None。"""
    if header.startswith(HDF5_MAGIC) or header.startswith(NETCDF_CLASSIC_MAGIC):
        return "netcdf"
    if header.startswith(NETCDF_64BIT_MAGIC):
        return "netcdf"
    if header.startswith(GRIB_MAGIC):
        return "grib"
    return None


def format_from_extension(path: Path) -> str | None:
    """由扩展名推断声称格式。"""
    ext = path.suffix.lower()
    if ext in NETCDF_EXTENSIONS:
        return "netcdf"
    if ext in GRIB_EXTENSIONS:
        return "grib"
    return None


def validate_cds_allowlist(
    *,
    dataset: str,
    variables: list[str],
    fmt: str,
) -> None:
    """校验 dataset/variables/format allowlist；不触网。"""
    if dataset not in SUPPORTED_DATASETS:
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "dataset 不在 G4 allowlist 内",
            details={"field": "dataset", "allowed": sorted(SUPPORTED_DATASETS)},
        )
    if fmt not in SUPPORTED_FORMATS:
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "format 仅允许 netcdf 或 grib",
            details={"field": "format", "allowed": sorted(SUPPORTED_FORMATS)},
        )
    allowed = DATASET_VARIABLES[dataset]
    unknown = [item for item in variables if item not in allowed]
    if not variables or unknown:
        raise climate_error(
            "CLIMATE_INVALID_INPUT",
            "variables 必须非空且全部位于该 dataset allowlist",
            details={"field": "variables", "allowed": sorted(allowed)},
        )


def validate_published_artifact(
    path: Path,
    claimed_format: str,
    *,
    suffix_path: Path | None = None,
    parse: bool = True,
) -> str:
    """扩展名、magic、解析器三者一致才接受；返回标准化格式名。

    ``suffix_path`` 用于 ``.part`` 临时文件：按最终目标扩展名校验，读取的是 path。
    ``parse=False`` 只做 magic/扩展名，避免 inspect 连开两次 NetCDF 子进程。
    """
    if claimed_format not in SUPPORTED_FORMATS:
        raise climate_error(
            "CLIMATE_FORMAT_UNSUPPORTED",
            "不支持的科学数据格式",
            details={"field": "format", "allowed": sorted(SUPPORTED_FORMATS)},
        )
    if not path.is_file():
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "发布产物必须是非空常规文件",
            details={"field": "path", "reason": "not_a_file"},
        )
    size = path.stat().st_size
    if size <= 0:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "发布产物不得为空",
            details={"field": "path", "reason": "empty"},
        )
    with path.open("rb") as handle:
        header = handle.read(8)
    magic_format = detect_magic(header)
    ext_format = format_from_extension(suffix_path or path)
    if magic_format is None:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "文件内容无法识别为 NetCDF 或 GRIB",
            details={"field": "format", "reason": "unknown_magic"},
        )
    if ext_format is None or ext_format != magic_format or magic_format != claimed_format:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "扩展名、magic 与声称格式必须一致",
            details={"field": "format", "reason": "magic_extension_mismatch"},
        )
    if parse:
        _parse_or_reject(path, claimed_format)
    return claimed_format


def read_bounded_profile(path: Path, claimed_format: str) -> dict[str, Any]:
    """读取有界 profile：变量、维度、时间/经纬坐标与统计；不序列化全网格。"""
    fmt = validate_published_artifact(path, claimed_format)
    from openharness.climate.readers import read_scientific_profile

    return read_scientific_profile(path, fmt)


def _parse_or_reject(path: Path, claimed_format: str) -> None:
    from openharness.climate.readers import open_scientific_reader, read_scientific_profile

    if claimed_format == "netcdf":
        read_scientific_profile(path, claimed_format)
        return
    with open_scientific_reader(path, claimed_format):
        return


def climate_integration_enabled() -> bool:
    """真实网络集成必须显式开关；默认关闭。"""
    import os

    return os.environ.get("CLIMATE_INTEGRATION") == "1"


def climate_integration_credentials_present() -> bool:
    """只检查环境变量是否存在，不读取文件、不回显值。"""
    import os

    return bool(os.environ.get("CDSAPI_KEY"))


def climate_integration_skip_reason() -> str:
    """稳定 skip reason；不得包含路径、token 或环境值。"""
    if not climate_integration_enabled():
        return "climate_integration 默认跳过；需 CLIMATE_INTEGRATION=1"
    if not climate_integration_credentials_present():
        return "climate_integration skipped: credentials not provided"
    return "climate_integration skipped"