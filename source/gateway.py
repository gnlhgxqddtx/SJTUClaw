"""
DD-SJTUClaw Gateway（Step 6）。

Gateway 是外部图形化入口与 claw agent runtime 之间的服务层：
- 长期运行的 HTTP server，持续接收外部请求；
- 根据请求中的 sessionId 找到 / 使用 / 创建 session；
- 把请求转交给**已有的 AgentRuntime**（不绕过 context builder / session store / tool registry）；
- 返回 assistant 回复、session 信息、tool trace 或错误。

单次请求失败只返回错误响应，不会导致进程退出。
通信协议：HTTP + JSON（附件用 base64 承载）。仅使用 Python 标准库。

HTTP 接口：
  GET  /                                  -> web 图形化入口（web/index.html）
  GET  /api/sessions                      -> 列出所有 session
  POST /api/sessions            {title?}  -> 新建 session
  GET  /api/sessions/<id>/messages        -> 该 session 的消息历史
  POST /api/chat  {sessionId?, message}   -> 走 agent loop，返回 reply + events
  GET  /api/sessions/<id>/attachments     -> 该 session 的附件 metadata（session 隔离）
  POST /api/sessions/<id>/attachments     -> 上传附件 {filename, type?, dataBase64}
  GET  /api/health                        -> 健康检查

sessionId 策略：
  - 带 sessionId 且存在：使用该 session；
  - 带 sessionId 但不存在：返回 404 错误（不隐式新建，避免拼写错误产生垃圾 session）；
  - 不带 sessionId：使用默认 session（manager.current）。
"""

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .attachments import AttachmentError
from .scheduler import SchedulerError

_RE_MESSAGES = re.compile(r"^/api/sessions/([^/]+)/messages$")
_RE_ATTACH = re.compile(r"^/api/sessions/([^/]+)/attachments$")
_RE_TASK = re.compile(r"^/api/tasks/([^/]+)$")
_RE_TASK_CANCEL = re.compile(r"^/api/tasks/([^/]+)/cancel$")

# 单条 event 中 tool 输出的展示上限，避免响应体过大。
_EVENT_TEXT_CAP = 2000


