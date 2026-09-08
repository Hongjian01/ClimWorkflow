"""Climate 安全路径解析与写入区校验。"""

from __future__ import annotations

import os
import re
import stat
import sys
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

from openharness.climate.errors import ClimateError, climate_error

# Windows 保留设备名（含扩展名变体，如 CON.txt）
_RESERVED_DEVICE_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_DRIVE_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_DRIVE_REL = re.compile(r"^[A-Za-z]:(?![\\/])")
_UNC = re.compile(r"^(\\\\|//)")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# STATE 写入区允许的相对路径模式（SPEC §5.1）
_STATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\.climate/index\.json$"),
    re.compile(
        r"^\.climate/runs/"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
        r"context\.json$"
    ),
    re.compile(r"^\.climate/locks/workspace\.lock$"),
    re.compile(
        r"^\.climate/locks/"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.lock$"
    ),
    re.compile(
        r"^\.climate/transactions/active-run-"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$"
    ),
    re.compile(
        r"^\.climate/backups/"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        r"-context-v\d+-.+\.json$"
    ),
)


class WriteZone(str, Enum):
    """Climate 内部允许的写入区域。"""

    DATA = "data"
    OUTPUT = "output"
    STATE = "state"
    KNOWLEDGE = "knowledge"


def knowledge_dir(workspace: Path) -> Path:
    """知识索引根目录（workspace 相对 `.climate/knowledge`）。"""
    return resolve_workspace_path(workspace, ".climate/knowledge")


def resolve_workspace_path(workspace: Path, relative: str) -> Path:
    """将 workspace 相对路径解析为位于边界内的绝对 Path。

    先做词法拒绝，再沿已存在父链做真实路径校验。
    """
    _validate_lexical(relative, workspace=workspace)
    try:
        root = workspace.resolve()
    except OSError as exc:
        raise _invalid_path("无法解析 workspace 真实路径", workspace=workspace) from exc

    parts = relative.split("/")
    current = root
    for index, part in enumerate(parts):
        candidate = current / part
        try:
            exists = candidate.exists() or candidate.is_symlink()
        except OSError as exc:
            raise _invalid_path("无法验证路径存在性", path=relative, workspace=workspace) from exc

        if exists:
            try:
                resolved = candidate.resolve()
            except OSError as exc:
                raise _invalid_path(
                    "无法可靠验证真实路径",
                    path=relative,
                    workspace=workspace,
                ) from exc
            if not _is_within_root(resolved, root):
                raise _invalid_path("路径逃逸 workspace", path=relative, workspace=workspace)
            current = resolved
            continue

        # 剩余段尚不存在：在已校验父目录上词法拼接，并尝试 resolve(strict=False)
        remainder = parts[index:]
        final = current.joinpath(*remainder)
        try:
            final_resolved = final.resolve(strict=False)
        except OSError as exc:
            raise _invalid_path(
                "无法可靠验证真实路径",
                path=relative,
                workspace=workspace,
            ) from exc
        if not _is_within_root(final_resolved, root):
            raise _invalid_path("路径逃逸 workspace", path=relative, workspace=workspace)
        return final_resolved

    return current


def validate_local_source_file(workspace: Path, relative: str) -> Path:
    """PATH-004：local 源必须是 workspace 内普通文件，拒绝目录/设备/FIFO/socket/链接。"""
    resolved = resolve_workspace_path(workspace, relative)
    raw = workspace.joinpath(*relative.split("/"))
    try:
        info = os.lstat(raw)
    except OSError as exc:
        raise _invalid_path(
            "无法读取 local 源文件类型",
            path=relative,
            workspace=workspace,
        ) from exc

    mode = info.st_mode
    if stat.S_ISLNK(mode):
        raise _invalid_path("local 源不得为 symlink", path=relative, workspace=workspace)
    if sys.platform == "win32":
        attrs = int(getattr(info, "st_file_attributes", 0) or 0)
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise _invalid_path(
                "local 源不得为 junction/reparse",
                path=relative,
                workspace=workspace,
            )
    if stat.S_ISDIR(mode):
        raise _invalid_path("local 源不得为目录", path=relative, workspace=workspace)
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        raise _invalid_path("local 源不得为设备文件", path=relative, workspace=workspace)
    if stat.S_ISFIFO(mode):
        raise _invalid_path("local 源不得为 FIFO", path=relative, workspace=workspace)
    if stat.S_ISSOCK(mode):
        raise _invalid_path("local 源不得为 socket", path=relative, workspace=workspace)
    if not stat.S_ISREG(mode):
        raise _invalid_path("local 源必须是普通文件", path=relative, workspace=workspace)
    return resolved


