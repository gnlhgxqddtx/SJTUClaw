"""
Advanced tool（Step 8）：在 workspace 内进行真实操作的高级 tool。

三类能力：
- Update tool（safety=write，需 approval）：create_file / overwrite_file / edit_file —— 在 workspace 内创建或修改文件。
- Shell tool（safety=shell，需 approval）：new_shell / run_command —— 启动 shell 并在 workspace 内执行命令。
- Download tool（safety=download，无需显式 approval）：create_download —— 为 workspace 内文件注册下载入口。

外加 copy_attachment_to_workspace（safety=write，需 approval）：把当前 session 绑定的附件拷贝进 workspace。

所有写文件 / 命令 / 拷贝 / 下载操作都要求已设置 workspace，且路径按 workspace 解析、禁止越界。
handler 统一签名为 handler(args, ctx)，ctx 为运行期 ToolContext。
"""

from pathlib import Path

from ..downloads import DownloadError
from ..shell import ShellError
from ..workspace import WorkspaceError, resolve_path
from .base import (
    SAFETY_DOWNLOAD,
    SAFETY_SHELL,
    SAFETY_WRITE,
    Tool,
    ToolResult,
)

# create_file / overwrite_file 单次写入内容的字符上限，避免异常巨大的写入。
WRITE_FILE_MAX_CHARS = 200000


def _need_workspace(ctx, tool):
    """校验 ctx 存在且已设置 workspace；未满足返回 (None, 失败结果)。"""
    if ctx is None or not getattr(ctx, "workspace", None):
        return None, ToolResult(ok=False, tool=tool,
                                error="workspace 未设置，无法执行该操作。请先设置 workspace。")
    return ctx.workspace, None


def _resolve(ctx, tool, path):
    """在 workspace 内解析路径；越界 / 未设置返回 (None, 失败结果)。"""
    ws, fail = _need_workspace(ctx, tool)
    if fail is not None:
        return None, fail
    try:
        return resolve_path(ws, path), None
    except WorkspaceError as e:
        return None, ToolResult(ok=False, tool=tool, error=str(e))


# ---------- Update tool ----------
def _create_file(args, ctx=None) -> ToolResult:
    path = str(args.get("path", ""))
    content = args.get("content", "")
    if not isinstance(content, str):
        return ToolResult(ok=False, tool="create_file", error="content 必须是字符串")
    if len(content) > WRITE_FILE_MAX_CHARS:
        return ToolResult(ok=False, tool="create_file",
                          error=f"content 过大（{len(content)} 字符），上限 {WRITE_FILE_MAX_CHARS}")
    target, fail = _resolve(ctx, "create_file", path)
    if fail is not None:
        return fail
    if target.exists():
        return ToolResult(ok=False, tool="create_file",
                          error=f"文件已存在: {path}（如需覆盖请用 overwrite_file）")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return ToolResult(ok=False, tool="create_file", error=f"创建文件失败: {e}")
    return ToolResult(ok=True, tool="create_file",
                      output=f"已创建文件 {path}（{len(content)} 字符）",
                      extra={"path": path, "bytes": len(content.encode('utf-8'))})


def _overwrite_file(args, ctx=None) -> ToolResult:
    path = str(args.get("path", ""))
    content = args.get("content", "")
    if not isinstance(content, str):
        return ToolResult(ok=False, tool="overwrite_file", error="content 必须是字符串")
    if len(content) > WRITE_FILE_MAX_CHARS:
        return ToolResult(ok=False, tool="overwrite_file",
                          error=f"content 过大（{len(content)} 字符），上限 {WRITE_FILE_MAX_CHARS}")
    target, fail = _resolve(ctx, "overwrite_file", path)
    if fail is not None:
        return fail
    if target.exists() and target.is_dir():
        return ToolResult(ok=False, tool="overwrite_file", error=f"这是目录而非文件: {path}")
    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return ToolResult(ok=False, tool="overwrite_file", error=f"写入文件失败: {e}")
    verb = "已覆盖" if existed else "已创建"
    return ToolResult(ok=True, tool="overwrite_file",
                      output=f"{verb}文件 {path}（{len(content)} 字符）",
                      extra={"path": path, "existed": existed})


