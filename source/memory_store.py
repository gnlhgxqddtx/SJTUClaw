"""
DD-SJTUClaw 长期记忆模块
MemoryStore 用于保存用户长期偏好、长期事实或长期项目背景，并持久化到本地 JSON。
记忆是跨 session 的稳定上下文，不属于任何单个会话。
仅使用 Python 标准库。
"""

import json
from datetime import datetime
from pathlib import Path


class MemoryError(Exception):
    """长期记忆相关错误"""


class MemoryStore:
    """管理跨会话的长期记忆，持久化到单个 JSON 文件。"""

    def __init__(self, memory_file):
        self.memory_file = Path(memory_file)
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.memories = []  # 每项形如 {"id": "mem_001", "content": str, "createdAt": iso}
        self._load()

    def _load(self):
        if not self.memory_file.exists():
            return
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            memories = data["memories"]
            if not isinstance(memories, list):
                raise ValueError("memories 字段不是列表")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as e:
            # 损坏时报错但不静默删除，保留原文件供人工排查
            print(f"[警告] 记忆文件损坏，已跳过加载（数据保留，未删除）: {self.memory_file} -> {e}")
            return
        self.memories = memories

    def _save(self):
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump({"memories": self.memories}, f, ensure_ascii=False, indent=2)
        except OSError as e:
            raise MemoryError(f"保存记忆失败（{self.memory_file}）: {e}")

    def _next_id(self):
        max_n = 0
        for item in self.memories:
            mid = item.get("id", "")
            if mid.startswith("mem_"):
                suffix = mid.split("_", 1)[1]
                if suffix.isdigit():
                    max_n = max(max_n, int(suffix))
        return f"mem_{max_n + 1:03d}"

    def resolve_id(self, user_id):
        """支持简写：mem_001 / 001 / 1 都能定位到 mem_001。"""
        ids = {item["id"] for item in self.memories}
        if user_id in ids:
            return user_id
        prefixed = f"mem_{user_id}"
        if prefixed in ids:
            return prefixed
        if user_id.isdigit():
            padded = f"mem_{int(user_id):03d}"
            if padded in ids:
                return padded
        return None

    def add(self, content):
        content = content.strip()
        if not content:
            raise MemoryError("记忆内容不能为空")
        item = {
            "id": self._next_id(),
            "content": content,
            "createdAt": datetime.now().isoformat(),
        }
        self.memories.append(item)
        self._save()
        return item

    def list(self):
        return list(self.memories)

    def delete(self, mem_id):
        resolved = self.resolve_id(mem_id)
        if resolved is None:
            raise MemoryError(f"没有找到记忆: {mem_id}")
        self.memories = [m for m in self.memories if m["id"] != resolved]
        self._save()
        return resolved
