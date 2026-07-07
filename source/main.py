"""
DD-SJTUClaw 交互式命令行入口
支持多轮对话、多会话管理与本地 JSON 持久化。

启动: python -m source.main
"""

import json
import sys

from .agent import build_runtime
from .compaction import compact
from .config import COMPACT_RECENT_MESSAGES, DEFAULT_MODEL
from .memory_store import MemoryError
from .session_manager import SessionError
from .workspace import WorkspaceError, normalize_workspace

SEP = "=" * 60
LINE = "-" * 60


def _reconfigure_io():
    """将标准输入输出切换为 UTF-8，保证中文与 emoji 正常显示。"""
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def print_banner(model, session):
    print(SEP)
    print("🐾 DD-SJTUClaw - 智能对话助手")
    print(SEP)
    print(f"模型: {model}")
    print("命令:")
    print("  /exit           - 退出程序")
    print("  /session list   - 列出所有会话")
    print("  /session new    - 创建新会话")
    print("  /session switch - 切换会话")
    print("  /memory list    - 查看长期记忆")
    print("  /memory add     - 添加长期记忆")
    print("  /workspace show - 查看当前 workspace")
    print("  /workspace set  - 设置 workspace 目录")
    print("  /skill list     - 列出可用 skill")
    print("  /skill <name> ..- 用某个 skill 完成任务")
    print("  /compact        - 立即压缩当前会话")
    print("  /help           - 显示帮助")
    print(LINE)
    print(f"📂 当前会话: {session.title} ({session.session_id})")
    print(f"📝 消息数: {len(session.messages)}")
    ws = getattr(session, "workspace", None)
    print(f"🗂️  workspace: {ws if ws else '（未设置）'}")


def print_help():
    print("命令:")
    print("  /exit                        - 退出程序")
    print("  /help                        - 显示帮助")
    print("  /session list                - 列出所有会话（* 为当前会话）")
    print("  /session new                 - 创建并切换到新会话")
    print("  /session switch <id>         - 切换到指定会话")
    print("  /session delete <id>         - 删除指定会话")
    print("  /session rename <id> <title> - 重命名会话")
    print("  /memory list                 - 列出所有长期记忆")
    print("  /memory add <内容>           - 添加一条长期记忆")
    print("  /memory delete <id>          - 删除指定长期记忆")
    print("  /workspace show              - 查看当前会话的 workspace")
    print("  /workspace set <path>        - 设置当前会话的 workspace 目录")
    print("  /skill list                  - 列出全部可用 skill")
    print("  /skill show <name>           - 查看某个 skill 的完整说明")
    print("  /skill usage                 - 查看当前会话的 skill 使用记录")
    print("  /skill <name> <task>         - 用指定 skill 完成任务（免审批）")
    print("  /compact                     - 立即压缩当前会话的较早消息")


def _print_session_list(manager):
    print("Sessions:")
    for s in manager.list_sorted():
        marker = "*" if s.session_id == manager.current_id else " "
        updated = s.updated_at.strftime("%Y-%m-%d %H:%M")
        print(f"{marker} {s.session_id:<12} {s.title:<16} messages={len(s.messages)}    updated={updated}")


def handle_session_command(manager, raw):
    parts = raw.split()
    if len(parts) < 2:
        print("用法: /session list | new | switch <id> | delete <id> | rename <id> <title>")
        return

    sub = parts[1]
    if sub == "list":
        _print_session_list(manager)
    elif sub == "new":
        session = manager.new_session()
        print(f"Created session: {session.session_id}")
        print(f"Switched to: {session.title}")
    elif sub == "switch":
        if len(parts) < 3:
            print("用法: /session switch <id>")
            return
        try:
            session = manager.switch(parts[2])
            print(f"Switched to session: {session.session_id}")
        except SessionError as e:
            print(f"[错误] {e}")
    elif sub == "delete":
        if len(parts) < 3:
            print("用法: /session delete <id>")
            return
        try:
            deleted_id = manager.delete(parts[2])
            print(f"Deleted session: {deleted_id}")
        except SessionError as e:
            print(f"[错误] {e}")
    elif sub == "rename":
        if len(parts) < 4:
            print("用法: /session rename <id> <new_title>")
            return
        new_title = " ".join(parts[3:])
        try:
            session = manager.rename(parts[2], new_title)
            print(f"Renamed {session.session_id} -> {session.title}")
        except SessionError as e:
            print(f"[错误] {e}")
    else:
        print(f"未知的 session 子命令: {sub}（输入 /help 查看帮助）")


def _print_memory_list(memory_store):
    memories = memory_store.list()
    if not memories:
        print("（暂无长期记忆）")
        return
    print("Memories:")
    for m in memories:
        print(f"  {m['id']:<10} {m['content']}")