class GatewayError(Exception):
    """带 HTTP 状态码的网关错误。"""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Gateway:
    """把外部 HTTP 请求路由到已有 AgentRuntime。"""

    def __init__(self, runtime, attachment_store, web_dir, download_registry=None,
                 scheduler=None, run_lock=None):
        self.runtime = runtime
        self.manager = runtime.manager
        self.attachments = attachment_store
        self.web_dir = web_dir
        # download_registry 供 Step 8 的下载入口使用；Step 6 可为 None
        self.downloads = download_registry
        # scheduler 供 Step 7 的定时任务管理使用；单独作为 CLI/测试时可为 None
        self.scheduler = scheduler
        # 串行化 agent 执行与会话写入：本地单用户场景，用一把全局锁即可保证线程安全。
        # run_lock 由外部传入时，可与 Scheduler 共用同一把锁，串行化“聊天”与“定时任务”对 session 的写入。
        self.lock = run_lock or threading.Lock()

    # ---------- session ----------
    def _resolve_session(self, sid, allow_default=False):
        if not sid:
            if allow_default:
                return self.manager.current
            raise GatewayError(400, "缺少 sessionId")
        resolved = self.manager.resolve_id(sid)
        if resolved is None:
            raise GatewayError(404, f"session 不存在: {sid}")
        return self.manager.sessions[resolved]

    def list_sessions(self):
        out = []
        for s in self.manager.list_sorted():
            out.append({
                "sessionId": s.session_id,
                "title": s.title,
                "messageCount": len(s.messages),
                "attachmentCount": len(s.attachments),
                "updatedAt": s.updated_at.isoformat(),
            })
        return {"sessions": out, "currentId": self.manager.current_id}

    def create_session(self, title):
        with self.lock:
            session = self.manager.new_session(title=title or "Web 会话")
        return {"sessionId": session.session_id, "title": session.title}

    @staticmethod
    def _public_messages(session):
        out = []
        for m in session.messages:
            item = {"role": m.get("role"), "content": m.get("content", "")}
            if m.get("kind"):
                item["kind"] = m["kind"]
            if m.get("tool"):
                item["tool"] = m["tool"]
            out.append(item)
        return out

    def get_messages(self, sid):
        session = self._resolve_session(sid)
        return {
            "sessionId": session.session_id,
            "title": session.title,
            "summary": session.summary,
            "messages": self._public_messages(session),
        }

    # ---------- chat（进入 agent loop）----------
    def chat(self, sid, message):
        if not isinstance(message, str) or not message.strip():
            raise GatewayError(400, "message 不能为空")
        session = self._resolve_session(sid, allow_default=True)

        events = []

        def on_event(kind, data):
            trimmed = dict(data)
            for key in ("output", "error", "content"):
                v = trimmed.get(key)
                if isinstance(v, str) and len(v) > _EVENT_TEXT_CAP:
                    trimmed[key] = v[:_EVENT_TEXT_CAP] + " ...(已截断)"
            events.append({"kind": kind, **trimmed})

        with self.lock:
            reply = self.runtime.run(session, message.strip(), on_event=on_event)

        return {
            "ok": reply is not None,
            "sessionId": session.session_id,
            "reply": reply,
            "events": events,
        }

    # ---------- 附件 ----------
    def list_attachments(self, sid):
        session = self._resolve_session(sid)
        return {"sessionId": session.session_id, "attachments": self.attachments.list(session)}

    def upload_attachment(self, sid, body):
        session = self._resolve_session(sid)
        filename = body.get("filename")
        data_b64 = body.get("dataBase64")
        content_type = body.get("type", "")
        if not filename or not data_b64:
            raise GatewayError(400, "缺少 filename 或 dataBase64")
        try:
            data = base64.b64decode(data_b64)
        except (ValueError, TypeError) as e:
            raise GatewayError(400, f"dataBase64 解码失败: {e}")
        try:
            with self.lock:
                meta = self.attachments.add(session, filename, data, content_type)
                self.manager.save(session)
        except AttachmentError as e:
            raise GatewayError(400, str(e))
        return {"sessionId": session.session_id, "attachment": meta}

    # ---------- 定时任务（Step 7，委托给 Scheduler）----------
    def _require_scheduler(self):
        if self.scheduler is None:
            raise GatewayError(503, "Scheduler 未启用")
        return self.scheduler

    def list_tasks(self):
        return {"tasks": self._require_scheduler().list_tasks()}

    def create_task(self, body):
        scheduler = self._require_scheduler()
        try:
            task = scheduler.create_task(body)
        except SchedulerError as e:
            raise GatewayError(400, str(e))
        return {"task": task}

    def get_task(self, tid):
        task = self._require_scheduler().get_task(tid)
        if task is None:
            raise GatewayError(404, f"任务不存在: {tid}")
        return {"task": task}

    def cancel_task(self, tid):
        scheduler = self._require_scheduler()
        try:
            task = scheduler.cancel_task(tid)
        except SchedulerError as e:
            # 找不到 -> 404；其它（已结束无法取消）-> 400
            status = 404 if "没有找到" in str(e) else 400
            raise GatewayError(status, str(e))
        return {"task": task}

    # ---------- server ----------
    def build_server(self, host, port):
        """构造 HTTP server（不启动 serve_forever），便于测试控制生命周期。"""
        return ThreadingHTTPServer((host, port), _make_handler(self))

    def serve(self, host, port):
        httpd = self.build_server(host, port)
        print(f"🐾 DD-SJTUClaw Gateway 已启动: http://{host}:{port}")
        print(f"   web 图形化入口: http://{host}:{port}/")
        print("   Ctrl+C 停止。")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nGateway 正在停止 ...")
        finally:
            httpd.shutdown()
        return httpd


