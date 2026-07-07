"""
DD-SJTUClaw Download 注册表（Step 8）。

Download tool 把 workspace 内一个已有文件注册成一个可由 Gateway 下载的临时入口，
使通过网页等远端经 Gateway 与 claw 交互的用户能够获取 workspace 中的输出文件。

- create_download tool 调用 register()，得到 downloadId / downloadUrl；
- Gateway 通过 handle_get() 在 /api/downloads/<id> 上返回文件内容（带 Content-Disposition）。

下载入口只记录文件路径，不复制文件内容；用户在前端点击下载入口时才真正读取文件。
仅使用 Python 标准库。
"""

import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

_RE_DOWNLOAD = re.compile(r"^/api/downloads/([^/]+)$")


class DownloadError(Exception):
    """下载入口相关错误。"""


class DownloadRegistry:
    """线程安全地登记可下载文件，并处理 Gateway 的下载请求。"""

    def __init__(self):
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def register(self, file_path):
        """登记一个已存在的文件，返回 {downloadId, downloadUrl, filename}。"""
        p = Path(file_path).resolve()
        if not p.exists() or not p.is_file():
            raise DownloadError(f"文件不存在或不是普通文件: {file_path}")
        with self._lock:
            self._counter += 1
            did = f"dl_{self._counter:03d}"
            self._items[did] = {
                "downloadId": did,
                "path": str(p),
                "filename": p.name,
                "createdAt": datetime.now().isoformat(),
            }
        return {"downloadId": did, "downloadUrl": f"/api/downloads/{did}", "filename": p.name}

    def get(self, download_id):
        with self._lock:
            item = self._items.get(download_id)
            return dict(item) if item else None

    # ---------- 供 Gateway 路由调用 ----------
    def handle_get(self, handler, path):
        """匹配 /api/downloads/<id> 时直接写响应并返回 True；否则返回 False。
        handler 为 Gateway 的 BaseHTTPRequestHandler 实例。"""
        m = _RE_DOWNLOAD.match(path)
        if not m:
            return False
        did = m.group(1)
        item = self.get(did)
        if item is None:
            handler._send_json(404, {"error": "下载入口不存在或已失效"})
            return True
        p = Path(item["path"])
        if not p.exists() or not p.is_file():
            handler._send_json(404, {"error": "文件已不存在"})
            return True
        try:
            data = p.read_bytes()
        except OSError as e:
            handler._send_json(500, {"error": f"读取文件失败: {e}"})
            return True
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        # 用 RFC 5987 编码文件名，兼容中文等非 ASCII 文件名。
        handler.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + quote(item["filename"]),
        )
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True

    def handle_post(self, handler, path):
        """下载入口不接受 POST。"""
        return False