def handle_memory_command(memory_store, raw):
    parts = raw.split(maxsplit=2)
    sub = parts[1] if len(parts) >= 2 else "list"
    if sub == "list":
        _print_memory_list(memory_store)
    elif sub == "add":
        if len(parts) < 3 or not parts[2].strip():
            print("用法: /memory add <内容>")
            return
        try:
            item = memory_store.add(parts[2])
            print(f"Added memory: {item['id']}")
        except MemoryError as e:
            print(f"[错误] {e}")
    elif sub == "delete":
        if len(parts) < 3 or not parts[2].strip():
            print("用法: /memory delete <id>")
            return
        try:
            deleted_id = memory_store.delete(parts[2].strip())
            print(f"Deleted memory: {deleted_id}")
        except MemoryError as e:
            print(f"[错误] {e}")
    else:
        print(f"未知的 memory 子命令: {sub}（输入 /help 查看帮助）")


def handle_workspace_command(manager, raw):
    """/workspace show | set <path>：查看或设置当前会话可操作的项目目录。"""
    parts = raw.split(maxsplit=2)
    sub = parts[1] if len(parts) >= 2 else "show"
    session = manager.current
    if sub == "show":
        ws = getattr(session, "workspace", None)
        print(f"workspace: {ws if ws else '（未设置）'}")
    elif sub == "set":
        if len(parts) < 3 or not parts[2].strip():
            print("用法: /workspace set <path>")
            return
        try:
            ws = normalize_workspace(parts[2])
        except WorkspaceError as e:
            print(f"[错误] {e}")
            return
        session.workspace = ws
        try:
            manager.save(session)
        except SessionError as e:
            print(f"[错误] 保存会话失败: {e}")
            return
        print(f"workspace 已设置为: {ws}")
    else:
        print(f"未知的 workspace 子命令: {sub}（用法: /workspace show | set <path>）")


def _print_compaction_summary(result):
    print(f"[system] summary:")
    print(result["summary"])


def handle_skill_command(runtime, raw):
    """/skill list | show <name> | usage | <name> <task>。
    - list：列出全部可用 skill（name + 适用场景）；
    - show <name>：查看某个 skill 的完整说明与资源文件名；
    - usage：查看当前会话内的 skill 使用记录；
    - <name> <task>：用户显式调用某个 skill 完成 <task>（免审批，直接加载完整内容）。"""
    registry = runtime.skill_registry
    parts = raw.split(maxsplit=2)
    if len(parts) < 2:
        print("用法: /skill list | show <name> | usage | <name> <task>")
        return
    sub = parts[1]
    if sub == "list":
        _print_skill_list(registry)
    elif sub == "show":
        if len(parts) < 3 or not parts[2].strip():
            print("用法: /skill show <name>")
            return
        _print_skill_show(registry, parts[2].strip())
    elif sub == "usage":
        _print_skill_usage(runtime.manager.current)
    else:
        # 其余情况视为显式调用：/skill <name> <task>
        name = sub
        task = parts[2].strip() if len(parts) >= 3 else ""
        if registry is None or not registry.has(name):
            print(f"[错误] 不存在名为 '{name}' 的 skill（用 /skill list 查看可用列表）")
            return
        if not task:
            print(f"用法: /skill {name} <task>（请在 skill 名后给出要完成的任务）")
            return
        chat_once(runtime, task, skill_name=name)


def _print_skill_list(registry):
    skills = registry.list() if registry is not None else []
    if not skills:
        print("（暂无可用 skill）")
        return
    print("Skills:")
    for s in skills:
        print(f"  {s['name']}")
        print(f"      {s['description']}")


def _print_skill_show(registry, name):
    if registry is None or not registry.has(name):
        print(f"[错误] 不存在名为 '{name}' 的 skill")
        return
    try:
        skill = registry.load(name)
    except Exception as e:
        print(f"[错误] 加载 skill 失败: {e}")
        return
    print(f"Skill: {skill.name}")
    print(f"描述: {skill.description}")
    print(LINE)
    print(skill.instructions or "（无正文）")
    if skill.resources:
        print(LINE)
        print("资源文件:")
        for rel in skill.resources:
            print(f"  - {rel}")


def _print_skill_usage(session):
    usages = getattr(session, "skill_usages", [])
    if not usages:
        print("（本会话暂无 skill 使用记录）")
        return
    print(f"Skill usage（{session.session_id}）:")
    for u in usages:
        src = u.get("source", "")
        reason = f" 理由={u['reason']}" if u.get("reason") else ""
        print(f"  {u.get('usedAt', '')} · {u.get('skill', '')}（{src}）{reason}")
        task = u.get("task", "")
        if task:
            print(f"      task: {task}")


