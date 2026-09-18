"""G4 窄 Reader Adapter：NetCDF4 / eccodes，只返回 JSON 安全的有界 profile。

不把 Dataset/GRIB handle/numpy 对象写入 Context。不执行 dataset 表达式或插件。
Windows 上不得对磁盘路径直接 Dataset()：与仍占用的 CDS retrieve 同进程时会锁死。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import warnings
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

from openharness.climate.errors import ClimateError, climate_error
from openharness.climate import formats as formats_mod

# 必须在首次加载 HDF5/netCDF4 之前；关闭文件锁避免 Windows 上 Dataset 挂起。
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

MAX_PROFILE_VARIABLES = 32
MAX_COORD_SAMPLES = 32
MAX_STATS_ELEMENTS = 65_536
MAX_PLOT_POINTS = 4_096
_PROFILE_WARNING_LIMIT = 20
_LAT_NAMES = ("latitude", "lat")
_LON_NAMES = ("longitude", "lon")
_TIME_NAMES = ("time",)
DATASET_OPEN_TIMEOUT_SECONDS = 60.0
_NETCDF_INPROCESS_ENV = "CLIMATE_NETCDF_INPROCESS"


class ScientificReader(AbstractContextManager["ScientificReader"]):
    """统一关闭资源；子类不得把库对象暴露给调用方。"""

    def bounded_profile(self) -> dict[str, Any]:
        raise NotImplementedError

    def bounded_values(self, name: str, *, limit: int) -> tuple[float, ...]:
        raise NotImplementedError


def open_scientific_reader(path: Path, claimed_format: str) -> ScientificReader:
    """按冻结格式打开 adapter；缺依赖返回稳定错误。"""
    if claimed_format == "netcdf":
        return NetcdfReaderAdapter(path)
    if claimed_format == "grib":
        return GribReaderAdapter(path)
    raise climate_error(
        "CLIMATE_FORMAT_UNSUPPORTED",
        "不支持的科学数据格式",
        details={"field": "format", "reason": claimed_format},
    )


def read_scientific_profile(path: Path, claimed_format: str) -> dict[str, Any]:
    if claimed_format == "netcdf" and not _netcdf_inprocess():
        if not formats_mod.netcdf4_available():
            raise _missing("netCDF4")
        payload = _netcdf_via_subprocess("profile", path)
        if not isinstance(payload, dict):
            raise _parser_error("netcdf")
        return payload
    with open_scientific_reader(path, claimed_format) as reader:
        return reader.bounded_profile()


def read_plot_values(
    path: Path, claimed_format: str, y_name: str, *, limit: int = MAX_PLOT_POINTS
) -> tuple[float, ...]:
    if claimed_format == "netcdf" and not _netcdf_inprocess():
        if not formats_mod.netcdf4_available():
            raise _missing("netCDF4")
        payload = _netcdf_via_subprocess("values", path, extra=[y_name, str(limit)])
        raw = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            raise climate_error(
                "CLIMATE_DATA_INVALID",
                "没有可用于绘图的数值",
                details={"field": "y", "reason": "missing_variable"},
            )
        return tuple(float(item) for item in raw)
    with open_scientific_reader(path, claimed_format) as reader:
        return reader.bounded_values(y_name, limit=limit)


def _netcdf_inprocess() -> bool:
    return os.environ.get(_NETCDF_INPROCESS_ENV) == "1"


def _netcdf_via_subprocess(
    action: str, path: Path, extra: list[str] | None = None
) -> dict[str, Any]:
    """子进程解析，超时可杀；避免 HDF5 在 TUI 进程里占住 GIL。

    TUI（Node 管道 + Windows 锁句柄）默认会把句柄遗传给子进程，HDF5 可能
    因此卡死。这里关掉句柄继承，并把字节拷到临时文件再解析。
    """
    payload = _read_path_bytes(path)
    fd, raw_tmp = tempfile.mkstemp(prefix="climate-nc-", suffix=".nc")
    os.close(fd)
    tmp_path = Path(raw_tmp)
    command = [
        sys.executable,
        "-m",
        "openharness.climate.netcdf_worker",
        action,
        str(tmp_path),
    ]
    if extra:
        command.extend(extra)
    env = os.environ.copy()
    env["HDF5_USE_FILE_LOCKING"] = "FALSE"
    env[_NETCDF_INPROCESS_ENV] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    run_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "timeout": float(DATASET_OPEN_TIMEOUT_SECONDS),
        "env": env,
        "check": False,
        "close_fds": True,
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        tmp_path.write_bytes(payload)
        completed = subprocess.run(command, **run_kwargs)
    except subprocess.TimeoutExpired:
        raise climate_error(
            "CLIMATE_DATA_INVALID",
            "NetCDF 解析失败，可能已截断或损坏",
            details={"field": "format", "reason": "parser_timeout"},
        ) from None
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        payload = json.loads((completed.stdout or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _parser_error("netcdf") from None
    if not isinstance(payload, dict):
        raise _parser_error("netcdf")
    if completed.returncode != 0 or payload.get("__climate_error__") is True:
        code = payload.get("code")
        message = payload.get("message")
        details = payload.get("details")
        if not isinstance(code, str) or not isinstance(message, str):
            raise _parser_error("netcdf")
        raise climate_error(
            code,
            message,
            details=details if isinstance(details, dict) else {},
        )
    return payload


def _missing(package: str) -> ClimateError:
    return climate_error(
        "CLIMATE_DEPENDENCY_MISSING",
        f"缺少可选依赖 {package}",
        details={"field": "format", "reason": package},
    )


def _parser_error(kind: str) -> ClimateError:
    label = "NetCDF" if kind == "netcdf" else "GRIB"
    return climate_error(
        "CLIMATE_DATA_INVALID",
        f"{label} 解析失败，可能已截断或损坏",
        details={"field": "format", "reason": "parser_rejected"},
    )


def _read_path_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        from openharness.climate.cds import _read_shared_payload

        return _read_shared_payload(path)


def _open_netcdf_from_memory(payload: bytes) -> Any:
    """用内存缓冲区打开。超时由外层子进程负责，线程 join 在 GIL 下等不到。"""
    from netCDF4 import Dataset

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dataset = Dataset("in-memory.nc", "r", memory=payload)
    except ClimateError:
        raise
    except Exception:
        raise _parser_error("netcdf") from None
    if dataset is None:
        raise _parser_error("netcdf")
    return dataset


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _flatten_floats(raw: Any, *, limit: int) -> list[float]:
    compressed = getattr(raw, "compressed", None)
    items = compressed() if callable(compressed) else raw
    ravel = getattr(items, "ravel", None)
    seq = ravel() if callable(ravel) else items
    try:
        iterator = iter(seq)
    except TypeError:
        number = _as_float(seq)
        return [number] if number is not None else []
    out: list[float] = []
    for item in iterator:
        number = _as_float(item)
        if number is None:
            continue
        out.append(number)
        if len(out) >= limit:
            break
    return out


def _bounded_coord_list(values: list[float]) -> list[float]:
    if len(values) <= MAX_COORD_SAMPLES:
        return values
    return [values[0], values[-1]]


def _json_stats(values: list[float]) -> dict[str, float | int]:
    minimum = min(values)
    maximum = max(values)
    mean = sum(values) / len(values)
    return {
        "min": float(minimum),
        "max": float(maximum),
        "mean": float(mean),
        "count": int(len(values)),
    }


class NetcdfReaderAdapter(ScientificReader):
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ds: Any = None

    def __enter__(self) -> NetcdfReaderAdapter:
        if not formats_mod.netcdf4_available():
            raise _missing("netCDF4")
        os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
        try:
            payload = _read_path_bytes(self._path)
            self._ds = _open_netcdf_from_memory(payload)
        except ClimateError:
            raise
        except Exception:
            raise _parser_error("netcdf") from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._ds is not None:
            closer = getattr(self._ds, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
            self._ds = None

    def bounded_profile(self) -> dict[str, Any]:
        ds = self._ds
        if ds is None:
            raise _parser_error("netcdf")
        try:
            dimensions = {str(name): int(dim.size) for name, dim in ds.dimensions.items()}
            if "time" in dimensions and dimensions["time"] <= 0:
                raise climate_error(
                    "CLIMATE_DATA_INVALID",
                    "时间维度为空",
                    details={"field": "format", "reason": "empty_time"},
                )
            data_vars = [
                str(name)
                for name in ds.variables
                if str(name) not in ds.dimensions
            ][:MAX_PROFILE_VARIABLES]
            if not data_vars:
                raise climate_error(
                    "CLIMATE_DATA_INVALID",
                    "缺少数据变量",
                    details={"field": "format", "reason": "missing_variable"},
                )
            coordinates: dict[str, list[float]] = {}
            for name in (*_TIME_NAMES, *_LAT_NAMES, *_LON_NAMES):
                if name not in ds.variables:
                    continue
                values = _flatten_floats(ds.variables[name][:], limit=MAX_STATS_ELEMENTS)
                if name in _LAT_NAMES and any(item < -90.0 or item > 90.0 for item in values):
                    raise climate_error(
                        "CLIMATE_DATA_INVALID",
                        "纬度坐标非法",
                        details={"field": "format", "reason": "illegal_coordinate"},
                    )
                if name in _LON_NAMES and any(item < -180.0 or item > 360.0 for item in values):
                    raise climate_error(
                        "CLIMATE_DATA_INVALID",
                        "经度坐标非法",
                        details={"field": "format", "reason": "illegal_coordinate"},
                    )
                coordinates[name] = _bounded_coord_list(values)

            statistics: dict[str, dict[str, float | int]] = {}
            profile_warnings: list[str] = []
            for name in data_vars:
                variable = ds.variables[name]
                n_elem = 1
                for size in getattr(variable, "shape", ()):
                    n_elem *= int(size)
                if n_elem <= 0:
                    raise climate_error(
                        "CLIMATE_DATA_INVALID",
                        "数据变量为空",
                        details={"field": "format", "reason": "missing_variable"},
                    )
                if n_elem > MAX_STATS_ELEMENTS:
                    if len(profile_warnings) < _PROFILE_WARNING_LIMIT:
                        profile_warnings.append("statistics_skipped")
                    continue
                values = _flatten_floats(variable[:], limit=MAX_STATS_ELEMENTS)
                if not values:
                    raise climate_error(
                        "CLIMATE_DATA_INVALID",
                        "数据变量为空",
                        details={"field": "format", "reason": "missing_variable"},
                    )
                statistics[name] = _json_stats(values)

            return {
                "format": "netcdf",
                "data_model": str(ds.data_model),
                "variables": data_vars,
                "dimensions": dimensions,
                "coordinates": coordinates,
                "statistics": statistics,
                "warnings": profile_warnings[:_PROFILE_WARNING_LIMIT],
            }
        except ClimateError:
            raise
        except Exception:
            raise _parser_error("netcdf") from None

    def bounded_values(self, name: str, *, limit: int) -> tuple[float, ...]:
        ds = self._ds
        if ds is None or name not in ds.variables:
            raise climate_error(
                "CLIMATE_DATA_INVALID",
                "目标变量不存在",
                details={"field": "y", "reason": "missing_variable"},
            )
        values = _flatten_floats(ds.variables[name][:], limit=limit)
        if not values:
            raise climate_error(
                "CLIMATE_DATA_INVALID",
                "没有可用于绘图的数值",
                details={"field": "y", "reason": "missing_variable"},
            )
        return tuple(values)


class GribReaderAdapter(ScientificReader):
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: Any = None
        self._gid: Any = None
        self._eccodes: Any = None

    def __enter__(self) -> GribReaderAdapter:
        if not formats_mod.eccodes_available():
            raise _missing("eccodes")
        try:
            import eccodes
        except (ImportError, RuntimeError, OSError):
            raise _missing("eccodes") from None

        self._eccodes = eccodes
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._fh = self._path.open("rb")
                self._gid = eccodes.codes_grib_new_from_file(self._fh)
        except ClimateError:
            raise
        except Exception:
            self._release()
            raise _parser_error("grib") from None
        if self._gid is None:
            self._release()
            raise _parser_error("grib")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self._release()

    def _release(self) -> None:
        if self._gid is not None and self._eccodes is not None:
            try:
                self._eccodes.codes_release(self._gid)
            except Exception:
                pass
            self._gid = None
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def bounded_profile(self) -> dict[str, Any]:
        eccodes = self._eccodes
        gid = self._gid
        if eccodes is None or gid is None:
            raise _parser_error("grib")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                short_name = str(eccodes.codes_get(gid, "shortName"))
                ni = int(eccodes.codes_get(gid, "Ni"))
                nj = int(eccodes.codes_get(gid, "Nj"))
                lat_first = float(eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees"))
                lon_first = float(eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees"))
                lat_last = float(eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees"))
                lon_last = float(eccodes.codes_get(gid, "longitudeOfLastGridPointInDegrees"))
                data_date = str(eccodes.codes_get(gid, "dataDate"))
                data_time = str(eccodes.codes_get(gid, "dataTime")).zfill(4)
                raw_values = eccodes.codes_get_values(gid)
        except ClimateError:
            raise
        except Exception:
            raise _parser_error("grib") from None
        if ni <= 0 or nj <= 0:
            raise climate_error(
                "CLIMATE_DATA_INVALID",
                "GRIB 网格为空",
                details={"field": "format", "reason": "empty_time"},
            )
        for lat in (lat_first, lat_last):
            if lat < -90.0 or lat > 90.0:
                raise climate_error(
                    "CLIMATE_DATA_INVALID",
                    "纬度坐标非法",
                    details={"field": "format", "reason": "illegal_coordinate"},
                )
        values = _flatten_floats(raw_values, limit=MAX_STATS_ELEMENTS)
        if not values:
            raise climate_error(
                "CLIMATE_DATA_INVALID",
                "缺少数据变量",
                details={"field": "format", "reason": "missing_variable"},
            )
        return {
            "format": "grib",
            "variables": [short_name],
            "dimensions": {"latitude": nj, "longitude": ni},
            "coordinates": {
                "latitude": [lat_first, lat_last],
                "longitude": [lon_first, lon_last],
                "time": [f"{data_date}T{data_time}"],
            },
            "statistics": {short_name: _json_stats(values)},
            "warnings": [],
        }

    def bounded_values(self, name: str, *, limit: int) -> tuple[float, ...]:
        eccodes = self._eccodes
        gid = self._gid
        if eccodes is None or gid is None:
            raise _parser_error("grib")
        try:
            short_name = str(eccodes.codes_get(gid, "shortName"))
            raw_values = eccodes.codes_get_values(gid)
        except Exception:
            raise _parser_error("grib") from None
        if name != short_name:
            raise climate_error(
                "CLIMATE_DATA_INVALID",
                "目标变量不存在",
                details={"field": "y", "reason": "missing_variable"},
            )
        values = _flatten_floats(raw_values, limit=limit)
        if not values:
            raise climate_error(
                "CLIMATE_DATA_INVALID",
                "没有可用于绘图的数值",
                details={"field": "y", "reason": "missing_variable"},
            )
        return tuple(values)
