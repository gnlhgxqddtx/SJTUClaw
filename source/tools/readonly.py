"""
默认只读 tool：current_time / list_dir / read_file。

这些 tool 的 safety_level 都是 read_only：只读取外部环境，不修改任何内容，直接执行、无需 approval。

路径解析（Step 8）：
- 若当前 session 已设置 workspace，则相对路径按 workspace 解析并强制边界（禁止绝对路径 / ../ 越界）；
- 若未设置 workspace，则退回按当前工作目录解析（保持 Step 5 行为，使无 workspace 时仍可浏览项目）。
"""

import os
from datetime import datetime
from pathlib import Path

from ..workspace import WorkspaceError, resolve_path
from .base import SAFETY_READ_ONLY, Tool, ToolResult

# read_file 单次最多返回的字符数，超出则截断，避免一次性塞入过长上下文。
READ_FILE_MAX_CHARS = int(os.getenv("READ_FILE_MAX_CHARS", "20000"))
# list_dir 单次最多列出的条目数。
LIST_DIR_MAX_ENTRIES = int(os.getenv("LIST_DIR_MAX_ENTRIES", "500"))


def _resolve_readonly(ctx, path):
    """只读 tool 的路径解析：有 workspace 时按 workspace 解析并强制边界；
    无 workspace 时退回按当前工作目录解析。返回 (Path 或 None, 错误信息 或 None)。"""
    if ctx is not None and getattr(ctx, "workspace", None):
        try:
            return resolve_path(ctx.workspace, path), None
        except WorkspaceError as e:
            return None, str(e)
    return Path(str(path)), None


def _current_time(args: dict, ctx=None) -> ToolResult:
    fmt = args.get("format") or "%Y-%m-%d %H:%M:%S"
    try:
        text = datetime.now().strftime(fmt)
    except (ValueError, TypeError) as e:
        return ToolResult(ok=False, tool="current_time", error=f"时间格式非法: {e}")
    return ToolResult(ok=True, tool="current_time", output=text)


def _list_dir(args: dict, ctx=None) -> ToolResult:
    raw_path = str(args.get("path", "."))
    p, err = _resolve_readonly(ctx, raw_path)
    if err is not None:
        return ToolResult(ok=False, tool="list_dir", error=err)
    if not p.exists():
        return ToolResult(ok=False, tool="list_dir", error=f"路径不存在: {raw_path}")
    if not p.is_dir():
        return ToolResult(ok=False, tool="list_dir", error=f"不是目录: {raw_path}")
    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except OSError as e:
        return ToolResult(ok=False, tool="list_dir", error=f"读取目录失败: {e}")

    lines = []
    for entry in entries[:LIST_DIR_MAX_ENTRIES]:
        kind = "dir " if entry.is_dir() else "file"
        lines.append(f"[{kind}] {entry.name}")
    truncated = len(entries) > LIST_DIR_MAX_ENTRIES
    output = f"目录 {raw_path} 共 {len(entries)} 项：\n" + "\n".join(lines)
    if truncated:
        output += f"\n...（已截断，仅显示前 {LIST_DIR_MAX_ENTRIES} 项）"
    return ToolResult(ok=True, tool="list_dir", output=output,
                      extra={"count": len(entries), "truncated": truncated})


def _read_file(args: dict, ctx=None) -> ToolResult:
    raw_path = str(args.get("path", ""))
    if not raw_path:
        return ToolResult(ok=False, tool="read_file", error="缺少参数 path")
    p, err = _resolve_readonly(ctx, raw_path)
    if err is not None:
        return ToolResult(ok=False, tool="read_file", error=err)
    if not p.exists():
        return ToolResult(ok=False, tool="read_file", error=f"文件不存在: {raw_path}")
    if p.is_dir():
        return ToolResult(ok=False, tool="read_file", error=f"这是目录而非文件: {raw_path}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(ok=False, tool="read_file", error=f"读取文件失败: {e}")

    truncated = len(text) > READ_FILE_MAX_CHARS
    if truncated:
        text = text[:READ_FILE_MAX_CHARS] + f"\n...（文件过大，已截断至前 {READ_FILE_MAX_CHARS} 字符）"
    return ToolResult(ok=True, tool="read_file", output=text,
                      extra={"path": raw_path, "truncated": truncated})


def register_readonly_tools(registry) -> None:
    """向 registry 注册默认只读 tool。"""
    registry.register(Tool(
        name="current_time",
        description="获取服务器当前的日期与时间。当用户询问现在几点、今天日期等信息时使用。",
        input_schema={
            "type": "object",
            "properties": {
                "format": {"type": "string", "description": "可选，strftime 格式，默认 %Y-%m-%d %H:%M:%S"},
            },
            "required": [],
        },
        handler=_current_time,
        safety_level=SAFETY_READ_ONLY,
    ))
    registry.register(Tool(
        name="list_dir",
        description="列出指定目录下的文件与子目录。当用户想了解项目结构、当前目录内容时使用。"
                    "已设置 workspace 时路径按 workspace 解析。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径（相对 workspace），默认当前目录 '.'"},
            },
            "required": [],
        },
        handler=_list_dir,
        safety_level=SAFETY_READ_ONLY,
    ))
    registry.register(Tool(
        name="read_file",
        description="读取指定文本文件的内容（过大时会被截断）。当需要查看 README、源码或文档内容时使用。"
                    "已设置 workspace 时路径按 workspace 解析。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径（相对 workspace）"},
            },
            "required": ["path"],
        },
        handler=_read_file,
        safety_level=SAFETY_READ_ONLY,
    ))
