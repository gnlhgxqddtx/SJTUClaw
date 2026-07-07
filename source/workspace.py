"""
DD-SJTUClaw Workspace 边界（Step 8）。

Workspace 是当前 agent 可以操作的项目根目录，也是文件读写、shell 命令、附件拷贝、
下载入口创建的默认边界。所有相对路径都按 workspace 解析，并强制不允许通过
绝对路径或 ../ 越界到 workspace 之外。

仅使用 Python 标准库。
"""

from pathlib import Path


class WorkspaceError(Exception):
    """workspace 边界相关错误（未设置 / 越界 / 目录不存在等）。"""


def normalize_workspace(path) -> str:
    """把用户提供的 workspace 路径规范为绝对路径并校验它是一个存在的目录。
    用于“设置 workspace”时校验输入；返回解析后的绝对路径字符串。"""
    raw = str(path or "").strip()
    if not raw:
        raise WorkspaceError("workspace 路径不能为空")
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        raise WorkspaceError(f"无法解析路径: {raw} -> {e}")
    if not p.exists():
        raise WorkspaceError(f"目录不存在: {p}")
    if not p.is_dir():
        raise WorkspaceError(f"不是目录: {p}")
    return str(p)


def resolve_path(workspace, rel_path) -> Path:
    """把 workspace 内的相对路径解析为绝对 Path，并强制 workspace 边界。

    - workspace 未设置 -> WorkspaceError
    - workspace 不是已存在目录 -> WorkspaceError
    - rel_path 为空 -> WorkspaceError
    - rel_path 为绝对路径（含 Windows 盘符/UNC）-> WorkspaceError
    - 解析后（含 ../）落在 workspace 之外 -> WorkspaceError

    返回解析后的 Path（位于 workspace 内或即为 workspace 本身），不保证目标已存在。
    """
    if not workspace:
        raise WorkspaceError("workspace 未设置，无法解析路径。请先设置 workspace。")
    base = Path(workspace).expanduser()
    try:
        base = base.resolve()
    except OSError as e:
        raise WorkspaceError(f"workspace 路径无法解析: {workspace} -> {e}")
    if not base.exists() or not base.is_dir():
        raise WorkspaceError(f"workspace 目录不存在或不是目录: {workspace}")

    raw = str(rel_path if rel_path is not None else "").strip()
    if not raw:
        raise WorkspaceError("路径不能为空")
    p = Path(raw)
    if p.is_absolute() or p.drive or p.anchor:
        raise WorkspaceError(f"不允许使用绝对路径: {raw}（只能使用 workspace 内的相对路径）")

    try:
        target = (base / p).resolve()
    except OSError as e:
        raise WorkspaceError(f"路径无法解析: {raw} -> {e}")

    if target != base and base not in target.parents:
        raise WorkspaceError(f"路径越界，超出 workspace 边界: {raw}")
    return target


def is_within(workspace, path) -> bool:
    """判断某个（可能已存在的）路径是否位于 workspace 内（含等于 workspace 本身）。
    供 shell tool 在执行前后校验 cwd 是否仍在 workspace 内使用。"""
    if not workspace:
        return False
    try:
        base = Path(workspace).resolve()
        target = Path(path).resolve()
    except OSError:
        return False
    return target == base or base in target.parents
