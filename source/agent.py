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
    kind ∈ {"tool_call", "approval", "approval_result", "tool_result",
            "skill", "skill_result", "final", "error", "compaction"}
"""

from datetime import datetime

from .compaction import compact, should_compact
from .context_builder import build_messages
from .skills import SkillError, build_skills_prompt
from .tools import (
    SAFETY_SHELL,
    SAFETY_WRITE,
    ToolContext,
    build_tools_prompt,
    parse_model_output,
)

# 需要 approval（执行前必须经用户确认）的 tool 安全级别。
_APPROVAL_SAFETY = (SAFETY_WRITE, SAFETY_SHELL)

# 模型自主请求使用 skill 时，approval 流程中使用的伪 tool 名（复用统一审批入口）。
SKILL_APPROVAL_TOOL = "use_skill"
# skill 使用记录中 output 字段（最终输出）的展示字符上限。
SKILL_USAGE_OUTPUT_CAP = 2000

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
                 stable_prompt, compact_max_messages, compact_max_chars, compact_recent,
                 attachment_store=None, download_registry=None, shell_manager=None,
                 skill_registry=None):
        self.client = client
        self.manager = manager
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.stable_prompt = stable_prompt
        self.compact_max_messages = compact_max_messages
        self.compact_max_chars = compact_max_chars
        self.compact_recent = compact_recent
        # Step 8 依赖：附件存储 / 下载注册表 / shell 管理器，注入到 tool 执行上下文。
        self.attachment_store = attachment_store
        self.download_registry = download_registry
        self.shell_manager = shell_manager
        # Step 9 依赖：skill 注册表（扫描本地 skills/），供显式调用与模型自主选择。
        self.skill_registry = skill_registry

    # ---------- 上下文动态部分 ----------
    def _extra_system_sections(self, session):
        """额外系统提示段：Step 8 加入 workspace 状态，让模型知道当前操作哪个目录及其边界。"""
        ws = getattr(session, "workspace", None)
        if ws:
            return [
                f"当前 workspace（agent 可操作的项目目录）: {ws}\n"
                "文件创建/修改、shell 命令、附件拷贝、下载入口创建都发生在该目录内；"
                "相对路径按它解析，禁止使用绝对路径或 ../ 越界。"
            ]
        return [
            "当前尚未设置 workspace。在设置 workspace 之前，"
            "文件修改、命令执行、附件拷贝、下载入口创建等高级 tool 无法执行；"
            "只读 tool（列目录、读文件、看时间）仍可使用。"
        ]

    def _build_tool_context(self, session):
        """构造本轮 tool 执行所需的运行期上下文（不发送给模型）。"""
        return ToolContext(
            workspace=getattr(session, "workspace", None),
            session=session,
            attachment_store=self.attachment_store,
            download_registry=self.download_registry,
            shell_manager=self.shell_manager,
        )

    def _build_tools_prompt(self, session):
        base = build_tools_prompt(self.tool_registry.definitions())
        sections = [s for s in self._extra_system_sections(session) if s]
        if sections:
            return "\n\n".join([base] + sections) if base else "\n\n".join(sections)
        return base

    def _build_full_prompt(self, session, active_skills):
        """组装本轮的动态系统提示：tool 说明 + workspace 状态 + skill（索引/已加载内容）。
        active_skills 每轮可能变化（模型中途自主选中 skill），故在循环内重建。"""
        parts = [self._build_tools_prompt(session)]
        if self.skill_registry is not None:
            skills_prompt = build_skills_prompt(self.skill_registry, active_skills)
            if skills_prompt:
                parts.append(skills_prompt)
        return "\n\n".join(p for p in parts if p)

    # ---------- 主循环 ----------
    def run(self, session, user_input, on_event=None, approval_fn=None, skill_name=None):
        """执行一个完整 agent turn。返回最终回答文本；若 LLM 调用失败返回 None。
        产生的 user / assistant / tool 消息都进入同一个 session 历史。

        approval_fn(tool_name, args) -> (approved: bool, reason: str)：
        执行 write / shell 类 tool 前、以及模型自主请求使用 skill 前调用它等待用户决定
        （CLI 用 input()，Gateway 用 ApprovalManager）。
        为 None 时（如无人值守的定时任务）自动拒绝这类需审批的操作。

        skill_name：用户显式调用某个 skill（/skill <name> <task>）。显式调用无需 approval，
        直接加载该 skill 的完整内容进入 agent loop。"""
        session.add_message("user", user_input)
        context = self._build_tool_context(session)
        active_skills = []
        turn_usages = []  # 本轮创建的 skill 使用记录（引用），循环结束后回填 output

        if skill_name:
            self._activate_explicit_skill(session, skill_name, user_input,
                                          active_skills, turn_usages, on_event)

        final_text = self._loop(session, on_event, context, approval_fn,
                                 active_skills, turn_usages, user_input)
        # 回填 skill 使用记录的最终输出（供 usage 查看）
        for rec in turn_usages:
            rec["output"] = (final_text or "")[:SKILL_USAGE_OUTPUT_CAP]
        # 一轮结束后按阈值自动压缩（只压缩 session messages）
        self._maybe_compact(session, on_event)
        try:
            self.manager.save(session)
        except Exception as e:
            _emit(on_event, "error", {"message": f"保存会话失败: {e}"})
        return final_text

    def _loop(self, session, on_event, context, approval_fn, active_skills, turn_usages, user_task):
        produced_any = False
        rounds = 0

        while True:
            rounds += 1
            tools_prompt = self._build_full_prompt(session, active_skills)
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

            if parsed.kind == "use_skill":
                # 模型自主请求使用某个 skill：先经 approval，再加载进上下文
                self._handle_use_skill(session, parsed, active_skills, turn_usages,
                                       user_task, approval_fn, on_event)
                produced_any = True
            else:
                # tool_calls：保存模型的 tool 请求原文（kind=tool_request 便于 UI 区分 trace），再逐个执行
                session.add_message("assistant", parsed.raw or raw, kind="tool_request")
                produced_any = True
                for call in parsed.calls:
                    _emit(on_event, "tool_call", {"tool": call["tool"], "args": call["args"]})
                    self._handle_call(session, call, context, approval_fn, on_event)

            if rounds >= AGENT_SAFETY_MAX_ROUNDS:
                # 触及防御性上限：强制结束，避免失控循环
                msg = "（已达到安全迭代上限，agent 提前结束本轮。）"
                session.add_message("assistant", msg)
                _emit(on_event, "final", {"content": msg})
                return msg

    # ---------- skill ----------
    def _record_skill_usage(self, session, name, task, source, reason):
        """把一次 skill 使用记录写入 session，并返回该记录（引用，供后续回填 output）。"""
        rec = {
            "skill": name,
            "sessionId": session.session_id,
            "task": task,
            "source": source,          # explicit / auto
            "reason": reason,          # auto 时模型为何选择该 skill；explicit 为空
            "usedAt": datetime.now().isoformat(),
            "output": "",
        }
        session.add_skill_usage(rec)
        return rec

    def _activate_explicit_skill(self, session, name, task, active_skills, turn_usages, on_event):
        """显式调用（/skill <name> <task>）：直接加载 skill，无需 approval。"""
        if self.skill_registry is None or not self.skill_registry.has(name):
            _emit(on_event, "skill_result", {"skill": name, "ok": False, "error": "skill 不存在"})
            session.add_message(
                "tool", f"[skill] 用户请求的 skill '{name}' 不存在，请正常完成任务。",
                tool=SKILL_APPROVAL_TOOL)
            return
        try:
            skill = self.skill_registry.load(name)
        except SkillError as e:
            _emit(on_event, "skill_result", {"skill": name, "ok": False, "error": str(e)})
            session.add_message("tool", f"[skill] 加载 skill '{name}' 失败: {e}", tool=SKILL_APPROVAL_TOOL)
            return
        active_skills.append(skill)
        turn_usages.append(self._record_skill_usage(session, name, task, "explicit", ""))
        _emit(on_event, "skill", {"skill": name, "source": "explicit", "reason": ""})
        session.add_message(
            "tool",
            f"[skill] 已按用户要求加载 skill '{name}'（来源: explicit）。"
            "请按其中的说明与模板完成任务；需要写文件时使用 update tool 并经用户审批。",
            tool=SKILL_APPROVAL_TOOL)

    def _handle_use_skill(self, session, parsed, active_skills, turn_usages,
                          user_task, approval_fn, on_event):
        """模型自主选择 skill：记录请求 trace -> 校验 -> approval -> 加载并记录使用。
        无论加载成功、被拒绝还是失败，都写回一条 observation 供模型继续推理。"""
        name = parsed.skill
        reason = parsed.reason
        # 保存模型的 skill 请求原文（kind=skill_request 便于 UI 区分 trace）
        session.add_message("assistant", parsed.raw or "", kind="skill_request")

        if self.skill_registry is None or not self.skill_registry.has(name):
            _emit(on_event, "skill_result", {"skill": name, "ok": False, "error": "skill 不存在"})
            session.add_message(
                "tool", f"[skill] 不存在名为 '{name}' 的 skill，请从可用列表中选择或不使用 skill。",
                tool=SKILL_APPROVAL_TOOL)
            return

        if any(s.name == name for s in active_skills):
            session.add_message(
                "tool", f"[skill] skill '{name}' 已加载，请直接按其说明继续，不要重复请求。",
                tool=SKILL_APPROVAL_TOOL)
            return

        # 自主使用 skill 前必须向用户发送 approval 信息
        _emit(on_event, "approval",
              {"tool": SKILL_APPROVAL_TOOL, "args": {"skill": name, "reason": reason}, "safety": "skill"})
        if approval_fn is None:
            approved, ares = False, "当前运行环境无人审批（如定时任务），已自动拒绝 skill 自主调用。"
        else:
            try:
                approved, ares = approval_fn(SKILL_APPROVAL_TOOL, {"skill": name, "reason": reason})
            except Exception as e:
                approved, ares = False, f"审批过程出错，已按拒绝处理: {e}"
        _emit(on_event, "approval_result",
              {"tool": SKILL_APPROVAL_TOOL, "approved": approved, "reason": ares})
        if not approved:
            note = f"[skill] 用户拒绝使用 skill '{name}'。"
            if ares:
                note += f" 原因: {ares}"
            note += " 请在不使用该 skill 的情况下继续完成任务。"
            session.add_message("tool", note, tool=SKILL_APPROVAL_TOOL)
            return

        try:
            skill = self.skill_registry.load(name)
        except SkillError as e:
            _emit(on_event, "skill_result", {"skill": name, "ok": False, "error": str(e)})
            session.add_message("tool", f"[skill] 加载 skill '{name}' 失败: {e}", tool=SKILL_APPROVAL_TOOL)
            return
        active_skills.append(skill)
        turn_usages.append(self._record_skill_usage(session, name, user_task, "auto", reason))
        _emit(on_event, "skill", {"skill": name, "source": "auto", "reason": reason})
        session.add_message(
            "tool",
            f"[skill] 已加载 skill '{name}'（来源: auto）。请按其中的说明与模板完成任务；"
            "需要写文件时使用 update tool 并经用户审批。",
            tool=SKILL_APPROVAL_TOOL)

    def _handle_call(self, session, call, context, approval_fn, on_event):
        """执行单个 tool call：write / shell 类先经 approval，其余直接执行。
        无论成功、失败还是被拒绝，都把 observation 写回 session 供模型继续推理。"""
        name = call["tool"]
        args = call["args"]
        tool = self.tool_registry.get(name)
        safety = tool.safety_level if tool is not None else None

        if safety in _APPROVAL_SAFETY:
            _emit(on_event, "approval", {"tool": name, "args": args, "safety": safety})
            if approval_fn is None:
                approved, reason = False, "当前运行环境无人审批（如定时任务），已自动拒绝写/命令类操作。"
            else:
                try:
                    approved, reason = approval_fn(name, args)
                except Exception as e:
                    approved, reason = False, f"审批过程出错，已按拒绝处理: {e}"
            _emit(on_event, "approval_result",
                  {"tool": name, "approved": approved, "reason": reason})
            if not approved:
                note = f"[approval] 用户拒绝执行 {name}。"
                if reason:
                    note += f" 原因: {reason}"
                session.add_message("tool", note, tool=name)
                return

        result = self.tool_registry.execute(name, args, context)
        _emit(on_event, "tool_result", {
            "tool": name, "ok": result.ok,
            "output": result.output, "error": result.error,
            "extra": result.extra,
        })
        # tool 成功/失败都作为 observation 反馈给模型（role=tool，携带 trace 元数据）
        session.add_message("tool", result.to_observation(), tool=name)

    # ---------- 压缩 ----------
    def _maybe_compact(self, session, on_event):
        if not should_compact(session, self.compact_max_messages, self.compact_max_chars):
            return
        result = compact(self.client, session, self.compact_recent)
        _emit(on_event, "compaction", result)


def build_runtime():
    """构造一套完整的 AgentRuntime（client + manager + memory + tool registry + prompt
    + Step 8 的附件存储 / 下载注册表 / shell 管理器）。
    CLI、Gateway（Step 6）、Scheduler（Step 7）共用同一构造入口，确保它们进入同一条 runtime 路径。
    返回的 runtime.download_registry 可被 Gateway 复用为对外下载入口。"""
    from .attachments import AttachmentStore
    from .config import (
        ATTACHMENT_MAX_BYTES, COMPACT_MAX_CHARS, COMPACT_MAX_MESSAGES,
        COMPACT_RECENT_MESSAGES, DEFAULT_MODEL, MEMORY_FILE, PROMPT_DIR,
        SESSIONS_DIR, SHELL_OUTPUT_MAX_CHARS, SHELL_TIMEOUT_SECONDS,
        SKILLS_DIR,
    )
    from .downloads import DownloadRegistry
    from .llm_client import LLMClient
    from .memory_store import MemoryStore
    from .prompt_loader import PromptLoader
    from .session_manager import SessionManager
    from .shell import ShellManager
    from .skills import SkillRegistry
    from .tools import build_default_registry

    manager = SessionManager(SESSIONS_DIR)
    memory_store = MemoryStore(MEMORY_FILE)
    stable_prompt = PromptLoader(PROMPT_DIR).stable_prompt()
    client = LLMClient(model=DEFAULT_MODEL)
    tool_registry = build_default_registry()
    attachment_store = AttachmentStore(SESSIONS_DIR, ATTACHMENT_MAX_BYTES)
    download_registry = DownloadRegistry()
    shell_manager = ShellManager(timeout=SHELL_TIMEOUT_SECONDS,
                                 output_max_chars=SHELL_OUTPUT_MAX_CHARS)
    skill_registry = SkillRegistry(SKILLS_DIR)
    return AgentRuntime(
        client, manager, memory_store, tool_registry, stable_prompt,
        COMPACT_MAX_MESSAGES, COMPACT_MAX_CHARS, COMPACT_RECENT_MESSAGES,
        attachment_store=attachment_store,
        download_registry=download_registry,
        shell_manager=shell_manager,
        skill_registry=skill_registry,
    )
