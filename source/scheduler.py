"""
DD-SJTUClaw Scheduler（Step 7）。

Scheduler 是 claw runtime 外围的长期运行服务：它保存并管理用户创建的定时任务，
并在任务到期时把任务内容作为一条用户消息交给**已有的 AgentRuntime**（agent loop）执行。

定位（与 Gateway 对比）：
    图形化界面消息 -> Gateway   -> agent loop
    定时任务       -> Scheduler -> agent loop
Scheduler 不是新的 agent，也不新建对话系统：它只负责“什么时候触发”和“触发后如何记录结果”；
真正的 context builder / memory / tool registry / compaction / 最终回答都由 AgentRuntime 完成，
因此不会绕过 session store、context builder、tool registry、memory 或 compaction。

任务模型（持久化在独立文件，不破坏 session 数据结构，也不另存聊天历史）：
    id          任务 ID（task_XXX）
    content     到期后交给 agent loop 的用户指令
    sessionId   任务执行时进入的 session（明确归属，结果写回该 session 历史）
    type        "once"（一次性）| "recurring"（周期性）
    schedule    触发规则：
                  {"kind": "once",     "runAt": iso}     一次性，指定时间点
                  {"kind": "interval", "seconds": N}     周期性，固定间隔
                  {"kind": "daily",    "time": "HH:MM"}  周期性，每天固定时间
    nextRunAt   下一次触发时间（iso）；一次性执行后或取消后为 None
    status      pending / running / done / cancelled / failed
    history     每次执行的结果：[{ranAt, ok, reply, error}]
    createdAt / updatedAt

边界处理（合理即可）：
    - 上一次执行失败：周期性任务仍继续下一次触发（失败被记录，不静默吞掉）；一次性任务标记 failed。
    - 执行时间长于间隔：任务串行执行，下一次触发时间从“执行完成时刻”重新计算，避免堆积。
    - 用户取消：状态置为 cancelled 且 nextRunAt=None，未来不再触发。
    - 关闭期间错过的触发：一次性任务在下次轮询时补执行一次；周期性任务滚动到下一个未来触发点（不逐个补执行）。
    - 程序重启：状态为 running 的任务（上次执行被中断）恢复为 pending 以便重新触发。

仅使用 Python 标准库（LLM 调用通过 AgentRuntime 内部的 client 完成）。
"""

import copy
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

# 单条执行历史里 assistant 回复的展示上限，避免任务文件无限膨胀。
_HISTORY_REPLY_CAP = 2000


class SchedulerError(Exception):
    """定时任务相关错误（创建校验失败、任务不存在等）。"""


# ---------- 时间与调度规则解析 ----------
def _parse_dt(text):
    """解析用户给出的时间点，支持常见的日期时间格式。"""
    if not isinstance(text, str) or not text.strip():
        raise SchedulerError("一次性任务缺少 runAt（触发时间）")
    s = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise SchedulerError(f"无法解析触发时间: {text}（请用 YYYY-MM-DD HH:MM 格式）")


def _parse_hhmm(text):
    """解析每天固定时间 HH:MM。"""
    if not isinstance(text, str):
        raise SchedulerError("每天任务缺少 time（HH:MM）")
    try:
        t = datetime.strptime(text.strip(), "%H:%M")
    except ValueError:
        raise SchedulerError(f"无法解析每天时间: {text}（请用 HH:MM 格式）")
    return t.hour, t.minute


def normalize_schedule(schedule):
    """校验并规整触发规则；返回 (规整后的 schedule, type)。规则非法时抛 SchedulerError。"""
    if not isinstance(schedule, dict):
        raise SchedulerError("缺少 schedule（触发规则）")
    kind = schedule.get("kind")
    if kind == "once":
        dt = _parse_dt(schedule.get("runAt"))
        return {"kind": "once", "runAt": dt.isoformat()}, "once"
    if kind == "interval":
        seconds = schedule.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            raise SchedulerError("interval 任务的 seconds 必须为正数（秒）")
        return {"kind": "interval", "seconds": int(seconds)}, "recurring"
    if kind == "daily":
        hh, mm = _parse_hhmm(schedule.get("time"))
        return {"kind": "daily", "time": f"{hh:02d}:{mm:02d}"}, "recurring"
    raise SchedulerError(f"不支持的 schedule.kind: {kind}（支持 once / interval / daily）")