def _edit_file(args, ctx=None) -> ToolResult:
    path = str(args.get("path", ""))
    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not old:
        return ToolResult(ok=False, tool="edit_file", error="old_string 必须是非空字符串")
    if not isinstance(new, str):
        return ToolResult(ok=False, tool="edit_file", error="new_string 必须是字符串")
    target, fail = _resolve(ctx, "edit_file", path)
    if fail is not None:
        return fail
    if not target.exists():
        return ToolResult(ok=False, tool="edit_file", error=f"文件不存在: {path}")
    if target.is_dir():
        return ToolResult(ok=False, tool="edit_file", error=f"这是目录而非文件: {path}")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as e:
        return ToolResult(ok=False, tool="edit_file", error=f"读取文件失败: {e}")
    count = text.count(old)
    if count == 0:
        return ToolResult(ok=False, tool="edit_file",
                          error="在文件中未找到 old_string，无法定位要修改的内容。")
    if count > 1:
        return ToolResult(ok=False, tool="edit_file",
                          error=f"old_string 在文件中出现 {count} 次，无法唯一定位。请提供更长的上下文使其唯一。")
    updated = text.replace(old, new, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as e:
        return ToolResult(ok=False, tool="edit_file", error=f"写入文件失败: {e}")
    return ToolResult(ok=True, tool="edit_file",
                      output=f"已更新文件 {path}（替换 1 处）",
                      extra={"path": path})


def _copy_attachment(args, ctx=None) -> ToolResult:
    """把当前 session 绑定的附件拷贝到 workspace 内指定路径。"""
    att_id = str(args.get("attachmentId", ""))
    dest = str(args.get("destPath", ""))
    if not att_id:
        return ToolResult(ok=False, tool="copy_attachment_to_workspace", error="缺少参数 attachmentId")
    if not dest:
        return ToolResult(ok=False, tool="copy_attachment_to_workspace", error="缺少参数 destPath")
    ws, fail = _need_workspace(ctx, "copy_attachment_to_workspace")
    if fail is not None:
        return fail
    store = getattr(ctx, "attachment_store", None)
    session = getattr(ctx, "session", None)
    if store is None or session is None:
        return ToolResult(ok=False, tool="copy_attachment_to_workspace",
                          error="附件功能不可用（缺少附件存储或 session）。")
    # 只能访问当前 session 绑定的附件（session 隔离由 store.path_for 基于本 session 保证）。
    src = store.path_for(session, att_id)
    if src is None or not Path(src).exists():
        return ToolResult(ok=False, tool="copy_attachment_to_workspace",
                          error=f"当前 session 未找到附件: {att_id}")
    target, fail = _resolve(ctx, "copy_attachment_to_workspace", dest)
    if fail is not None:
        return fail
    if target.exists() and target.is_dir():
        return ToolResult(ok=False, tool="copy_attachment_to_workspace",
                          error=f"目标是已存在的目录: {dest}")
    try:
        data = Path(src).read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        return ToolResult(ok=False, tool="copy_attachment_to_workspace", error=f"拷贝附件失败: {e}")
    return ToolResult(ok=True, tool="copy_attachment_to_workspace",
                      output=f"已将附件 {att_id} 拷贝到 workspace 内 {dest}（{len(data)} 字节）",
                      extra={"attachmentId": att_id, "destPath": dest, "bytes": len(data)})


# ---------- Shell tool ----------
def _new_shell(args, ctx=None) -> ToolResult:
    ws, fail = _need_workspace(ctx, "new_shell")
    if fail is not None:
        return fail
    mgr = getattr(ctx, "shell_manager", None)
    session = getattr(ctx, "session", None)
    if mgr is None or session is None:
        return ToolResult(ok=False, tool="new_shell", error="shell 功能不可用。")
    cwd = ws
    sub = args.get("path")
    if sub:
        target, f2 = _resolve(ctx, "new_shell", sub)
        if f2 is not None:
            return f2
        if not target.exists() or not target.is_dir():
            return ToolResult(ok=False, tool="new_shell", error=f"初始目录不存在或不是目录: {sub}")
        cwd = str(target)
    shell = mgr.new_shell(session.session_id, ws, cwd=cwd)
    return ToolResult(ok=True, tool="new_shell",
                      output=f"已启动新 shell（旧 shell 若存在已退出），初始工作目录: {shell.cwd}",
                      extra={"cwd": shell.cwd})


def _run_command(args, ctx=None) -> ToolResult:
    command = str(args.get("command", ""))
    if not command.strip():
        return ToolResult(ok=False, tool="run_command", error="缺少参数 command")
    ws, fail = _need_workspace(ctx, "run_command")
    if fail is not None:
        return fail
    mgr = getattr(ctx, "shell_manager", None)
    session = getattr(ctx, "session", None)
    if mgr is None or session is None:
        return ToolResult(ok=False, tool="run_command", error="shell 功能不可用。")
    shell = mgr.get_shell(session.session_id)
    if shell is None:
        return ToolResult(ok=False, tool="run_command",
                          error="当前没有活动的 shell，请先调用 new_shell。")
    try:
        r = shell.run(command)
    except ShellError as e:
        return ToolResult(ok=False, tool="run_command", error=str(e))

    success = (r["returncode"] == 0) and (not r["timedOut"]) and (not r["leftWorkspace"])
    lines = [
        f"命令: {r['command']}",
        f"cwd: {r['cwd']}",
        f"退出码: {r['returncode']}",
        f"成功: {'是' if success else '否'}",
        f"超时: {'是' if r['timedOut'] else '否'}",
        f"输出截断: {'是' if r['truncated'] else '否'}",
    ]
    if r["leftWorkspace"]:
        lines.append("注意: 命令使 shell 离开了 workspace，该 shell 已被终止。")
    lines.append("----- stdout -----")
    lines.append(r["stdout"] if r["stdout"] else "(空)")
    lines.append("----- stderr -----")
    lines.append(r["stderr"] if r["stderr"] else "(空)")
    return ToolResult(ok=True, tool="run_command", output="\n".join(lines), extra=r)


# ---------- Download tool ----------
def _create_download(args, ctx=None) -> ToolResult:
    path = str(args.get("path", ""))
    ws, fail = _need_workspace(ctx, "create_download")
    if fail is not None:
        return fail
    registry = getattr(ctx, "download_registry", None)
    if registry is None:
        return ToolResult(ok=False, tool="create_download",
                          error="下载功能未启用（当前入口不支持通过 Gateway 下载）。")
    target, f2 = _resolve(ctx, "create_download", path)
    if f2 is not None:
        return f2
    if not target.exists() or not target.is_file():
        return ToolResult(ok=False, tool="create_download", error=f"文件不存在: {path}")
    try:
        info = registry.register(str(target))
    except DownloadError as e:
        return ToolResult(ok=False, tool="create_download", error=str(e))
    return ToolResult(ok=True, tool="create_download",
                      output=f"已为 {path} 创建下载入口：downloadId={info['downloadId']}，"
                             f"downloadUrl={info['downloadUrl']}",
                      extra=info)


def register_advanced_tools(registry) -> None:
    """向 registry 注册全部高级 tool（update / shell / download / copy_attachment）。"""
    registry.register(Tool(
        name="create_file",
        description="在 workspace 内创建一个新文件（若文件已存在则失败，请改用 overwrite_file）。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "workspace 内的相对路径"},
                "content": {"type": "string", "description": "文件内容，默认空字符串"},
            },
            "required": ["path"],
        },
        handler=_create_file,
        safety_level=SAFETY_WRITE,
    ))
    registry.register(Tool(
        name="overwrite_file",
        description="用新内容覆盖 workspace 内已有文件（不存在则创建）。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "workspace 内的相对路径"},
                "content": {"type": "string", "description": "覆盖后的完整文件内容"},
            },
            "required": ["path", "content"],
        },
        handler=_overwrite_file,
        safety_level=SAFETY_WRITE,
    ))
    registry.register(Tool(
        name="edit_file",
        description="在 workspace 内已有文件中，把唯一出现的 old_string 替换为 new_string（用于局部修改）。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "workspace 内的相对路径"},
                "old_string": {"type": "string", "description": "要被替换的原文（需在文件中唯一）"},
                "new_string": {"type": "string", "description": "替换后的新内容"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=_edit_file,
        safety_level=SAFETY_WRITE,
    ))
    registry.register(Tool(
        name="copy_attachment_to_workspace",
        description="把当前 session 上传的附件拷贝到 workspace 内指定路径，以便像普通文件一样读取或处理。",
        input_schema={
            "type": "object",
            "properties": {
                "attachmentId": {"type": "string", "description": "附件 id（当前 session 内，如 att_001）"},
                "destPath": {"type": "string", "description": "workspace 内的目标相对路径"},
            },
            "required": ["attachmentId", "destPath"],
        },
        handler=_copy_attachment,
        safety_level=SAFETY_WRITE,
    ))
    registry.register(Tool(
        name="new_shell",
        description="启动一个新的 shell（若已有 shell 则先退出）。初始工作目录为 workspace 或其内子目录。"
                    "执行命令前必须先调用它。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "可选，shell 初始子目录（相对 workspace），默认 workspace 根"},
            },
            "required": [],
        },
        handler=_new_shell,
        safety_level=SAFETY_SHELL,
    ))
    registry.register(Tool(
        name="run_command",
        description="在当前已启动的 shell 中执行一条命令。多次调用复用同一 shell，"
                    "前序命令的 cwd 与环境变量会影响后续命令。命令只能在 workspace 内执行。",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要在 shell 中执行的命令"},
            },
            "required": ["command"],
        },
        handler=_run_command,
        safety_level=SAFETY_SHELL,
    ))
    registry.register(Tool(
        name="create_download",
        description="为 workspace 内一个已有文件创建可通过 Gateway 下载的临时入口，"
                    "以便远端用户获取该文件。返回 downloadId / downloadUrl。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "workspace 内要提供下载的文件相对路径"},
            },
            "required": ["path"],
        },
        handler=_create_download,
        safety_level=SAFETY_DOWNLOAD,
    ))
