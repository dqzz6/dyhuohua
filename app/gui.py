"""软件主界面：左侧是内置浏览器，右侧是设置、状态与运行日志。"""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .embedded import EmbeddedBrowser
from .logger import recent_lines
from .paths import DATA_DIR
from .service import Application


class MainWindow(QMainWindow):
    """面向普通用户的操作界面，耗时动作都放到后台线程执行。"""

    send_finished = Signal(dict)

    def __init__(self, app: Application, browser: EmbeddedBrowser):
        super().__init__()
        self.app = app
        self.browser = browser
        self._closing = False
        self._status = {}
        self._busy = False

        self.setWindowTitle("抖音自动消息")
        self.resize(1360, 860)

        self._build_widgets()
        self._load_form()
        self._connect_signals()
        self._start_polling()

    # ---------- 界面搭建 ----------
    def _build_widgets(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.browser)
        splitter.addWidget(self._build_panel())
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([960, 400])
        self.setCentralWidget(splitter)

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(self._build_status_group())
        layout.addWidget(self._build_settings_group())
        layout.addLayout(self._build_buttons())
        layout.addWidget(self._build_log_group(), 1)
        return panel

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("运行状态")
        grid = QGridLayout(group)
        self.label_browser = QLabel("未启动")
        self.label_login = QLabel("未知")
        self.label_today = QLabel("未发送")
        self.label_next = QLabel("-")
        bold = QFont()
        bold.setBold(True)
        for label in (self.label_browser, self.label_login, self.label_today, self.label_next):
            label.setFont(bold)
        for column, (title, widget) in enumerate(
            (
                ("内置浏览器", self.label_browser),
                ("登录状态", self.label_login),
                ("今日发送", self.label_today),
                ("下次发送", self.label_next),
            )
        ):
            grid.addWidget(QLabel(title), column // 2 * 2, column % 2)
            grid.addWidget(widget, column // 2 * 2, column % 2 + 1)
        return group

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("发送设置")
        grid = QGridLayout(group)

        self.input_target = QLineEdit()
        self.input_target.setPlaceholderText("要和聊天列表里显示的名字完全一致")
        self.input_time = QLineEdit()
        self.input_time.setFixedWidth(70)
        self.check_daily = QCheckBox("每天定时发送")
        self.check_missed = QCheckBox("错过时间后不限时补发")
        self.input_message = QPlainTextEdit()
        self.input_message.setFixedHeight(88)

        grid.addWidget(QLabel("目标好友"), 0, 0)
        grid.addWidget(self.input_target, 0, 1, 1, 3)
        grid.addWidget(QLabel("发送时间"), 1, 0)
        grid.addWidget(self.input_time, 1, 1)
        grid.addWidget(QLabel("HH:MM（北京时间）"), 1, 2, 1, 2)
        grid.addWidget(self.check_daily, 2, 1, 1, 3)
        grid.addWidget(self.check_missed, 3, 1, 1, 3)
        grid.addWidget(QLabel("消息内容"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.input_message, 4, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)
        return group

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.button_save = QPushButton("保存设置")
        self.button_reload = QPushButton("打开私信页")
        self.button_send = QPushButton("立即发送一次")
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
        self.send_finished.connect(self._on_send_finished)

    def _load_form(self) -> None:
        config = self.app.config_snapshot()
        self.input_target.setText(str(config.get("target_name") or ""))
        self.input_time.setText(str(config.get("send_time") or "09:00"))
        self.check_daily.setChecked(bool(config.get("daily_enabled")))
        self.check_missed.setChecked(bool(config.get("missed_run")))
        self.input_message.setPlainText(str(config.get("message") or ""))

    # ---------- 交互动作 ----------
    def _save_clicked(self) -> None:
        if self._save():
            QMessageBox.information(self, "提示", "设置已保存")

    def _save(self) -> bool:
        try:
            self.app.update_config(
                {
                    "target_name": self.input_target.text().strip(),
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
        return True

    def _reload_clicked(self) -> None:
        self.browser.load_url(self.app.start_url())

    def _send_now_clicked(self) -> None:
        if not self._save():
            return
        target = self.input_target.text().strip()
        if not target:
            QMessageBox.warning(self, "提示", "请先填写目标好友名称")
            return
        confirm = QMessageBox.question(self, "确认", f"确定现在给「{target}」发送一条消息吗？")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)

        def worker() -> None:
            try:
                result = self.app.submit(self.app.run_send_now(reason="手动")).result(timeout=300)
            except Exception as exc:
                result = {"ok": False, "detail": str(exc)}
            self.send_finished.emit(result)

        threading.Thread(target=worker, name="manual-send", daemon=True).start()

    def _on_send_finished(self, result: dict) -> None:
        self._set_busy(False)
        if result.get("ok"):
            QMessageBox.information(self, "发送成功", str(result.get("detail") or "消息已发出"))
        else:
            QMessageBox.critical(self, "发送失败", str(result.get("detail") or "未知原因"))

    def _open_data_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.button_save, self.button_reload, self.button_send):
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
            self.label_today.setText("已发送" if today.get("sent") else "未发送")
            next_run = str(status.get("next_run_at") or "")
            self.label_next.setText(next_run[5:16].replace("T", " ") if len(next_run) >= 16 else "-")

        self.text_log.setPlainText("\n".join(recent_lines(300)))
        self.text_log.verticalScrollBar().setValue(self.text_log.verticalScrollBar().maximum())

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