def handle_compact_command(client, manager):
    """手动 /compact：立即压缩当前会话，并在应用后持久化。"""
    session = manager.current
    result = compact(client, session, COMPACT_RECENT_MESSAGES)
    print(f"Compacted session {session.session_id}.")
    print(f"Old messages: {result['old_count']}")
    print(f"Recent messages: {result['recent_count']}")
    print(f"Summary updated: {'yes' if result['applied'] else 'no'}")
    if result["applied"]:
        print("Summary:")
        print(result["summary"])
        try:
            manager.save(session)
        except SessionError as e:
            print(f"[错误] {e}")
    elif result["reason"]:
        print(f"（原因: {result['reason']}）")


def _cli_event_printer(kind, data):
    """把 agent loop 的过程事件打印到 CLI，便于观察 tool 调用与结果。"""
    if kind == "tool_call":
        print(f"[tool_call] {data['tool']} {json.dumps(data['args'], ensure_ascii=False)}")
    elif kind == "approval":
        # 审批请求即将进入 _cli_approval 阻塞询问，这里只提示。
        print(f"[approval] 需要确认：{data['tool']}（{data.get('safety', '')}）")
    elif kind == "approval_result":
        verb = "已批准" if data["approved"] else "已拒绝"
        reason = f"，原因: {data['reason']}" if data.get("reason") else ""
        print(f"[approval_result] {data['tool']} {verb}{reason}")
    elif kind == "tool_result":
        status = "ok" if data["ok"] else "error"
        text = data["output"] if data["ok"] else data["error"]
        preview = text if len(text) <= 500 else text[:500] + " ...(已截断)"
        print(f"[tool_result] {data['tool']} ({status}) {preview}")
    elif kind == "skill":
        src = "用户指定" if data.get("source") == "explicit" else "模型自主选择"
        reason = f"，理由: {data['reason']}" if data.get("reason") else ""
        print(f"[skill] 已加载 skill: {data['skill']}（{src}{reason}）")
    elif kind == "skill_result":
        if not data.get("ok", False):
            print(f"[skill] 加载 skill {data.get('skill', '')} 失败: {data.get('error', '')}")
    elif kind == "final":
        print(f"[Assistant] {data['content']}")
    elif kind == "error":
        print(f"[错误] {data['message']}")
    elif kind == "compaction":
        if data.get("applied"):
            print(f"\n[system] compact: old_messages={data['old_count']}, "
                  f"recent_messages={data['recent_count']}")
            _print_compaction_summary(data)
        else:
            print(f"\n[system] compaction 跳过（{data.get('reason', '')}），本轮消息保持不变。")


def _cli_approval(tool, args):
    """CLI 侧的同步审批：写/命令类 tool 执行前询问用户 y/N，返回 (approved, reason)。
    非 y/yes 一律视为拒绝；拒绝时可选填原因，作为 observation 反馈给模型。"""
    print(f"\n[需要审批] 模型请求执行 {tool}")
    print(f"  参数: {json.dumps(args, ensure_ascii=False)}")
    try:
        ans = input("  是否允许执行？(y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return (False, "用户取消了审批。")
    if ans in ("y", "yes"):
        return (True, "")
    try:
        reason = input("  拒绝原因（可留空，回车跳过）: ").strip()
    except (EOFError, KeyboardInterrupt):
        reason = ""
    return (False, reason or "用户未批准该操作。")


def chat_once(runtime, user_input, skill_name=None):
    """通过统一 agent runtime 处理一轮对话（含 tool loop、审批、压缩与持久化）。
    skill_name 非空时表示用户显式调用某个 skill（免审批，直接加载完整内容）。"""
    session = runtime.manager.current
    runtime.run(session, user_input, on_event=_cli_event_printer,
                approval_fn=_cli_approval, skill_name=skill_name)


def main():
    _reconfigure_io()
    try:
        runtime = build_runtime()
        manager = runtime.manager
        memory_store = runtime.memory_store
        client = runtime.client
    except Exception as e:
        print(f"[启动失败] {e}")
        return 1

    print_banner(DEFAULT_MODEL, manager.current)

    while True:
        try:
            user_input = input("\n[You] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break

        if not user_input:
            continue
        if user_input == "/exit":
            print("bye.")
            break
        if user_input == "/help":
            print_help()
            continue
        if user_input.startswith("/session"):
            handle_session_command(manager, user_input)
            continue
        if user_input.startswith("/memory"):
            handle_memory_command(memory_store, user_input)
            continue
        if user_input.startswith("/workspace"):
            handle_workspace_command(manager, user_input)
            continue
        if user_input.startswith("/skill"):
            handle_skill_command(runtime, user_input)
            continue
        if user_input == "/compact":
            handle_compact_command(client, manager)
            continue
        if user_input.startswith("/"):
            print(f"未知命令: {user_input}（输入 /help 查看帮助）")
            continue

        chat_once(runtime, user_input)

    return 0


if __name__ == "__main__":
    sys.exit(main())
