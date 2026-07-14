"""
DD-SJTUClaw 图形化界面入口（基于 PyGUIAdapter）

启动方式：python -m source.gui

PyGUIAdapter (0.3.12) 将函数自动转换为 GUI，通过 GUIAdapter.add() 注册功能。
"""

import sys
import threading
from datetime import datetime
from typing import Optional

from pyguiadapter import GUIAdapter

from .agent import build_runtime
from .approval import ApprovalManager
from .config import DEFAULT_MODEL, APPROVAL_TIMEOUT_SECONDS
from .memory_store import MemoryError
from .session_manager import SessionError
from .workspace import WorkspaceError, normalize_workspace


class GUIContext:
    def __init__(self):
        self.runtime = None
        self.approval_manager = None
        self.event_buffer = []
        self._event_lock = threading.Lock()

    def init_runtime(self):
        self.runtime = build_runtime()
        self.approval_manager = ApprovalManager(timeout=APPROVAL_TIMEOUT_SECONDS)
        return f"✅ 运行时初始化成功\n模型: {DEFAULT_MODEL}\n会话: {len(self.runtime.manager.sessions)} 个\n技能: {len(self.runtime.skill_registry.list())} 个"

    def clear_events(self):
        with self._event_lock:
            events = list(self.event_buffer)
            self.event_buffer = []
        return events


_gui_ctx = GUIContext()


def _event_collector(kind, data):
    with _gui_ctx._event_lock:
        _gui_ctx.event_buffer.append({
            "kind": kind,
            "time": datetime.now().strftime("%H:%M:%S"),
            "data": data,
        })


def _gui_approval(tool, args):
    if _gui_ctx.approval_manager is None:
        return False, "审批管理器未初始化"
    approval_id = _gui_ctx.approval_manager.create(
        _gui_ctx.runtime.manager.current_id, tool, args
    )["approvalId"]
    approved, reason = _gui_ctx.approval_manager.wait(approval_id)
    return approved, reason


# ---------- 会话管理 ----------

def list_sessions() -> str:
    """列出所有会话"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    sessions = _gui_ctx.runtime.manager.list_sorted()
    if not sessions:
        return "（暂无会话）"
    lines = []
    for s in sessions:
        marker = "⭐️ 当前" if s.session_id == _gui_ctx.runtime.manager.current_id else "   "
        lines.append(f"{marker} {s.session_id:<12} {s.title:<16} 消息数={len(s.messages)}")
    return "\n".join(lines)


def new_session(title: str = "新会话") -> str:
    """创建新会话"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    try:
        session = _gui_ctx.runtime.manager.new_session(title)
        return f"✅ 创建会话: {session.session_id}（{session.title}）"
    except Exception as e:
        return f"❌ 创建失败: {e}"


def switch_session(session_id: str) -> str:
    """切换会话（支持简写，如 001 或 1）"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    try:
        session = _gui_ctx.runtime.manager.switch(session_id)
        ws = getattr(session, "workspace", None)
        return f"✅ 已切换到: {session.session_id}（{session.title}）\nWorkspace: {ws or '未设置'}"
    except SessionError as e:
        return f"❌ {e}"


def delete_session(session_id: str) -> str:
    """删除会话"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    try:
        deleted = _gui_ctx.runtime.manager.delete(session_id)
        return f"✅ 已删除会话: {deleted}"
    except SessionError as e:
        return f"❌ {e}"


def rename_session(session_id: str, new_title: str) -> str:
    """重命名会话"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    try:
        session = _gui_ctx.runtime.manager.rename(session_id, new_title)
        return f"✅ 已重命名: {session.session_id} -> {session.title}"
    except SessionError as e:
        return f"❌ {e}"


# ---------- 长期记忆 ----------

def list_memories() -> str:
    """列出所有长期记忆"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    memories = _gui_ctx.runtime.memory_store.list()
    if not memories:
        return "（暂无长期记忆）"
    lines = []
    for m in memories:
        lines.append(f"{m['id']:<10} {m['content']}")
    return "\n".join(lines)


def add_memory(content: str) -> str:
    """添加长期记忆"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    if not content.strip():
        return "❌ 内容不能为空"
    try:
        item = _gui_ctx.runtime.memory_store.add(content)
        return f"✅ 添加记忆: {item['id']}"
    except MemoryError as e:
        return f"❌ {e}"


def delete_memory(memory_id: str) -> str:
    """删除长期记忆"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    try:
        deleted = _gui_ctx.runtime.memory_store.delete(memory_id)
        return f"✅ 已删除记忆: {deleted}"
    except MemoryError as e:
        return f"❌ {e}"