def _make_handler(gateway):
    """构造一个绑定到给定 Gateway 的请求处理器类。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "DDSJTUClawGateway/1.0"

        def log_message(self, fmt, *args):  # 降低默认日志噪音
            pass

        # ---- 响应工具 ----
        def _send_json(self, status, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, status, content_type, data):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise GatewayError(400, f"请求体不是合法 JSON: {e}")

        def _serve_static(self, path):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (gateway.web_dir / rel).resolve()
            web_root = gateway.web_dir.resolve()
            if web_root not in target.parents and target != web_root:
                self._send_json(404, {"error": "not found"})
                return
            if not target.exists() or not target.is_file():
                self._send_json(404, {"error": "not found"})
                return
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
            }.get(target.suffix, "application/octet-stream")
            self._send_bytes(200, ctype, target.read_bytes())

        # ---- 路由 ----
        def do_GET(self):
            try:
                path = urlparse(self.path).path
                if path == "/api/health":
                    self._send_json(200, {"ok": True})
                elif path == "/api/sessions":
                    self._send_json(200, gateway.list_sessions())
                elif _RE_MESSAGES.match(path):
                    sid = _RE_MESSAGES.match(path).group(1)
                    self._send_json(200, gateway.get_messages(sid))
                elif _RE_ATTACH.match(path):
                    sid = _RE_ATTACH.match(path).group(1)
                    self._send_json(200, gateway.list_attachments(sid))
                elif path == "/api/tasks":
                    self._send_json(200, gateway.list_tasks())
                elif _RE_TASK.match(path):
                    tid = _RE_TASK.match(path).group(1)
                    self._send_json(200, gateway.get_task(tid))
                elif gateway.downloads is not None and gateway.downloads.handle_get(self, path):
                    return  # 由下载注册表处理（Step 8）
                else:
                    self._serve_static(path)
            except GatewayError as e:
                self._send_json(e.status, {"error": e.message})
            except Exception as e:  # 单请求异常不影响 server
                self._send_json(500, {"error": f"内部错误: {e}"})

        def do_POST(self):
            try:
                path = urlparse(self.path).path
                if path == "/api/chat":
                    body = self._read_json()
                    self._send_json(200, gateway.chat(body.get("sessionId"), body.get("message", "")))
                elif path == "/api/sessions":
                    body = self._read_json()
                    self._send_json(200, gateway.create_session(body.get("title")))
                elif _RE_ATTACH.match(path):
                    sid = _RE_ATTACH.match(path).group(1)
                    body = self._read_json()
                    self._send_json(200, gateway.upload_attachment(sid, body))
                elif path == "/api/tasks":
                    body = self._read_json()
                    self._send_json(200, gateway.create_task(body))
                elif _RE_TASK_CANCEL.match(path):
                    tid = _RE_TASK_CANCEL.match(path).group(1)
                    self._send_json(200, gateway.cancel_task(tid))
                elif gateway.downloads is not None and gateway.downloads.handle_post(self, path):
                    return
                else:
                    self._send_json(404, {"error": "not found"})
            except GatewayError as e:
                self._send_json(e.status, {"error": e.message})
            except Exception as e:
                self._send_json(500, {"error": f"内部错误: {e}"})

    return Handler


def main():
    import sys
    import threading
    from pathlib import Path

    from .agent import build_runtime
    from .attachments import AttachmentStore
    from .config import (
        ATTACHMENT_MAX_BYTES, GATEWAY_HOST, GATEWAY_PORT,
        SCHEDULER_POLL_SECONDS, SCHEDULER_TASKS_FILE, SESSIONS_DIR, WEB_DIR,
    )
    from .scheduler import Scheduler, TaskStore

    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    try:
        runtime = build_runtime()
    except Exception as e:
        print(f"[启动失败] {e}")
        return 1

    attachment_store = AttachmentStore(SESSIONS_DIR, ATTACHMENT_MAX_BYTES)
    # 共享执行锁：Gateway 的聊天请求与 Scheduler 的定时任务都通过它串行进入 agent loop，
    # 避免并发写坏同一个 session。
    run_lock = threading.Lock()
    task_store = TaskStore(SCHEDULER_TASKS_FILE)
    scheduler = Scheduler(task_store, runtime, run_lock, poll_seconds=SCHEDULER_POLL_SECONDS)
    gateway = Gateway(runtime, attachment_store, Path(WEB_DIR),
                      scheduler=scheduler, run_lock=run_lock)

    scheduler.start()
    print(f"⏰ Scheduler 已启动（轮询间隔 {SCHEDULER_POLL_SECONDS}s，任务文件 {SCHEDULER_TASKS_FILE}）")
    try:
        gateway.serve(GATEWAY_HOST, GATEWAY_PORT)
    finally:
        scheduler.stop()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
