"""
DD-SJTUClaw 交互式命令行入口
支持多轮对话、多会话管理与本地 JSON 持久化。

启动: python -m source.main
"""

import json
import sys

from .agent import AgentRuntime
from .compaction import compact
from .config import (
    COMPACT_MAX_CHARS,
    COMPACT_MAX_MESSAGES,
    COMPACT_RECENT_MESSAGES,
    DEFAULT_MODEL,
    MEMORY_FILE,
    PROMPT_DIR,
    SESSIONS_DIR,
)
from .llm_client import LLMClient
from .memory_store import MemoryStore, MemoryError
from .prompt_loader import PromptLoader
from .session_manager import SessionManager, SessionError
from .tools import build_default_registry

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
    print("  /compact        - 立即压缩当前会话")
    print("  /help           - 显示帮助")
    print(LINE)
    print(f"📂 当前会话: {session.title} ({session.session_id})")
    print(f"📝 消息数: {len(session.messages)}")


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


def _print_compaction_summary(result):
    print(f"[system] summary:")
    print(result["summary"])


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
    elif kind == "tool_result":
        status = "ok" if data["ok"] else "error"
        text = data["output"] if data["ok"] else data["error"]
        preview = text if len(text) <= 500 else text[:500] + " ...(已截断)"
        print(f"[tool_result] {data['tool']} ({status}) {preview}")
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


def chat_once(runtime, user_input):
    """通过统一 agent runtime 处理一轮对话（含 tool loop、压缩与持久化）。"""
    session = runtime.manager.current
    runtime.run(session, user_input, on_event=_cli_event_printer)


def main():
    _reconfigure_io()
    try:
        manager = SessionManager(SESSIONS_DIR)
        memory_store = MemoryStore(MEMORY_FILE)
        stable_prompt = PromptLoader(PROMPT_DIR).stable_prompt()
        client = LLMClient(model=DEFAULT_MODEL)
        tool_registry = build_default_registry()
        runtime = AgentRuntime(
            client, manager, memory_store, tool_registry, stable_prompt,
            COMPACT_MAX_MESSAGES, COMPACT_MAX_CHARS, COMPACT_RECENT_MESSAGES,
        )
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
