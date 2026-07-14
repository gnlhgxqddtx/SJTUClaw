"""
DD-SJTUClaw 图形化界面入口（基于 PyQt5）

启动方式：python -m source.gui
"""

import sys
import threading
import os
from datetime import datetime
from typing import Optional

from PyQt5 import QtWidgets, QtCore, QtGui

from .agent import build_runtime
from .approval import ApprovalManager
from .config import DEFAULT_MODEL, APPROVAL_TIMEOUT_SECONDS
from .memory_store import MemoryError
from .session_manager import SessionError
from .workspace import WorkspaceError, normalize_workspace


class GUIContext:
    def __init__(self):
        self.runtime = None
        self.approval_manager = None
        self.event_buffer = []
        self._event_lock = threading.Lock()

    def init_runtime(self):
        self.runtime = build_runtime()
        self.approval_manager = ApprovalManager(timeout=APPROVAL_TIMEOUT_SECONDS)

    def clear_events(self):
        with self._event_lock:
            events = list(self.event_buffer)
            self.event_buffer = []
        return events


_gui_ctx = GUIContext()


def _event_collector(kind, data):
    with _gui_ctx._event_lock:
        _gui_ctx.event_buffer.append({
            "kind": kind,
            "time": datetime.now().strftime("%H:%M:%S"),
            "data": data,
        })


def _gui_approval(tool, args):
    if _gui_ctx.approval_manager is None:
        return False, "审批管理器未初始化"
    approval_id = _gui_ctx.approval_manager.create(
        _gui_ctx.runtime.manager.current_id, tool, args
    )["approvalId"]
    approved, reason = _gui_ctx.approval_manager.wait(approval_id)
    return approved, reason