def to_workspace_relative_posix(workspace: Path, absolute: Path) -> str:
    """将绝对路径转为 workspace 相对 POSIX 路径；失败则抛 CLIMATE_INVALID_PATH。"""
    try:
        root = workspace.resolve()
        target = absolute.resolve()
    except OSError as exc:
        raise _invalid_path("无法解析路径", workspace=workspace) from exc
    if not _is_within_root(target, root):
        raise _invalid_path("路径逃逸 workspace", workspace=workspace)
    rel = target.relative_to(root)
    return rel.as_posix()


def validate_write_zone(
    workspace: Path,
    absolute: Path,
    zone: WriteZone,
    *,
    run_id: str | None = None,
) -> None:
    """校验目标绝对路径落在指定写入区；否则抛 CLIMATE_INVALID_PATH。"""
    relative = to_workspace_relative_posix(workspace, absolute)

    if zone is WriteZone.DATA:
        if not run_id:
            raise _invalid_path("data 写入区需要 run_id", path=relative, workspace=workspace)
        prefix = f".climate/data/{run_id}/"
        if not relative.startswith(prefix):
            raise _invalid_path(
                "路径不在 acquisition data 写入区",
                path=relative,
                zone=zone.value,
                workspace=workspace,
            )
        return

    if zone is WriteZone.OUTPUT:
        if not run_id:
            raise _invalid_path("output 写入区需要 run_id", path=relative, workspace=workspace)
        prefix = f".climate/output/{run_id}/"
        if not relative.startswith(prefix):
            raise _invalid_path(
                "路径不在 plot/report output 写入区",
                path=relative,
                zone=zone.value,
                workspace=workspace,
            )
        return

    if zone is WriteZone.STATE:
        if any(pattern.fullmatch(relative) for pattern in _STATE_PATTERNS):
            return
        raise _invalid_path(
            "路径不在固定 Context 状态写入区",
            path=relative,
            zone=zone.value,
            workspace=workspace,
        )

    if zone is WriteZone.KNOWLEDGE:
        prefix = ".climate/knowledge/"
        if not relative.startswith(prefix) or relative == prefix:
            raise _invalid_path(
                "路径不在 knowledge 索引写入区",
                path=relative,
                zone=zone.value,
                workspace=workspace,
            )
        return

    raise _invalid_path("未知写入区", path=relative, workspace=workspace)


def _validate_lexical(relative: str, *, workspace: Path) -> None:
    if not isinstance(relative, str) or relative.strip() == "":
        raise _invalid_path("路径不能为空", workspace=workspace)
    if relative != relative.strip():
        raise _invalid_path("路径含首尾空白", path=relative, workspace=workspace)
    if "\x00" in relative:
        raise _invalid_path("路径含 NUL", workspace=workspace)
    if "\\" in relative:
        raise _invalid_path("路径不得使用反斜杠或混合分隔符", path=relative, workspace=workspace)
    if relative.startswith("~"):
        raise _invalid_path("路径不得使用 ~", path=relative, workspace=workspace)
    if _UNC.match(relative):
        raise _invalid_path("路径不得为 UNC", path=relative, workspace=workspace)
    if _DRIVE_ABS.match(relative) or _DRIVE_REL.match(relative):
        raise _invalid_path("路径不得为盘符绝对或 drive-relative", path=relative, workspace=workspace)
    if relative.startswith("/"):
        raise _invalid_path("路径不得为绝对路径", path=relative, workspace=workspace)

    # 额外用 pathlib 捕获平台绝对路径形态
    if PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).is_absolute():
        raise _invalid_path("路径不得为绝对路径", path=relative, workspace=workspace)

    parts = relative.split("/")
    if any(part == "" for part in parts):
        raise _invalid_path("路径含空段", path=relative, workspace=workspace)
    if any(part in {".", ".."} for part in parts):
        raise _invalid_path("路径含 . 或 .. 段", path=relative, workspace=workspace)
    for part in parts:
        # CON.txt → 取主名 CON；拒绝纯保留名及其 .ext 变体
        name_for_device = part.split(".", 1)[0].upper() if "." in part else part.upper()
        if name_for_device in _RESERVED_DEVICE_NAMES:
            raise _invalid_path(
                "路径含 Windows 保留设备名",
                path=relative,
                workspace=workspace,
            )


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _invalid_path(
    message: str,
    *,
    workspace: Path | None = None,
    path: str | None = None,
    zone: str | None = None,
) -> ClimateError:
    details: dict[str, str] = {}
    if path is not None:
        details["path"] = path
    if zone is not None:
        details["zone"] = zone
    return climate_error(
        "CLIMATE_INVALID_PATH",
        message,
        details=details,
        workspace=workspace,
    )
