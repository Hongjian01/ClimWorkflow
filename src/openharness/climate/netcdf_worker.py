"""独立进程解析 NetCDF，避免 TUI 进程里 HDF5 占住 GIL 导致 inspect 无法超时。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _emit(payload: dict[str, Any]) -> None:
    """始终写 UTF-8 JSON。Windows 默认 GBK 会让父进程 decode('utf-8') 失败。"""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"
    os.environ["CLIMATE_NETCDF_INPROCESS"] = "1"
    os.environ["PYTHONUTF8"] = "1"
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("usage: netcdf_worker profile|values <path> [y_name] [limit]", file=sys.stderr)
        return 2
    action, raw_path = args[0], args[1]
    path = Path(raw_path)
    from openharness.climate.errors import ClimateError
    from openharness.climate.readers import read_plot_values, read_scientific_profile

    try:
        if action == "profile":
            payload = read_scientific_profile(path, "netcdf")
        elif action == "values":
            if len(args) < 4:
                print("values 需要 y_name 与 limit", file=sys.stderr)
                return 2
            values = read_plot_values(path, "netcdf", args[2], limit=int(args[3]))
            payload = {"values": list(values)}
        else:
            print("unknown action", file=sys.stderr)
            return 2
    except ClimateError as exc:
        _emit(
            {
                "__climate_error__": True,
                "code": exc.code,
                "message": exc.message,
                "details": dict(exc.details),
            }
        )
        return 1
    except Exception:
        _emit(
            {
                "__climate_error__": True,
                "code": "CLIMATE_DATA_INVALID",
                "message": "NetCDF 解析失败，可能已截断或损坏",
                "details": {"reason": "parser_rejected"},
            }
        )
        return 1
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
