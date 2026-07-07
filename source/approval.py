"""
DD-SJTUClaw Approval 管理（Step 8）。

update tool 与 shell tool 会改变用户项目或运行环境，执行前必须先创建 approval 请求并
暂停这次 tool 执行，等用户批准后再执行、拒绝则不执行（并可附拒绝原因指导模型继续回答）。

设计：agent loop 在执行需审批 tool 前通过一个同步的 approval 回调阻塞等待用户决定。
- CLI：回调直接用 input() 阻塞询问用户；
- Gateway：回调用本模块创建一条待审批记录并 wait()，前端通过 HTTP 轮询 /api/approvals
  发现待审批项，再 POST 决定，decide() 唤醒等待中的 agent loop。

download tool 不进入本流程（其确认发生在前端点击下载入口时）。
仅使用 Python 标准库。
"""

import threading
from datetime import datetime


class ApprovalError(Exception):
    """审批相关错误（记录不存在 / 已处理等）。"""


class ApprovalManager:
    """线程安全地管理待审批请求，并通过 Event 让 agent loop 阻塞等待用户决定。"""

    def __init__(self, timeout=300.0):
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._counter = 0

    def create(self, session_id, tool, args):
        """创建一条待审批记录（status=pending），返回记录副本。"""
        with self._lock:
            self._counter += 1
            aid = f"apr_{self._counter:03d}"
            rec = {
                "approvalId": aid,
                "sessionId": session_id,
                "tool": tool,
                "args": args,
                "status": "pending",   # pending | approved | rejected
                "reason": "",
                "createdAt": datetime.now().isoformat(),
            }
            self._items[aid] = rec
            self._events[aid] = threading.Event()
            return dict(rec)

    def wait(self, approval_id, timeout=None):
        """阻塞等待用户决定，返回 (approved: bool, reason: str)。
        超时按拒绝处理，避免 agent loop 永久阻塞。"""
        ev = self._events.get(approval_id)
        if ev is None:
            return (False, "审批请求不存在")
        got = ev.wait(self.timeout if timeout is None else timeout)
        with self._lock:
            rec = self._items.get(approval_id)
            if not got:
                if rec is not None and rec["status"] == "pending":
                    rec["status"] = "rejected"
                    rec["reason"] = "审批超时，已自动拒绝。"
                return (False, rec["reason"] if rec else "审批超时")
            if rec is None:
                return (False, "审批请求不存在")
            return (rec["status"] == "approved", rec.get("reason", ""))

    def decide(self, approval_id, approved, reason=""):
        """由用户侧（CLI / HTTP）调用，批准或拒绝一条待审批请求并唤醒等待方。"""
        with self._lock:
            rec = self._items.get(approval_id)
            if rec is None:
                raise ApprovalError(f"审批请求不存在: {approval_id}")
            if rec["status"] != "pending":
                raise ApprovalError(f"审批请求已处理: {approval_id}（当前状态 {rec['status']}）")
            rec["status"] = "approved" if approved else "rejected"
            rec["reason"] = reason or ""
            ev = self._events.get(approval_id)
            result = dict(rec)
        if ev is not None:
            ev.set()
        return result

    def list_pending(self):
        """列出所有待审批请求（供前端轮询发现）。"""
        with self._lock:
            return [dict(r) for r in self._items.values() if r["status"] == "pending"]

    def get(self, approval_id):
        with self._lock:
            rec = self._items.get(approval_id)
            return dict(rec) if rec else None
