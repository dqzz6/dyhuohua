"""软件主界面：左侧是内置浏览器，右侧是状态、设置、好友列表与运行日志。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .embedded import EmbeddedBrowser
from .friends import read_cache
from .logger import recent_lines
from .paths import DATA_DIR
from .service import Application

AVATAR_SIZE = QSize(34, 34)


class MainWindow(QMainWindow):
    """面向普通用户的操作界面，耗时动作都放到后台线程执行。"""

    send_finished = Signal(dict)
    friends_ready = Signal(dict)

    def __init__(self, app: Application, browser: EmbeddedBrowser):
        super().__init__()
        self.app = app
        self.browser = browser
        self._closing = False
        self._busy = False
        self._status = {}
        self._auto_scanned = False
        self._last_logged_in = None

        self.setWindowTitle("抖音自动消息")
        self.resize(1420, 900)
        self.setMinimumSize(1100, 760)

        self._build_widgets()
        self._load_form()
        self._load_cached_friends()
        self._connect_signals()
        self._start_polling()

    # ---------- 界面搭建 ----------
    def _build_widgets(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.browser)
        splitter.addWidget(self._build_panel())
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([930, 470])
        self.setCentralWidget(splitter)

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(430)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(self._build_status_group())
        layout.addWidget(self._build_settings_group())
        layout.addWidget(self._build_friends_group(), 3)
        layout.addLayout(self._build_buttons())
        layout.addWidget(self._build_log_group(), 2)
        return panel

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("运行状态")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        self.label_browser = QLabel("未启动")
        self.label_login = QLabel("未知")
        self.label_today = QLabel("未发送")
        self.label_next = QLabel("-")
        bold = QFont()
        bold.setBold(True)
        for label in (self.label_browser, self.label_login, self.label_today, self.label_next):
            label.setFont(bold)

        rows = (
            ("内置浏览器", self.label_browser),
            ("登录状态", self.label_login),
            ("今日发送", self.label_today),
            ("下次发送", self.label_next),
        )
        for row, (title, widget) in enumerate(rows):
            grid.addWidget(QLabel(title), row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        return group

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("发送设置")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self.input_time = QLineEdit()
        self.input_time.setFixedWidth(72)
        self.check_daily = QCheckBox("每天定时发送")
        self.check_missed = QCheckBox("错过时间后不限时补发")
        self.input_message = QPlainTextEdit()
        self.input_message.setFixedHeight(78)

        grid.addWidget(QLabel("发送时间"), 0, 0)
        grid.addWidget(self.input_time, 0, 1)
        grid.addWidget(QLabel("HH:MM（北京时间）"), 0, 2)
        grid.addWidget(self.check_daily, 1, 1, 1, 2)
        grid.addWidget(self.check_missed, 2, 1, 1, 2)
        grid.addWidget(QLabel("消息内容"), 3, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.input_message, 3, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return group

    def _build_friends_group(self) -> QGroupBox:
        group = QGroupBox("好友列表（勾选要每天发送的好友）")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        self.list_friends = QListWidget()
        self.list_friends.setIconSize(AVATAR_SIZE)
        self.list_friends.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list_friends.setUniformItemSizes(True)
        self.list_friends.setStyleSheet("QListWidget::item { height: 42px; }")
        layout.addWidget(self.list_friends, 1)

        row = QHBoxLayout()
        self.button_scan = QPushButton("读取好友列表")
        self.button_all = QPushButton("全选")
        self.button_none = QPushButton("清空选择")
        for button in (self.button_scan, self.button_all, self.button_none):
            row.addWidget(button)
        layout.addLayout(row)

        self.label_friends = QLabel("还没有读取好友列表，登录后会自动读取一次。")
        self.label_friends.setWordWrap(True)
        layout.addWidget(self.label_friends)
        return group

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.button_save = QPushButton("保存设置")
        self.button_reload = QPushButton("打开私信页")
        self.button_send = QPushButton("立即给选中好友发送")
        self.button_data = QPushButton("打开数据目录")
        for button in (self.button_save, self.button_reload, self.button_send, self.button_data):
            row.addWidget(button)
        return row

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("运行日志")
        layout = QVBoxLayout(group)
        self.text_log = QPlainTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.text_log)
        return group

    def _connect_signals(self) -> None:
        self.button_save.clicked.connect(self._save_clicked)
        self.button_reload.clicked.connect(self._reload_clicked)
        self.button_send.clicked.connect(self._send_now_clicked)
        self.button_data.clicked.connect(self._open_data_dir)
        self.button_scan.clicked.connect(lambda: self._scan_friends(auto=False))
        self.button_all.clicked.connect(lambda: self._set_all_checked(True))
        self.button_none.clicked.connect(lambda: self._set_all_checked(False))
        self.send_finished.connect(self._on_send_finished)
        self.friends_ready.connect(self._on_friends_ready)

    # ---------- 表单数据 ----------
    def _load_form(self) -> None:
        config = self.app.config_snapshot()
        self.input_time.setText(str(config.get("send_time") or "09:00"))
        self.check_daily.setChecked(bool(config.get("daily_enabled")))
        self.check_missed.setChecked(bool(config.get("missed_run")))
        self.input_message.setPlainText(str(config.get("message") or ""))
        self._selected_names = set(str(name) for name in (config.get("target_names") or []))

    def _load_cached_friends(self) -> None:
        cached = read_cache()
        if cached:
            self._fill_friends(cached)
            self.label_friends.setText(f"已载入上次读取的好友（{len(cached)} 位），可点「读取好友列表」刷新。")

    def _fill_friends(self, friends) -> None:
        selected = getattr(self, "_selected_names", set())
        self.list_friends.clear()
        for item in friends:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            node = QListWidgetItem(name)
            node.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            node.setCheckState(Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked)
            avatar_path = str(item.get("avatarPath") or item.get("avatar_path") or "")
            if avatar_path and Path(avatar_path).exists():
                node.setIcon(QIcon(avatar_path))
            node.setData(Qt.ItemDataRole.UserRole, name)
            self.list_friends.addItem(node)
        self.label_friends.setText(f"共 {self.list_friends.count()} 位好友，已勾选 {len(self._checked_names())} 位。")

    def _checked_names(self) -> list:
        names = []
        for index in range(self.list_friends.count()):
            item = self.list_friends.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                names.append(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))
        return names

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.list_friends.count()):
            self.list_friends.item(index).setCheckState(state)
        self.label_friends.setText(f"共 {self.list_friends.count()} 位好友，已勾选 {len(self._checked_names())} 位。")

    # ---------- 交互动作 ----------
    def _save_clicked(self) -> None:
        if self._save():
            QMessageBox.information(self, "提示", "设置已保存")

    def _save(self) -> bool:
        names = self._checked_names()
        try:
            self.app.update_config(
                {
                    "target_names": names,
                    "message": self.input_message.toPlainText().strip(),
                    "send_time": self.input_time.text().strip(),
                    "daily_enabled": self.check_daily.isChecked(),
                    "missed_run": self.check_missed.isChecked(),
                }
            )
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"保存设置时出错：{exc}")
            return False
        self._selected_names = set(names)
        self.label_friends.setText(f"共 {self.list_friends.count()} 位好友，已勾选 {len(names)} 位。")
        return True

    def _reload_clicked(self) -> None:
        self.browser.load_url(self.app.start_url())

    def _scan_friends(self, auto: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self.button_scan.setEnabled(False)
        if not auto:
            self.label_friends.setText("正在读取好友列表，需要滚动到底部，请稍候……")

        def worker() -> None:
            try:
                friends = self.app.submit(self.app.scan_friends()).result(timeout=900)
                self.friends_ready.emit({"ok": True, "friends": friends, "auto": auto})
            except Exception as exc:
                self.friends_ready.emit({"ok": False, "detail": str(exc), "auto": auto})

        threading.Thread(target=worker, name="scan-friends", daemon=True).start()

    def _on_friends_ready(self, payload: dict) -> None:
        self._busy = False
        self.button_scan.setEnabled(True)
        if not payload.get("ok"):
            self.label_friends.setText(f"读取好友列表失败：{payload.get('detail')}")
            if not payload.get("auto"):
                QMessageBox.warning(self, "读取失败", str(payload.get("detail")))
            return
        friends = payload.get("friends") or []
        self._fill_friends(friends)
        if not friends:
            self.label_friends.setText("没有读取到好友，请确认内置浏览器已经登录并停留在私信页。")

    def _send_now_clicked(self) -> None:
        if not self._save():
            return
        names = self._checked_names()
        if not names:
            QMessageBox.warning(self, "提示", "请先在好友列表里勾选要发送的好友")
            return
        preview = "、".join(names[:5]) + ("……" if len(names) > 5 else "")
        confirm = QMessageBox.question(self, "确认", f"确定现在给这 {len(names)} 位好友发送吗？\n{preview}")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)

        def worker() -> None:
            try:
                result = self.app.submit(self.app.run_send_now(targets=names, reason="手动")).result(timeout=1800)
            except Exception as exc:
                result = {"ok": False, "detail": str(exc)}
            self.send_finished.emit(result)

        threading.Thread(target=worker, name="manual-send", daemon=True).start()

    def _on_send_finished(self, result: dict) -> None:
        self._set_busy(False)
        detail = str(result.get("detail") or "未知原因")
        if result.get("ok"):
            QMessageBox.information(self, "发送完成", detail)
        else:
            QMessageBox.critical(self, "发送失败", detail)

    def _open_data_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.button_save, self.button_reload, self.button_send, self.button_scan):
            button.setEnabled(not busy)

    # ---------- 状态与日志刷新 ----------
    def _start_polling(self) -> None:
        threading.Thread(target=self._status_loop, name="status-poll", daemon=True).start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1500)
        self._refresh()

    def _status_loop(self) -> None:
        while not self._closing:
            try:
                self._status = self.app.submit(self.app.status()).result(timeout=30)
            except Exception as exc:
                self._status = {"error": str(exc)}
            time.sleep(2)

    def _refresh(self) -> None:
        status = self._status or {}
        if status.get("error"):
            self.label_browser.setText("状态读取失败")
        else:
            browser = status.get("browser") or {}
            self.label_browser.setText("运行中" if browser.get("started") else "未启动")
            logged_in = status.get("logged_in")
            self.label_login.setText("已登录" if logged_in is True else ("未登录" if logged_in is False else "未知"))
            today = status.get("today") or {}
            total = int(today.get("total") or 0)
            sent_count = int(today.get("sentCount") or 0)
            self.label_today.setText(f"{sent_count}/{total} 已发送" if total else "未设置好友")
            next_run = str(status.get("next_run_at") or "")
            self.label_next.setText(next_run[5:16].replace("T", " ") if len(next_run) >= 16 else "-")
            self._maybe_auto_scan(logged_in)

        self.text_log.setPlainText("\n".join(recent_lines(300)))
        self.text_log.verticalScrollBar().setValue(self.text_log.verticalScrollBar().maximum())

    def _maybe_auto_scan(self, logged_in) -> None:
        """登录成功后自动读取一次好友列表。"""
        if logged_in is True and self._last_logged_in is not True and not self._auto_scanned and not self._busy:
            self._auto_scanned = True
            self.label_friends.setText("检测到已登录，正在自动读取好友列表……")
            self._scan_friends(auto=True)
        self._last_logged_in = logged_in

    # ---------- 生命周期 ----------
    def closeEvent(self, event) -> None:  # noqa: N802
        confirm = QMessageBox.question(self, "退出确认", "退出后定时发送将停止，确定要退出吗？")
        if confirm != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._closing = True
        try:
            self.app.stop()
        except Exception:
            pass
        event.accept()
