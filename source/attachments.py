"""
DD-SJTUClaw 附件存储模块（Step 6）。

附件属于当前 session（不是 workspace 文件管理）：用来保存用户从图形化入口提交的材料。
- 文件字节保存在 data/sessions/<sessionId>/attachments/ 下；
- 附件 metadata 记录在对应 Session.attachments 中（随 session JSON 持久化）；
- 因此附件与 session 绑定，且天然隔离：一个 session 只能看到自己的附件 metadata。
仅使用 Python 标准库。
"""

import re
from datetime import datetime
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fff-]+")


class AttachmentError(Exception):
    """附件相关错误"""


class AttachmentStore:
    """按 session 保存附件字节，并把 metadata 写入 session。"""

    def __init__(self, sessions_dir, max_bytes):
        self.sessions_dir = Path(sessions_dir)
        self.max_bytes = max_bytes

    def _dir(self, session_id):
        return self.sessions_dir / session_id / "attachments"

    @staticmethod
    def _safe_name(filename):
        """只取文件名部分并清洗非法字符，防止路径穿越。"""
        name = Path(str(filename)).name  # 去掉任何目录部分
        name = _UNSAFE.sub("_", name).strip("._")
        return name or "file"

    @staticmethod
    def _next_id(session):
        max_n = 0
        for a in session.attachments:
            aid = a.get("id", "")
            if aid.startswith("att_"):
                suffix = aid.split("_", 1)[1]
                if suffix.isdigit():
                    max_n = max(max_n, int(suffix))
        return f"att_{max_n + 1:03d}"

    def add(self, session, filename, data: bytes, content_type=""):
        """保存附件字节并把 metadata 追加到 session。调用方负责随后持久化 session。"""
        if not isinstance(data, (bytes, bytearray)):
            raise AttachmentError("附件内容必须是二进制数据")
        if len(data) == 0:
            raise AttachmentError("附件内容为空")
        if len(data) > self.max_bytes:
            raise AttachmentError(f"附件过大（{len(data)} 字节），上限 {self.max_bytes} 字节")

        att_id = self._next_id(session)
        safe = self._safe_name(filename)
        stored = f"{att_id}_{safe}"
        d = self._dir(session.session_id)
        d.mkdir(parents=True, exist_ok=True)
        try:
            (d / stored).write_bytes(bytes(data))
        except OSError as e:
            raise AttachmentError(f"保存附件失败: {e}")

        meta = {
            "id": att_id,
            "filename": safe,
            "storedName": stored,
            "size": len(data),
            "type": content_type or "",
            "uploadedAt": datetime.now().isoformat(),
        }
        session.attachments.append(meta)
        session.updated_at = datetime.now()
        return meta

    def list(self, session):
        """返回本 session 的附件 metadata（不含文件内容）。"""
        return list(session.attachments)

    def find(self, session, attachment_id):
        """在本 session 中按附件 id 查找 metadata；找不到返回 None。"""
        for a in session.attachments:
            if a["id"] == attachment_id:
                return a
        return None

    def path_for(self, session, attachment_id):
        """返回本 session 内附件的真实存储路径；找不到返回 None。"""
        meta = self.find(session, attachment_id)
        if meta is None:
            return None
        return self._dir(session.session_id) / meta["storedName"]
