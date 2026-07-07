"""
DD-SJTUClaw 会话管理模块
提供 Session 数据模型与 SessionManager（多会话管理 + JSON 持久化）。
仅使用 Python 标准库。
"""

import json
from datetime import datetime
from pathlib import Path


class SessionError(Exception):
    """会话管理相关错误"""


class Session:
    """单个会话的数据模型"""

    def __init__(self, session_id, title, messages=None, created_at=None, updated_at=None,
                 summary="", attachments=None, workspace=None):
        self.session_id = session_id
        self.title = title
        self.messages = messages if messages is not None else []
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()
        # summary 属于本 session：由较早消息压缩而来，仅在本 session 内生效，不跨 session 共享
        self.summary = summary
        # attachments 属于本 session：每项为附件 metadata（不含文件内容），与 session 绑定、彼此隔离
        self.attachments = attachments if attachments is not None else []
        # workspace 属于本 session（Step 8）：agent 可操作的项目目录绝对路径；未设置为 None
        self.workspace = workspace

    def add_message(self, role, content, **extra):
        """向会话追加一条消息，并刷新更新时间。
        extra 用于携带 trace 元数据（如 tool 名称），会一并持久化，但不发送给 LLM API。"""
        message = {"role": role, "content": content}
        message.update(extra)
        self.messages.append(message)
        self.updated_at = datetime.now()

    def to_dict(self):
        return {
            "sessionId": self.session_id,
            "title": self.title,
            "summary": self.summary,
            "messages": self.messages,
            "attachments": self.attachments,
            "workspace": self.workspace,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data):
        """从 JSON 数据构造 Session；字段缺失或格式错误会抛出异常。
        summary / attachments / workspace 为后续 Step 新增字段，旧会话文件缺失时按空值兼容。"""
        return cls(
            session_id=data["sessionId"],
            title=data["title"],
            messages=data["messages"],
            created_at=datetime.fromisoformat(data["createdAt"]),
            updated_at=datetime.fromisoformat(data["updatedAt"]),
            summary=data.get("summary", ""),
            attachments=data.get("attachments", []),
            workspace=data.get("workspace"),
        )


class SessionManager:
    """管理多个会话：创建、切换、删除、重命名、列表，以及 JSON 持久化"""

    def __init__(self, sessions_dir):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, Session] = {}
        self.current_id = None
        self._load_all()
        if not self.sessions:
            self._create_default()
        else:
            self.current_id = self.list_sorted()[0].session_id

    # ---------- 持久化 ----------
    def _path(self, session_id):
        return self.sessions_dir / f"{session_id}.json"

    def _load_all(self):
        """加载目录下所有会话文件；单个文件损坏时报错并跳过，不删除数据"""
        for path in sorted(self.sessions_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session = Session.from_dict(data)
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                print(f"[警告] 会话文件损坏，已跳过（数据保留，未删除）: {path.name} -> {e}")
                continue
            self.sessions[session.session_id] = session

    def save(self, session):
        """将会话保存为 JSON 文件；失败时抛出 SessionError"""
        path = self._path(session.session_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            raise SessionError(f"保存会话失败（{path}）: {e}")

    # ---------- 会话操作 ----------
    @property
    def current(self) -> Session:
        return self.sessions.get(self.current_id)

    def _next_id(self):
        max_n = 0
        for sid in self.sessions:
            if sid.startswith("session_"):
                suffix = sid.split("_", 1)[1]
                if suffix.isdigit():
                    max_n = max(max_n, int(suffix))
        return f"session_{max_n + 1:03d}"

    def _create_default(self):
        session = Session(session_id="session_001", title="default")
        self.sessions[session.session_id] = session
        self.current_id = session.session_id
        self.save(session)
        return session

    def resolve_id(self, user_id):
        """把用户输入的简写解析为真实会话 ID：
        - 精确匹配（如 session_001）
        - 补全前缀（如 001 -> session_001）
        - 纯数字补零（如 1 -> session_001）
        找不到则返回 None。
        """
        if user_id in self.sessions:
            return user_id
        prefixed = f"session_{user_id}"
        if prefixed in self.sessions:
            return prefixed
        if user_id.isdigit():
            padded = f"session_{int(user_id):03d}"
            if padded in self.sessions:
                return padded
        return None

    def new_session(self, title="新会话"):
        session = Session(session_id=self._next_id(), title=title)
        self.sessions[session.session_id] = session
        self.current_id = session.session_id
        self.save(session)
        return session

    def switch(self, session_id):
        resolved = self.resolve_id(session_id)
        if resolved is None:
            raise SessionError(f"没有找到会话: {session_id}")
        self.current_id = resolved
        return self.sessions[resolved]

    def delete(self, session_id):
        resolved = self.resolve_id(session_id)
        if resolved is None:
            raise SessionError(f"没有找到会话: {session_id}")
        del self.sessions[resolved]
        path = self._path(resolved)
        try:
            if path.exists():
                path.unlink()
        except OSError as e:
            raise SessionError(f"删除会话文件失败（{path}）: {e}")
        # 若删除的是当前会话，切换到最近更新的会话；若已无会话则新建默认会话
        if self.current_id == resolved:
            if self.sessions:
                self.current_id = self.list_sorted()[0].session_id
            else:
                self._create_default()
        return resolved

    def rename(self, session_id, new_title):
        resolved = self.resolve_id(session_id)
        if resolved is None:
            raise SessionError(f"没有找到会话: {session_id}")
        session = self.sessions[resolved]
        session.title = new_title
        session.updated_at = datetime.now()
        self.save(session)
        return session

    def list_sorted(self):
        """按更新时间倒序返回所有会话"""
        return sorted(self.sessions.values(), key=lambda s: s.updated_at, reverse=True)