class SessionWidget(QtWidgets.QWidget):
    _status_signal = QtCore.pyqtSignal(str)
    _result_signal = QtCore.pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reanim_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reanim")
        self._setup_ui()
        self._status_signal.connect(self._update_status_image)
        self._result_signal.connect(self._on_run_result)

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(3)

        self.session_list = QtWidgets.QListWidget()
        self.session_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.session_list.itemDoubleClicked.connect(self._on_item_double_click)
        self.session_list.keyPressEvent = self._on_key_press
        left_layout.addWidget(QtWidgets.QLabel("会话列表"))
        left_layout.addWidget(self.session_list)

        self.new_btn = QtWidgets.QPushButton("新建会话")
        self.new_btn.clicked.connect(self._on_new_session)
        left_layout.addWidget(self.new_btn)

        self.compact_btn = QtWidgets.QPushButton("压缩会话")
        self.compact_btn.clicked.connect(self._on_compact)
        left_layout.addWidget(self.compact_btn)

        mouse_layout = QtWidgets.QHBoxLayout()
        mouse_layout.addStretch()
        self.status_label = QtWidgets.QLabel()
        self.status_label.setFixedSize(64, 64)
        self._load_status_image("default")
        mouse_layout.addWidget(self.status_label)
        mouse_layout.addStretch()
        left_layout.addLayout(mouse_layout)

        layout.addWidget(self.left_panel, 1)

        self.right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(3)

        self.title_label = QtWidgets.QLabel("当前会话：")
        right_layout.addWidget(self.title_label)

        self.content_text = QtWidgets.QTextEdit()
        self.content_text.setReadOnly(True)
        right_layout.addWidget(self.content_text)

        self.input_layout = QtWidgets.QHBoxLayout()
        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.returnPressed.connect(self._on_send)
        self.send_btn = QtWidgets.QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send)
        self.input_layout.addWidget(self.input_edit)
        self.input_layout.addWidget(self.send_btn)
        right_layout.addLayout(self.input_layout)

        layout.addWidget(self.right_panel, 2)

        self._editing_item = None
        self._editing_session_id = None

    def _load_status_image(self, status):
        img_path = os.path.join(self._reanim_dir, f"{status}.png")
        if os.path.exists(img_path):
            pixmap = QtGui.QPixmap(img_path)
            self.status_label.setPixmap(pixmap.scaled(64, 64, QtCore.Qt.KeepAspectRatio))

    def _update_status_image(self, status):
        self._load_status_image(status)

    def refresh(self):
        if _gui_ctx.runtime is None:
            return
        self.session_list.clear()
        sessions = _gui_ctx.runtime.manager.list_sorted()
        current_id = _gui_ctx.runtime.manager.current_id
        for s in sessions:
            marker = "⭐️ " if s.session_id == current_id else ""
            item = QtWidgets.QListWidgetItem(f"{marker}{s.title} ({s.session_id})")
            item.setData(QtCore.Qt.UserRole, s.session_id)
            self.session_list.addItem(item)
        self._update_right_panel()
        self._load_status_image("default")

    def _update_right_panel(self):
        if _gui_ctx.runtime is None:
            return
        session = _gui_ctx.runtime.manager.current
        self.title_label.setText(f"当前会话：{session.title} ({session.session_id})")
        content = ""
        for msg in session.messages:
            role = "用户" if msg["role"] == "user" else "助手"
            content += f"{role}: {msg['content']}\n\n"
        self.content_text.setPlainText(content.strip())

    def _on_item_double_click(self, item):
        if _gui_ctx.runtime is None:
            return
        session_id = item.data(QtCore.Qt.UserRole)
        current_id = _gui_ctx.runtime.manager.current_id
        if session_id == current_id:
            self._start_rename(item)
        else:
            try:
                _gui_ctx.runtime.manager.switch(session_id)
                self.refresh()
            except SessionError as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))

    def _on_key_press(self, event):
        if event.key() == QtCore.Qt.Key_Delete:
            self._on_delete()
        elif event.key() == QtCore.Qt.Key_Return or event.key() == QtCore.Qt.Key_Enter:
            self._on_enter_key()
        else:
            super(type(self.session_list), self.session_list).keyPressEvent(event)

    def _on_delete(self):
        if _gui_ctx.runtime is None:
            return
        item = self.session_list.currentItem()
        if item is None:
            return
        session_id = item.data(QtCore.Qt.UserRole)
        current_id = _gui_ctx.runtime.manager.current_id
        if session_id != current_id:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "确认删除", f"确定删除会话 '{item.text()}' 吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            try:
                _gui_ctx.runtime.manager.delete(session_id)
                self.refresh()
            except SessionError as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))

    def _on_enter_key(self):
        if self._editing_item:
            self._finish_rename()
        else:
            item = self.session_list.currentItem()
            if item:
                session_id = item.data(QtCore.Qt.UserRole)
                current_id = _gui_ctx.runtime.manager.current_id
                if session_id == current_id:
                    self._start_rename(item)

    def _start_rename(self, item):
        session_id = item.data(QtCore.Qt.UserRole)
        full_text = item.text()
        title_part = full_text.split(" (")[0].replace("⭐️ ", "")
        editable_item = QtWidgets.QListWidgetItem(title_part)
        editable_item.setData(QtCore.Qt.UserRole, session_id)
        row = self.session_list.row(item)
        self.session_list.takeItem(row)
        self.session_list.insertItem(row, editable_item)
        self.session_list.setCurrentItem(editable_item)
        self._editing_item = editable_item
        self._editing_session_id = session_id
        self.session_list.editItem(editable_item)

    def _finish_rename(self):
        if self._editing_item and self._editing_session_id:
            new_title = self._editing_item.text().strip()
            if new_title:
                try:
                    _gui_ctx.runtime.manager.rename(self._editing_session_id, new_title)
                except SessionError as e:
                    QtWidgets.QMessageBox.warning(self, "错误", str(e))
            self._editing_item = None
            self._editing_session_id = None
            self.refresh()

    def _on_new_session(self):
        if _gui_ctx.runtime is None:
            return
        title, ok = QtWidgets.QInputDialog.getText(self, "新建会话", "输入会话标题：")
        if ok and title.strip():
            try:
                _gui_ctx.runtime.manager.new_session(title.strip())
                self.refresh()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))
        elif ok:
            try:
                _gui_ctx.runtime.manager.new_session("新会话")
                self.refresh()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))

    def _on_compact(self):
        if _gui_ctx.runtime is None:
            return
        from .compaction import compact
        from .config import COMPACT_RECENT_MESSAGES
        session = _gui_ctx.runtime.manager.current
        result = compact(_gui_ctx.runtime.client, session, COMPACT_RECENT_MESSAGES)
        if result["applied"]:
            try:
                _gui_ctx.runtime.manager.save(session)
            except SessionError as e:
                QtWidgets.QMessageBox.warning(self, "警告", f"压缩成功但保存失败: {e}")
            QtWidgets.QMessageBox.information(self, "压缩完成",
                f"旧消息数: {result['old_count']}\n保留消息数: {result['recent_count']}\n\n摘要:\n{result['summary']}")
            self.refresh()
        else:
            QtWidgets.QMessageBox.information(self, "无需压缩", result.get("reason", ""))

    def _on_send(self):
        if _gui_ctx.runtime is None:
            return
        message = self.input_edit.text().strip()
        if not message:
            return

        self.input_edit.clear()
        self._status_signal.emit("think")

        session = _gui_ctx.runtime.manager.current

        def run_thread():
            try:
                _gui_ctx.clear_events()
                result = _gui_ctx.runtime.run(
                    session, message, on_event=_event_collector,
                    approval_fn=_gui_approval, skill_name=None
                )
                events = _gui_ctx.clear_events()
                trace_lines = []
                for ev in events:
                    kind = ev["kind"]
                    time = ev["time"]
                    data = ev["data"]
                    if kind == "tool_call":
                        trace_lines.append(f"🔧 [{time}] {data['tool']} {data['args']}")
                    elif kind == "approval":
                        trace_lines.append(f"⚠️ [{time}] 需要审批: {data['tool']}")
                    elif kind == "approval_result":
                        verb = "✅ 批准" if data["approved"] else "❌ 拒绝"
                        trace_lines.append(f"📋 [{time}] {data['tool']} {verb}")
                    elif kind == "tool_result":
                        status = "成功" if data["ok"] else "失败"
                        text = data["output"] if data["ok"] else data["error"]
                        preview = text if len(text) <= 500 else text[:500] + " ...(已截断)"
                        trace_lines.append(f"📊 [{time}] {data['tool']} ({status}): {preview}")
                    elif kind == "skill":
                        src = "用户指定" if data.get("source") == "explicit" else "模型自主"
                        trace_lines.append(f"🧩 [{time}] 已加载技能: {data['skill']}（{src}）")
                    elif kind == "compaction":
                        if data.get("applied"):
                            trace_lines.append(f"📦 [{time}] 已压缩: 旧消息={data['old_count']}, 保留={data['recent_count']}")

                if trace_lines:
                    result_text = "\n".join(trace_lines) + "\n\n" + ("=" * 60) + "\n" + (result or "调用失败")
                else:
                    result_text = result or "调用失败"

                self._result_signal.emit(result_text, True)
            except Exception as e:
                self._result_signal.emit(f"调用失败: {e}", False)

        thread = threading.Thread(target=run_thread, daemon=True)
        thread.start()

    def _on_run_result(self, result_text, success):
        if success:
            self._status_signal.emit("success")
        else:
            self._status_signal.emit("fail")
        self.refresh()


class MemoryWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(3)

        self.memory_list = QtWidgets.QListWidget()
        self.memory_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.memory_list.itemDoubleClicked.connect(self._on_item_double_click)
        self.memory_list.keyPressEvent = self._on_key_press
        left_layout.addWidget(QtWidgets.QLabel("记忆列表"))
        left_layout.addWidget(self.memory_list)

        self.new_btn = QtWidgets.QPushButton("新建记忆")
        self.new_btn.clicked.connect(self._on_new_memory)
        left_layout.addWidget(self.new_btn)

        layout.addWidget(self.left_panel, 1)

        self.right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(3)

        self.content_label = QtWidgets.QLabel("记忆内容：")
        right_layout.addWidget(self.content_label)

        self.content_edit = QtWidgets.QTextEdit()
        right_layout.addWidget(self.content_edit)

        self.save_btn = QtWidgets.QPushButton("保存")
        self.save_btn.clicked.connect(self._on_save)
        right_layout.addWidget(self.save_btn)

        layout.addWidget(self.right_panel, 2)

        self._editing_item = None

    def refresh(self):
        if _gui_ctx.runtime is None:
            return
        self.memory_list.clear()
        memories = _gui_ctx.runtime.memory_store.list()
        for m in memories:
            item = QtWidgets.QListWidgetItem(f"{m['id']}: {m['content'][:30]}...")
            item.setData(QtCore.Qt.UserRole, m)
            self.memory_list.addItem(item)
        self.content_edit.clear()

    def _on_item_double_click(self, item):
        if _gui_ctx.runtime is None:
            return
        memory = item.data(QtCore.Qt.UserRole)
        self.content_edit.setPlainText(memory["content"])

    def _on_key_press(self, event):
        if event.key() == QtCore.Qt.Key_Delete:
            self._on_delete()
        elif event.key() == QtCore.Qt.Key_Return or event.key() == QtCore.Qt.Key_Enter:
            self._on_enter_key()
        else:
            super(type(self.memory_list), self.memory_list).keyPressEvent(event)

    def _on_delete(self):
        if _gui_ctx.runtime is None:
            return
        item = self.memory_list.currentItem()
        if item is None:
            return
        memory = item.data(QtCore.Qt.UserRole)
        reply = QtWidgets.QMessageBox.question(
            self, "确认删除", f"确定删除记忆 '{memory['id']}' 吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            try:
                _gui_ctx.runtime.memory_store.delete(memory["id"])
                self.refresh()
            except MemoryError as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))

    def _on_enter_key(self):
        if self._editing_item:
            self._finish_rename()
        else:
            item = self.memory_list.currentItem()
            if item:
                self._start_rename(item)

    def _start_rename(self, item):
        memory = item.data(QtCore.Qt.UserRole)
        content_part = item.text().split(": ", 1)[-1]
        editable_item = QtWidgets.QListWidgetItem(content_part)
        editable_item.setData(QtCore.Qt.UserRole, memory)
        row = self.memory_list.row(item)
        self.memory_list.takeItem(row)
        self.memory_list.insertItem(row, editable_item)
        self.memory_list.setCurrentItem(editable_item)
        self._editing_item = editable_item
        self.memory_list.editItem(editable_item)

    def _finish_rename(self):
        if self._editing_item:
            memory = self._editing_item.data(QtCore.Qt.UserRole)
            new_content = self._editing_item.text().strip()
            if new_content:
                try:
                    _gui_ctx.runtime.memory_store.add(new_content)
                    _gui_ctx.runtime.memory_store.delete(memory["id"])
                except MemoryError as e:
                    QtWidgets.QMessageBox.warning(self, "错误", str(e))
            self._editing_item = None
            self.refresh()

    def _on_new_memory(self):
        if _gui_ctx.runtime is None:
            return
        content, ok = QtWidgets.QInputDialog.getMultiLineText(self, "新建记忆", "输入记忆内容：")
        if ok and content.strip():
            try:
                _gui_ctx.runtime.memory_store.add(content.strip())
                self.refresh()
            except MemoryError as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))

    def _on_save(self):
        if _gui_ctx.runtime is None:
            return
        item = self.memory_list.currentItem()
        if item is None:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择一个记忆")
            return
        memory = item.data(QtCore.Qt.UserRole)
        new_content = self.content_edit.toPlainText().strip()
        if not new_content:
            QtWidgets.QMessageBox.warning(self, "提示", "内容不能为空")
            return
        try:
            _gui_ctx.runtime.memory_store.add(new_content)
            _gui_ctx.runtime.memory_store.delete(memory["id"])
            self.refresh()
            QtWidgets.QMessageBox.information(self, "成功", "记忆已保存")
        except MemoryError as e:
            QtWidgets.QMessageBox.warning(self, "错误", str(e))


class WorkspaceWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(3)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.file_list.itemDoubleClicked.connect(self._on_item_double_click)
        self.file_list.keyPressEvent = self._on_key_press
        left_layout.addWidget(QtWidgets.QLabel("文件列表"))
        left_layout.addWidget(self.file_list)

        self.new_btn = QtWidgets.QPushButton("新建文件")
        self.new_btn.clicked.connect(self._on_new_file)
        left_layout.addWidget(self.new_btn)

        self.set_ws_btn = QtWidgets.QPushButton("设置 Workspace")
        self.set_ws_btn.clicked.connect(self._on_set_workspace)
        left_layout.addWidget(self.set_ws_btn)

        layout.addWidget(self.left_panel, 1)

        self.right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(3)

        self.ws_label = QtWidgets.QLabel("当前 Workspace：未设置")
        right_layout.addWidget(self.ws_label)

        self.content_label = QtWidgets.QLabel("文件内容：")
        right_layout.addWidget(self.content_label)

        self.content_edit = QtWidgets.QTextEdit()
        right_layout.addWidget(self.content_edit)

        self.save_btn = QtWidgets.QPushButton("保存文件")
        self.save_btn.clicked.connect(self._on_save)
        right_layout.addWidget(self.save_btn)

        layout.addWidget(self.right_panel, 2)

        self._editing_item = None

    def refresh(self):
        if _gui_ctx.runtime is None:
            return
        session = _gui_ctx.runtime.manager.current
        ws = getattr(session, "workspace", None)
        self.ws_label.setText(f"当前 Workspace：{ws or '未设置'}")
        self.file_list.clear()
        if ws:
            try:
                for f in os.listdir(ws):
                    full_path = os.path.join(ws, f)
                    if os.path.isfile(full_path):
                        item = QtWidgets.QListWidgetItem(f)
                        item.setData(QtCore.Qt.UserRole, full_path)
                        self.file_list.addItem(item)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "错误", f"读取目录失败: {e}")

    def _on_item_double_click(self, item):
        full_path = item.data(QtCore.Qt.UserRole)
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.content_edit.setPlainText(content)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"读取文件失败: {e}")

    def _on_key_press(self, event):
        if event.key() == QtCore.Qt.Key_Delete:
            self._on_delete()
        elif event.key() == QtCore.Qt.Key_Return or event.key() == QtCore.Qt.Key_Enter:
            self._on_enter_key()
        else:
            super(type(self.file_list), self.file_list).keyPressEvent(event)

    def _on_delete(self):
        item = self.file_list.currentItem()
        if item is None:
            return
        full_path = item.data(QtCore.Qt.UserRole)
        reply = QtWidgets.QMessageBox.question(
            self, "确认删除", f"确定删除文件 '{item.text()}' 吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            try:
                os.remove(full_path)
                self.refresh()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))

    def _on_enter_key(self):
        if self._editing_item:
            self._finish_rename()
        else:
            item = self.file_list.currentItem()
            if item:
                self._start_rename(item)

    def _start_rename(self, item):
        full_path = item.data(QtCore.Qt.UserRole)
        editable_item = QtWidgets.QListWidgetItem(item.text())
        editable_item.setData(QtCore.Qt.UserRole, full_path)
        row = self.file_list.row(item)
        self.file_list.takeItem(row)
        self.file_list.insertItem(row, editable_item)
        self.file_list.setCurrentItem(editable_item)
        self._editing_item = editable_item
        self.file_list.editItem(editable_item)

    def _finish_rename(self):
        if self._editing_item:
            old_path = self._editing_item.data(QtCore.Qt.UserRole)
            new_name = self._editing_item.text().strip()
            if new_name:
                new_path = os.path.join(os.path.dirname(old_path), new_name)
                try:
                    os.rename(old_path, new_path)
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "错误", str(e))
            self._editing_item = None
            self.refresh()

    def _on_new_file(self):
        if _gui_ctx.runtime is None:
            return
        session = _gui_ctx.runtime.manager.current
        ws = getattr(session, "workspace", None)
        if not ws:
            QtWidgets.QMessageBox.warning(self, "提示", "请先设置 Workspace")
            return
        filename, ok = QtWidgets.QInputDialog.getText(self, "新建文件", "输入文件名：")
        if ok and filename.strip():
            full_path = os.path.join(ws, filename.strip())
            if os.path.exists(full_path):
                QtWidgets.QMessageBox.warning(self, "错误", "文件已存在")
                return
            try:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write("")
                self.refresh()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))

    def _on_set_workspace(self):
        if _gui_ctx.runtime is None:
            return
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择 Workspace 目录")
        if path:
            try:
                ws = normalize_workspace(path)
                session = _gui_ctx.runtime.manager.current
                session.workspace = ws
                _gui_ctx.runtime.manager.save(session)
                self.refresh()
                QtWidgets.QMessageBox.information(self, "成功", f"Workspace 已设置为: {ws}")
            except WorkspaceError as e:
                QtWidgets.QMessageBox.warning(self, "错误", str(e))
            except SessionError as e:
                QtWidgets.QMessageBox.warning(self, "错误", f"保存失败: {e}")

    def _on_save(self):
        if _gui_ctx.runtime is None:
            return
        item = self.file_list.currentItem()
        if item is None:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择一个文件")
            return
        full_path = item.data(QtCore.Qt.UserRole)
        content = self.content_edit.toPlainText()
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            QtWidgets.QMessageBox.information(self, "成功", "文件已保存")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", str(e))


class SkillWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(3)

        self.skill_list = QtWidgets.QListWidget()
        self.skill_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.skill_list.itemDoubleClicked.connect(self._on_item_double_click)
        left_layout.addWidget(QtWidgets.QLabel("技能列表"))
        left_layout.addWidget(self.skill_list)

        self.usage_btn = QtWidgets.QPushButton("查看使用记录")
        self.usage_btn.clicked.connect(self._on_usage)
        left_layout.addWidget(self.usage_btn)

        layout.addWidget(self.left_panel, 1)

        self.right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(3)

        self.name_label = QtWidgets.QLabel("技能名称：")
        right_layout.addWidget(self.name_label)

        self.desc_label = QtWidgets.QLabel("描述：")
        right_layout.addWidget(self.desc_label)

        self.content_edit = QtWidgets.QTextEdit()
        self.content_edit.setReadOnly(True)
        right_layout.addWidget(self.content_edit)

        self.resources_label = QtWidgets.QLabel("资源文件：")
        right_layout.addWidget(self.resources_label)

        layout.addWidget(self.right_panel, 2)

    def refresh(self):
        if _gui_ctx.runtime is None:
            return
        self.skill_list.clear()
        skills = _gui_ctx.runtime.skill_registry.list() if _gui_ctx.runtime.skill_registry else []
        for s in skills:
            item = QtWidgets.QListWidgetItem(s["name"])
            item.setData(QtCore.Qt.UserRole, s["name"])
            self.skill_list.addItem(item)
        self.name_label.setText("技能名称：")
        self.desc_label.setText("描述：")
        self.content_edit.clear()
        self.resources_label.setText("资源文件：")

    def _on_item_double_click(self, item):
        if _gui_ctx.runtime is None:
            return
        name = item.data(QtCore.Qt.UserRole)
        if not _gui_ctx.runtime.skill_registry or not _gui_ctx.runtime.skill_registry.has(name):
            QtWidgets.QMessageBox.warning(self, "错误", f"不存在名为 '{name}' 的技能")
            return
        try:
            skill = _gui_ctx.runtime.skill_registry.load(name)
            self.name_label.setText(f"技能名称：{skill.name}")
            self.desc_label.setText(f"描述：{skill.description}")
            self.content_edit.setPlainText(skill.instructions or "")
            if skill.resources:
                self.resources_label.setText("资源文件：" + ", ".join(skill.resources))
            else:
                self.resources_label.setText("资源文件：无")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"加载失败: {e}")

    def _on_usage(self):
        if _gui_ctx.runtime is None:
            return
        session = _gui_ctx.runtime.manager.current
        usages = getattr(session, "skill_usages", [])
        if not usages:
            QtWidgets.QMessageBox.information(self, "技能使用记录", "本会话暂无技能使用记录")
            return
        lines = []
        for u in usages:
            src = u.get("source", "")
            reason = f" 理由={u['reason']}" if u.get("reason") else ""
            lines.append(f"{u.get('usedAt', '')} · {u.get('skill', '')}（{src}）{reason}")
            task = u.get("task", "")
            if task:
                lines.append(f"      任务: {task}")
        QtWidgets.QMessageBox.information(self, "技能使用记录", "\n".join(lines))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DD-SJTUClaw - 智能对话助手")
        self.setGeometry(100, 100, 1000, 700)
        self._setup_ui()
        self._init_runtime()

    def _setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        layout = QtWidgets.QVBoxLayout(central_widget)

        button_bar = QtWidgets.QWidget()
        button_layout = QtWidgets.QHBoxLayout(button_bar)
        button_layout.setContentsMargins(5, 5, 5, 5)
        button_layout.setSpacing(5)

        self.session_btn = QtWidgets.QPushButton("管理会话")
        self.session_btn.clicked.connect(self._show_session)
        button_layout.addWidget(self.session_btn)

        self.workspace_btn = QtWidgets.QPushButton("管理工作区")
        self.workspace_btn.clicked.connect(self._show_workspace)
        button_layout.addWidget(self.workspace_btn)

        self.memory_btn = QtWidgets.QPushButton("管理记忆")
        self.memory_btn.clicked.connect(self._show_memory)
        button_layout.addWidget(self.memory_btn)

        self.skill_btn = QtWidgets.QPushButton("管理技能")
        self.skill_btn.clicked.connect(self._show_skill)
        button_layout.addWidget(self.skill_btn)

        layout.addWidget(button_bar)

        self.stacked_widget = QtWidgets.QStackedWidget()
        layout.addWidget(self.stacked_widget)

        self.session_widget = SessionWidget()
        self.stacked_widget.addWidget(self.session_widget)

        self.memory_widget = MemoryWidget()
        self.stacked_widget.addWidget(self.memory_widget)

        self.workspace_widget = WorkspaceWidget()
        self.stacked_widget.addWidget(self.workspace_widget)

        self.skill_widget = SkillWidget()
        self.stacked_widget.addWidget(self.skill_widget)

        self.stacked_widget.setCurrentIndex(0)

    def _init_runtime(self):
        try:
            _gui_ctx.init_runtime()
            self.session_widget.refresh()
            self.memory_widget.refresh()
            self.workspace_widget.refresh()
            self.skill_widget.refresh()
            QtWidgets.QMessageBox.information(self, "初始化完成",
                f"运行时初始化成功\n模型: {DEFAULT_MODEL}\n会话: {len(_gui_ctx.runtime.manager.sessions)} 个\n技能: {len(_gui_ctx.runtime.skill_registry.list())} 个")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "初始化失败", str(e))
            sys.exit(1)

    def _show_session(self):
        self.stacked_widget.setCurrentIndex(0)
        self.session_widget.refresh()

    def _show_workspace(self):
        self.stacked_widget.setCurrentIndex(2)
        self.workspace_widget.refresh()

    def _show_memory(self):
        self.stacked_widget.setCurrentIndex(1)
        self.memory_widget.refresh()

    def _show_skill(self):
        self.stacked_widget.setCurrentIndex(3)
        self.skill_widget.refresh()


def main():
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    sys.exit(main())