# ---------- Workspace ----------

def show_workspace() -> str:
    """查看当前会话的 workspace"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    session = _gui_ctx.runtime.manager.current
    ws = getattr(session, "workspace", None)
    return f"当前 workspace: {ws or '未设置'}\n\n⚠️ 高级工具（写文件/执行命令）需要先设置 workspace"


def set_workspace(path: str) -> str:
    """设置当前会话的 workspace 目录"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    if not path.strip():
        return "❌ 路径不能为空"
    try:
        ws = normalize_workspace(path)
        session = _gui_ctx.runtime.manager.current
        session.workspace = ws
        _gui_ctx.runtime.manager.save(session)
        return f"✅ workspace 已设置为: {ws}"
    except WorkspaceError as e:
        return f"❌ {e}"
    except SessionError as e:
        return f"❌ 保存失败: {e}"


# ---------- Skill ----------

def list_skills() -> str:
    """列出所有可用技能"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    skills = _gui_ctx.runtime.skill_registry.list() if _gui_ctx.runtime.skill_registry else []
    if not skills:
        return "（暂无可用技能）"
    lines = []
    for s in skills:
        lines.append(f"📦 {s['name']}")
        lines.append(f"   {s['description']}")
    return "\n".join(lines)


def show_skill(name: str) -> str:
    """查看某个技能的详细说明"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    if not _gui_ctx.runtime.skill_registry or not _gui_ctx.runtime.skill_registry.has(name):
        return f"❌ 不存在名为 '{name}' 的技能"
    try:
        skill = _gui_ctx.runtime.skill_registry.load(name)
        lines = [
            f"技能: {skill.name}",
            f"描述: {skill.description}",
            "-" * 40,
            skill.instructions or "（无正文）",
        ]
        if skill.resources:
            lines.append("-" * 40)
            lines.append("资源文件:")
            for rel in skill.resources:
                lines.append(f"  - {rel}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 加载失败: {e}"


def skill_usage() -> str:
    """查看当前会话的技能使用记录"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    session = _gui_ctx.runtime.manager.current
    usages = getattr(session, "skill_usages", [])
    if not usages:
        return "（本会话暂无技能使用记录）"
    lines = []
    for u in usages:
        src = u.get("source", "")
        reason = f" 理由={u['reason']}" if u.get("reason") else ""
        lines.append(f"{u.get('usedAt', '')} · {u.get('skill', '')}（{src}）{reason}")
        task = u.get("task", "")
        if task:
            lines.append(f"      任务: {task}")
    return "\n".join(lines)


# ---------- 聊天 ----------

def chat(message: str, skill_name: str = "") -> str:
    """与模型对话（支持显式使用技能）

    Args:
        message: 用户输入的消息
        skill_name: 可选，指定使用某个技能（免审批），留空则不使用技能
    """
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    if not message.strip():
        return "❌ 消息不能为空"

    _gui_ctx.clear_events()

    session = _gui_ctx.runtime.manager.current
    result = _gui_ctx.runtime.run(
        session,
        message.strip(),
        on_event=_event_collector,
        approval_fn=_gui_approval,
        skill_name=skill_name.strip() or None,
    )

    events = _gui_ctx.clear_events()
    trace_lines = []
    for ev in events:
        kind = ev["kind"]
        time = ev["time"]
        data = ev["data"]
        if kind == "tool_call":
            trace_lines.append(f"🔧 [{time}] {data['tool']} {data['args']}")
        elif kind == "approval":
            trace_lines.append(f"⚠️ [{time}] 需要审批: {data['tool']}")
        elif kind == "approval_result":
            verb = "✅ 批准" if data["approved"] else "❌ 拒绝"
            trace_lines.append(f"📋 [{time}] {data['tool']} {verb}")
        elif kind == "tool_result":
            status = "成功" if data["ok"] else "失败"
            text = data["output"] if data["ok"] else data["error"]
            preview = text if len(text) <= 500 else text[:500] + " ...(已截断)"
            trace_lines.append(f"📊 [{time}] {data['tool']} ({status}): {preview}")
        elif kind == "skill":
            src = "用户指定" if data.get("source") == "explicit" else "模型自主"
            trace_lines.append(f"🧩 [{time}] 已加载技能: {data['skill']}（{src}）")
        elif kind == "compaction":
            if data.get("applied"):
                trace_lines.append(f"📦 [{time}] 已压缩: 旧消息={data['old_count']}, 保留={data['recent_count']}")

    if trace_lines:
        return "\n".join(trace_lines) + "\n\n" + ("=" * 60) + "\n" + (result or "❌ 调用失败")
    else:
        return result or "❌ 调用失败"


# ---------- 压缩 ----------

def compact_session() -> str:
    """手动压缩当前会话"""
    if _gui_ctx.runtime is None:
        return "❌ 运行时未初始化，请先执行【初始化运行时】"
    from .compaction import compact
    from .config import COMPACT_RECENT_MESSAGES
    session = _gui_ctx.runtime.manager.current
    result = compact(_gui_ctx.runtime.client, session, COMPACT_RECENT_MESSAGES)
    if result["applied"]:
        try:
            _gui_ctx.runtime.manager.save(session)
        except SessionError as e:
            return f"⚠️ 压缩成功但保存失败: {e}"
        return f"✅ 压缩完成\n旧消息数: {result['old_count']}\n保留消息数: {result['recent_count']}\n\n摘要:\n{result['summary']}"
    else:
        return f"ℹ️ 无需压缩（{result.get('reason', '')}）"


# ---------- GUI 入口 ----------

def main():
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    adapter = GUIAdapter(
        app_style="Fusion",
        always_show_selection_window=True,
    )

    adapter.add(
        _gui_ctx.init_runtime,
        display_name="初始化运行时",
        display_document="启动前必须先执行此操作，初始化 LLM 客户端、会话管理器、工具注册表等。",
        window_title="DD-SJTUClaw - 初始化",
    )

    adapter.add(
        chat,
        display_name="聊天",
        display_document="与模型对话，支持工具调用和技能使用。高级工具（写文件/执行命令）需要先设置 workspace。",
        window_title="DD-SJTUClaw - 聊天",
    )

    adapter.add(
        list_sessions,
        display_name="列出会话",
        display_document="显示所有会话列表，⭐️ 标记为当前会话。",
        window_title="DD-SJTUClaw - 会话列表",
    )

    adapter.add(
        new_session,
        display_name="新建会话",
        display_document="创建一个新会话。",
        window_title="DD-SJTUClaw - 新建会话",
    )

    adapter.add(
        switch_session,
        display_name="切换会话",
        display_document="切换到指定会话，支持简写（如 001 或 1）。",
        window_title="DD-SJTUClaw - 切换会话",
    )

    adapter.add(
        delete_session,
        display_name="删除会话",
        display_document="删除指定会话。",
        window_title="DD-SJTUClaw - 删除会话",
    )

    adapter.add(
        rename_session,
        display_name="重命名会话",
        display_document="修改会话标题。",
        window_title="DD-SJTUClaw - 重命名会话",
    )

    adapter.add(
        list_memories,
        display_name="列出记忆",
        display_document="显示所有长期记忆。",
        window_title="DD-SJTUClaw - 长期记忆",
    )

    adapter.add(
        add_memory,
        display_name="添加记忆",
        display_document="添加一条跨会话共享的长期记忆。",
        window_title="DD-SJTUClaw - 添加记忆",
    )

    adapter.add(
        delete_memory,
        display_name="删除记忆",
        display_document="删除指定的长期记忆。",
        window_title="DD-SJTUClaw - 删除记忆",
    )

    adapter.add(
        show_workspace,
        display_name="查看 Workspace",
        display_document="查看当前会话的 workspace 设置。",
        window_title="DD-SJTUClaw - Workspace",
    )

    adapter.add(
        set_workspace,
        display_name="设置 Workspace",
        display_document="设置当前会话可操作的项目目录，高级工具的读写被限制在此目录内。",
        window_title="DD-SJTUClaw - 设置 Workspace",
    )

    adapter.add(
        list_skills,
        display_name="列出技能",
        display_document="显示所有可用技能及其描述。",
        window_title="DD-SJTUClaw - 技能列表",
    )

    adapter.add(
        show_skill,
        display_name="查看技能",
        display_document="查看某个技能的完整说明和资源文件。",
        window_title="DD-SJTUClaw - 技能详情",
    )

    adapter.add(
        skill_usage,
        display_name="技能使用记录",
        display_document="查看当前会话的技能使用历史。",
        window_title="DD-SJTUClaw - 技能使用记录",
    )

    adapter.add(
        compact_session,
        display_name="压缩会话",
        display_document="手动压缩当前会话的较早消息为摘要，保留最近消息。",
        window_title="DD-SJTUClaw - 压缩会话",
    )

    adapter.run()


if __name__ == "__main__":
    sys.exit(main())