def next_after(schedule, after):
    """给定“基准时刻 after”，计算严格晚于/等于它的下一次触发时间。
    一次性任务无下一次触发，返回 None。"""
    kind = schedule["kind"]
    if kind == "interval":
        return after + timedelta(seconds=schedule["seconds"])
    if kind == "daily":
        hh, mm = (int(x) for x in schedule["time"].split(":"))
        candidate = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate
    return None


def initial_next(schedule, now):
    """任务创建时计算首次触发时间。"""
    kind = schedule["kind"]
    if kind == "once":
        return datetime.fromisoformat(schedule["runAt"])
    if kind == "interval":
        return now + timedelta(seconds=schedule["seconds"])
    if kind == "daily":
        return next_after(schedule, now)
    return None


# ---------- 任务持久化 ----------
class TaskStore:
    """定时任务的持久化与增删查改。线程安全（RLock 保护内存与文件写入）。"""

    def __init__(self, tasks_file):
        self.tasks_file = Path(tasks_file)
        self.tasks_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tasks = {}  # id -> task dict
        self._load()

    def _load(self):
        if not self.tasks_file.exists():
            return
        try:
            with open(self.tasks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            tasks = data["tasks"]
            if not isinstance(tasks, list):
                raise ValueError("tasks 字段不是列表")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as e:
            print(f"[警告] 任务文件损坏，已跳过加载（数据保留，未删除）: {self.tasks_file} -> {e}")
            return
        for t in tasks:
            tid = t.get("id")
            if tid:
                self.tasks[tid] = t

    def _save(self):
        items = [self.tasks[k] for k in sorted(self.tasks)]
        try:
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                json.dump({"tasks": items}, f, ensure_ascii=False, indent=2)
        except OSError as e:
            raise SchedulerError(f"保存任务失败（{self.tasks_file}）: {e}")

    def _next_id(self):
        max_n = 0
        for tid in self.tasks:
            if tid.startswith("task_"):
                suffix = tid.split("_", 1)[1]
                if suffix.isdigit():
                    max_n = max(max_n, int(suffix))
        return f"task_{max_n + 1:03d}"

    def resolve_id(self, user_id):
        """支持简写：task_001 / 001 / 1 都能定位到 task_001。"""
        user_id = str(user_id)
        with self.lock:
            if user_id in self.tasks:
                return user_id
            prefixed = f"task_{user_id}"
            if prefixed in self.tasks:
                return prefixed
            if user_id.isdigit():
                padded = f"task_{int(user_id):03d}"
                if padded in self.tasks:
                    return padded
            return None

    def add(self, task):
        with self.lock:
            tid = self._next_id()
            task["id"] = tid
            self.tasks[tid] = task
            self._save()
            return copy.deepcopy(task)

    def list(self):
        with self.lock:
            return [copy.deepcopy(self.tasks[k]) for k in sorted(self.tasks)]

    def get(self, tid):
        with self.lock:
            resolved = self.resolve_id(tid)
            return copy.deepcopy(self.tasks[resolved]) if resolved else None

    def set_running(self, tid):
        with self.lock:
            resolved = self.resolve_id(tid)
            if resolved is None:
                return
            t = self.tasks[resolved]
            t["status"] = "running"
            t["updatedAt"] = datetime.now().isoformat()
            self._save()

    def record_result(self, tid, ok, reply, error):
        """追加一次执行历史，并按任务类型推进状态与下一次触发时间。"""
        with self.lock:
            resolved = self.resolve_id(tid)
            if resolved is None:
                return
            t = self.tasks[resolved]
            now = datetime.now()
            entry = {
                "ranAt": now.isoformat(),
                "ok": bool(ok),
                "reply": (reply or "")[:_HISTORY_REPLY_CAP] if ok else "",
                "error": error,
            }
            t["history"].append(entry)
            if t["status"] == "cancelled":
                # 执行期间被取消：保留历史，但不再排下一次触发
                t["nextRunAt"] = None
            elif t["schedule"]["kind"] == "once":
                t["status"] = "done" if ok else "failed"
                t["nextRunAt"] = None
            else:
                # 周期性任务：无论成功失败都继续下一次；下次时间从“现在”重新计算，避免堆积
                t["status"] = "pending"
                nxt = next_after(t["schedule"], now)
                t["nextRunAt"] = nxt.isoformat() if nxt else None
            t["updatedAt"] = now.isoformat()
            self._save()

    def cancel(self, tid):
        with self.lock:
            resolved = self.resolve_id(tid)
            if resolved is None:
                raise SchedulerError(f"没有找到任务: {tid}")
            t = self.tasks[resolved]
            if t["status"] in ("done", "failed", "cancelled"):
                raise SchedulerError(f"任务已结束（{t['status']}），无法取消")
            t["status"] = "cancelled"
            t["nextRunAt"] = None
            t["updatedAt"] = datetime.now().isoformat()
            self._save()
            return copy.deepcopy(t)

    def recover_running(self):
        """程序重启时：把上次被中断的 running 任务恢复为 pending，便于重新触发。"""
        with self.lock:
            changed = False
            now = datetime.now().isoformat()
            for t in self.tasks.values():
                if t["status"] == "running":
                    t["status"] = "pending"
                    t["history"].append({
                        "ranAt": now, "ok": False, "reply": "",
                        "error": "程序重启：任务从『执行中』恢复为『等待中』",
                    })
                    t["updatedAt"] = now
                    changed = True
            if changed:
                self._save()


# ---------- 调度器 ----------
class Scheduler:
    """长期运行的调度线程：轮询到期任务并交给 AgentRuntime 执行。"""

    def __init__(self, store, runtime, run_lock, poll_seconds=5.0):
        self.store = store
        self.runtime = runtime
        self.manager = runtime.manager
        # 与 Gateway 共用同一把执行锁：串行化 agent 执行与 session 写入，避免并发写坏 session
        self.run_lock = run_lock
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = None

    # ---- 任务管理（供 Gateway / 其它入口调用）----
    def create_task(self, body):
        """校验并创建任务。时间无法解析 / 规则无效 / session 不存在都会抛 SchedulerError。"""
        if not isinstance(body, dict):
            raise SchedulerError("请求体无效")
        content = (body.get("content") or "").strip()
        if not content:
            raise SchedulerError("任务内容不能为空")

        session_ref = body.get("sessionId") or self.manager.current_id
        resolved = self.manager.resolve_id(str(session_ref)) if session_ref else None
        if resolved is None:
            raise SchedulerError(f"session 不存在: {session_ref}")

        schedule, ttype = normalize_schedule(body.get("schedule"))
        now = datetime.now()
        next_run = initial_next(schedule, now)
        task = {
            "id": None,
            "content": content,
            "sessionId": resolved,
            "type": ttype,
            "schedule": schedule,
            "nextRunAt": next_run.isoformat() if next_run else None,
            "status": "pending",
            "createdAt": now.isoformat(),
            "updatedAt": now.isoformat(),
            "history": [],
        }
        return self.store.add(task)

    def list_tasks(self):
        return self.store.list()

    def get_task(self, tid):
        return self.store.get(tid)

    def cancel_task(self, tid):
        return self.store.cancel(tid)

    # ---- 生命周期 ----
    def start(self):
        self.store.recover_running()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="claw-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_seconds + 1)

    # ---- 轮询与执行 ----
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                # 调度线程本身不能因单次异常而退出
                print(f"[scheduler] 轮询异常（已忽略，继续运行）: {e}")
            self._stop.wait(self.poll_seconds)

    def _tick(self):
        now = datetime.now()
        for task in self.store.list():
            if self._stop.is_set():
                break
            if task["status"] != "pending":
                continue
            nxt = task.get("nextRunAt")
            if not nxt:
                continue
            try:
                due = datetime.fromisoformat(nxt) <= now
            except (ValueError, TypeError):
                continue
            if due:
                self._execute(task["id"])

    def _execute(self, tid):
        task = self.store.get(tid)
        if task is None or task["status"] != "pending":
            return

        resolved = self.manager.resolve_id(task["sessionId"])
        if resolved is None:
            # 所属 session 已被删除：记录失败（周期性任务会继续尝试并持续记录，不静默吞掉）
            self.store.record_result(
                tid, ok=False, reply=None,
                error=f"所属 session 不存在: {task['sessionId']}",
            )
            return
        session = self.manager.sessions[resolved]

        self.store.set_running(tid)
        reply = None
        error = None
        try:
            # 复用已有 agent loop：context builder / memory / tool / compaction 全部走同一路径
            with self.run_lock:
                reply = self.runtime.run(session, task["content"])
            if reply is None:
                error = "agent loop 未返回结果（可能是模型调用失败）"
        except Exception as e:
            error = f"任务执行异常: {e}"

        self.store.record_result(tid, ok=(reply is not None), reply=reply, error=error)
