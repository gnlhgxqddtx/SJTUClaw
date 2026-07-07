"""
DD-SJTUClaw Agent Runtime（可复用的 agent loop）。

把"一次 LLM 调用"改造成 "LLM -> tools -> LLM" 的循环：
    用户输入 -> buildContext -> callLLM
      -> 若模型返回 final，结束
      -> 若模型返回 tool_call(s)，执行本轮 tool（一轮最多 5 个）
      -> 将 tool results 作为 observation 加入 session messages
      -> 再次 buildContext / callLLM，直到得到 final

这是 CLI、Gateway（Step 6）与 Scheduler（Step 7）共用的唯一 agent 路径：
它们都通过 AgentRuntime.run() 进入，不绕过 context builder / session store / tool registry。

事件回调 on_event(kind, data) 用于把过程反馈给调用方（CLI 打印、Gateway 流式推送）：
    kind ∈ {"tool_call", "tool_result", "final", "error", "compaction"}
"""

from .compaction import compact, should_compact
from .context_builder import build_messages
from .tools import build_tools_prompt, parse_model_output

# 防御性安全上限：单个 agent turn 的正常流程不设迭代上限（由 5 个/轮的 tool 批量限制 +
# 模型自行决定何时 final 来收敛）。此处仅为避免异常/失控模型造成无限循环与费用失控，
# 设一个很宽松的硬保护；正常任务远达不到。
AGENT_SAFETY_MAX_ROUNDS = 25


def _emit(on_event, kind, data):
    if on_event is not None:
        try:
            on_event(kind, data)
        except Exception:
            pass  # 事件回调本身的异常不应影响 agent loop


class AgentRuntime:
    """封装一整套 agent 依赖，供各入口复用。"""

    def __init__(self, client, manager, memory_store, tool_registry,
                 stable_prompt, compact_max_messages, compact_max_chars, compact_recent):
        self.client = client
        self.manager = manager
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.stable_prompt = stable_prompt
        self.compact_max_messages = compact_max_messages
        self.compact_max_chars = compact_max_chars
        self.compact_recent = compact_recent

    # ---------- 上下文动态部分 ----------
    def _extra_system_sections(self, session):
        """子类/后续 Step 可扩展的额外系统提示段（如 workspace 状态、skill 索引）。
        Step 5 暂无额外段。返回字符串列表。"""
        return []

    def _build_tools_prompt(self, session):
        base = build_tools_prompt(self.tool_registry.definitions())
        sections = [s for s in self._extra_system_sections(session) if s]
        if sections:
            return "\n\n".join([base] + sections) if base else "\n\n".join(sections)
        return base

    # ---------- 主循环 ----------
    def run(self, session, user_input, on_event=None):
        """执行一个完整 agent turn。返回最终回答文本；若 LLM 调用失败返回 None。
        产生的 user / assistant / tool 消息都进入同一个 session 历史。"""
        session.add_message("user", user_input)
        final_text = self._loop(session, on_event)
        # 一轮结束后按阈值自动压缩（只压缩 session messages）
        self._maybe_compact(session, on_event)
        try:
            self.manager.save(session)
        except Exception as e:
            _emit(on_event, "error", {"message": f"保存会话失败: {e}"})
        return final_text

    def _loop(self, session, on_event):
        tools_prompt = self._build_tools_prompt(session)
        produced_any = False
        rounds = 0

        while True:
            rounds += 1
            api_messages = build_messages(
                self.stable_prompt, self.memory_store.list(),
                session.summary, session.messages, tools_prompt,
            )
            try:
                raw = self.client.chat(api_messages)
            except Exception as e:
                _emit(on_event, "error", {"message": f"调用模型失败: {e}"})
                # 与既有行为一致：首个 LLM 调用失败时回滚本轮 user 消息，
                # 不追加空 assistant 消息；已产出内容（如 tool 结果）则保留 trace。
                if not produced_any and session.messages and session.messages[-1]["role"] == "user":
                    session.messages.pop()
                return None

            parsed = parse_model_output(raw)

            if parsed.kind == "final":
                session.add_message("assistant", parsed.content)
                _emit(on_event, "final", {"content": parsed.content})
                return parsed.content

            # tool_calls：保存模型的 tool 请求原文（标记 kind=tool_request 便于 UI 区分 trace），再逐个执行
            session.add_message("assistant", parsed.raw or raw, kind="tool_request")
            produced_any = True
            for call in parsed.calls:
                _emit(on_event, "tool_call", {"tool": call["tool"], "args": call["args"]})
                result = self.tool_registry.execute(call["tool"], call["args"])
                _emit(on_event, "tool_result", {
                    "tool": call["tool"], "ok": result.ok,
                    "output": result.output, "error": result.error,
                })
                # tool 成功/失败都作为 observation 反馈给模型（role=tool，携带 trace 元数据）
                session.add_message("tool", result.to_observation(), tool=call["tool"])

            if rounds >= AGENT_SAFETY_MAX_ROUNDS:
                # 触及防御性上限：强制结束，避免失控循环
                msg = "（已达到安全迭代上限，agent 提前结束本轮。）"
                session.add_message("assistant", msg)
                _emit(on_event, "final", {"content": msg})
                return msg

    # ---------- 压缩 ----------
    def _maybe_compact(self, session, on_event):
        if not should_compact(session, self.compact_max_messages, self.compact_max_chars):
            return
        result = compact(self.client, session, self.compact_recent)
        _emit(on_event, "compaction", result)


def build_runtime():
    """构造一套完整的 AgentRuntime（client + manager + memory + tool registry + prompt）。
    CLI、Gateway（Step 6）、Scheduler（Step 7）共用同一构造入口，确保它们进入同一条 runtime 路径。"""
    from .config import (
        COMPACT_MAX_CHARS, COMPACT_MAX_MESSAGES, COMPACT_RECENT_MESSAGES,
        DEFAULT_MODEL, MEMORY_FILE, PROMPT_DIR, SESSIONS_DIR,
    )
    from .llm_client import LLMClient
    from .memory_store import MemoryStore
    from .prompt_loader import PromptLoader
    from .session_manager import SessionManager
    from .tools import build_default_registry

    manager = SessionManager(SESSIONS_DIR)
    memory_store = MemoryStore(MEMORY_FILE)
    stable_prompt = PromptLoader(PROMPT_DIR).stable_prompt()
    client = LLMClient(model=DEFAULT_MODEL)
    tool_registry = build_default_registry()
    return AgentRuntime(
        client, manager, memory_store, tool_registry, stable_prompt,
        COMPACT_MAX_MESSAGES, COMPACT_MAX_CHARS, COMPACT_RECENT_MESSAGES,
    )
