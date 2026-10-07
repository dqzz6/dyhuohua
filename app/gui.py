"""软件主界面：左侧是内置浏览器，右侧是状态、发送设置、好友搜索与运行日志。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QSize, QThread, QTimer, Signal
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
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .badge_renewal import normalize_live_urls
from .embedded import EmbeddedBrowser
from .friends import normalize_key, read_cache
from .logger import recent_lines
from .message import split_candidates
from .paths import DATA_DIR
from .service import Application

AVATAR_SIZE = QSize(34, 34)
SELECTED_AVATAR_SIZE = QSize(26, 26)

APP_STYLE = """
QMainWindow, QWidget#controlPanel {
    background: #eef3f8;
    color: #1b2a41;
}
QSplitter::handle {
    background: #d6e0eb;
    width: 1px;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #dbe5ef;
    border-radius: 8px;
    margin-top: 10px;
    padding: 14px 10px 10px 10px;
    color: #17253a;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #17253a;
}
QGroupBox#badgeCard {
    background: #fbfefd;
    border: 1px solid #b8ddd8;
}
QLabel {
    color: #53657c;
}
QLabel#badgeCount {
    color: #0f766e;
    font-weight: 600;
}
QLineEdit, QPlainTextEdit, QSpinBox {
    background: #fbfdff;
    border: 1px solid #cbd8e5;
    border-radius: 6px;
    padding: 6px 8px;
    color: #16243a;
    selection-background-color: #159a91;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
    border: 1px solid #159a91;
    background: #ffffff;
}
QPushButton {
    background: #f7fafc;
    border: 1px solid #cbd8e5;
    border-radius: 6px;
    color: #24364e;
    font-weight: 600;
    padding: 7px 11px;
}
QPushButton:hover {
    background: #eaf4fb;
    border-color: #8db8df;
}
QPushButton:pressed {
    background: #dceaf8;
}
QPushButton:disabled {
    background: #eef2f6;
    color: #98a6b7;
    border-color: #dce4ec;
}
QPushButton#primaryButton {
    background: #176fa8;
    border-color: #176fa8;
    color: #ffffff;
}
QPushButton#primaryButton:hover {
    background: #125d90;
}
QPushButton#successButton {
    background: #0f8f82;
    border-color: #0f8f82;
    color: #ffffff;
}
QPushButton#successButton:hover {
    background: #0b756b;
}
QPushButton#accentButton {
    background: #edf8f6;
    border-color: #7fc8c0;
    color: #0f6b63;
}
QPushButton#accentButton:hover {
    background: #d9f1ed;
}
QPushButton#warningButton {
    background: #fff7ed;
    border-color: #fdba74;
    color: #9a3412;
}
QPushButton#warningButton:hover {
    background: #ffedd5;
}
QCheckBox, QRadioButton {
    color: #34465d;
    spacing: 6px;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #dbe5ef;
    border-radius: 6px;
    outline: 0;
}
QListWidget::item {
    border-radius: 4px;
    padding: 6px;
}
QListWidget::item:hover {
    background: #f1f7fb;
}
QListWidget::item:selected {
    background: #d9f1ed;
    color: #0f655f;
}
QPlainTextEdit#logView {
    background: #0f172a;
    border: 1px solid #1f314b;
    color: #d8eee9;
    selection-background-color: #1e5b68;
}
QScrollBar:vertical {
    background: #eef3f8;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #b8c8d9;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #8fa9c1;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QTabWidget::pane {
    background: #eef3f8;
    border: 0;
    top: -1px;
}
QTabBar::tab {
    background: #dde7f0;
    border: 1px solid #cbd8e5;
    border-bottom: 0;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    color: #52657b;
    margin-right: 3px;
    min-width: 112px;
    padding: 8px 16px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #135f93;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background: #eaf2f8;
}
QTabWidget#settingsTabs QTabBar::tab {
    min-width: 0;
    padding: 7px 10px;
}
"""


def filter_friend_names(friends, keyword: str) -> list:
    """按关键词过滤好友名，支持全角/零宽字符归一化。"""
    word = normalize_key(keyword)
    names = []
    for item in friends:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if word and word not in normalize_key(name):
            continue
        names.append(name)
    return names


def order_selected_names(friends, selected) -> list:
    """已选好友按页面顺序排列，不在当前列表里的补在后面。"""
    order = [str(item.get("name") or "") for item in friends]
    chosen = {str(name) for name in selected}
    result = [name for name in order if name and name in chosen]
    result.extend(name for name in chosen if name not in order)
    return result


class MainWindow(QMainWindow):
    """面向普通用户的操作界面，耗时动作都放到后台线程执行。"""

    send_finished = Signal(dict)
    friends_ready = Signal(dict)
    badge_finished = Signal(dict)
    badge_test_finished = Signal(dict)
    chat_browser_load_requested = Signal()
    live_browser_load_requested = Signal()

    def __init__(
        self,
        app: Application,
        browser: Optional[EmbeddedBrowser] = None,
        live_browser: Optional[EmbeddedBrowser] = None,
        browser_factory: Optional[Callable[[], EmbeddedBrowser]] = None,
        live_browser_factory: Optional[Callable[[], EmbeddedBrowser]] = None,
    ):
        super().__init__()
        self.app = app
        self.browser = browser
        self.live_browser = live_browser
        self._browser_factory = browser_factory
        self._live_browser_factory = live_browser_factory
        self._closing = False
        self._busy = False
        self._status = {}
        self._auto_scanned = False
        self._last_logged_in = None
        self._loading = False
        self._friends = []
        self._selected_names = set()
        self._chat_browser_load_event = None
        self._chat_browser_load_error = ""
        self._chat_browser_load_lock = threading.Lock()
        self._live_browser_load_event = None
        self._live_browser_load_error = ""
        self._live_browser_load_lock = threading.Lock()

        self.setWindowTitle("抖音自动消息")
        self.resize(1480, 980)
        self.setMinimumSize(1150, 820)
        self.setStyleSheet(APP_STYLE)

        self._build_widgets()
        self._load_form()
        self._load_cached_friends()
        self._connect_signals()
        self._start_polling()

    # ---------- 界面搭建 ----------
    def _build_widgets(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        browser_tabs = QTabWidget()
        browser_tabs.setObjectName("browserTabs")
        self.browser_stack = QStackedWidget()
        self.browser_placeholder = self._build_browser_placeholder(
            "私信浏览器尚未加载",
            "到发送时间、读取好友或手动打开私信页时会自动加载。",
        )
        self.browser_stack.addWidget(self.browser_placeholder)
        if self.browser is not None:
            self.browser_stack.addWidget(self.browser)
            self.browser_stack.setCurrentWidget(self.browser)
        browser_tabs.addTab(self.browser_stack, "私信浏览器")
        self.browser_tabs = browser_tabs
        if self.live_browser is not None:
            browser_tabs.addTab(self.live_browser, "直播浏览器")
        splitter.addWidget(browser_tabs)
        splitter.addWidget(self._build_panel())
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([820, 620])
        self.setCentralWidget(splitter)

    def _build_browser_placeholder(self, title: str, detail: str) -> QWidget:
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading = QLabel(title)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("font-size: 18px; font-weight: 700; color: #24364e;")
        hint = QLabel(detail)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7c90; margin-top: 8px;")
        button = QPushButton("立即加载私信浏览器")
        button.setObjectName("primaryButton")
        button.setFixedWidth(190)
        button.clicked.connect(self._reload_clicked)
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addSpacing(14)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        return placeholder

    def _ensure_chat_browser_loaded(self) -> EmbeddedBrowser:
        """创建私信浏览器页面，并通知后台服务接管。"""
        if self.browser is not None:
            return self.browser
        if self._browser_factory is None:
            raise RuntimeError("私信浏览器加载器不可用")
        browser = self._browser_factory()
        self.browser = browser
        self.browser_stack.addWidget(browser)
        self.browser_stack.setCurrentWidget(browser)
        self.app.mark_chat_browser_loaded()
        try:
            self.app.submit(self.app.enable_chat_browser())
        except Exception:
            pass
        return browser

    def request_chat_browser_load(self) -> None:
        """供后台定时任务调用，在界面线程完成页面创建后返回。"""
        if QThread.currentThread() == self.thread():
            self._ensure_chat_browser_loaded()
            return
        with self._chat_browser_load_lock:
            if self.browser is not None:
                return
            event = threading.Event()
            self._chat_browser_load_event = event
            self._chat_browser_load_error = ""
            self.chat_browser_load_requested.emit()
            if not event.wait(60):
                raise RuntimeError("等待私信浏览器加载超时")
            if self._chat_browser_load_error:
                raise RuntimeError(self._chat_browser_load_error)

    def _on_chat_browser_load_requested(self) -> None:
        try:
            self._ensure_chat_browser_loaded()
        except Exception as exc:
            self._chat_browser_load_error = str(exc)
        finally:
            event = self._chat_browser_load_event
            if event is not None:
                event.set()

    def _release_chat_browser_loader(self) -> None:
        event = self._chat_browser_load_event
        if event is not None and not event.is_set():
            self._chat_browser_load_error = "程序正在退出"
            event.set()

    def _release_live_browser_loader(self) -> None:
        event = self._live_browser_load_event
        if event is not None and not event.is_set():
            self._live_browser_load_error = "程序正在退出"
            event.set()

    def _show_chat_browser_tab(self) -> EmbeddedBrowser:
        browser = self._ensure_chat_browser_loaded()
        self.browser_tabs.setCurrentIndex(0)
        return browser

    def _ensure_live_browser_loaded(self) -> EmbeddedBrowser:
        """只在启用直播功能时创建第二个浏览器页面。"""
        if self.live_browser is not None:
            return self.live_browser
        if self._live_browser_factory is None:
            raise RuntimeError("直播浏览器加载器不可用")
        live_browser = self._live_browser_factory()
        self.live_browser = live_browser
        self.browser_tabs.addTab(live_browser, "直播浏览器")
        self.app.mark_live_browser_loaded()
        try:
            self.app.submit(self.app.enable_live_browser())
        except Exception:
            pass
        return live_browser

    def request_live_browser_load(self) -> None:
        """供后台续灯牌任务调用，在界面线程创建直播页后返回。"""
        if QThread.currentThread() == self.thread():
            self._ensure_live_browser_loaded()
            return
        with self._live_browser_load_lock:
            if self.live_browser is not None:
                return
            event = threading.Event()
            self._live_browser_load_event = event
            self._live_browser_load_error = ""
            self.live_browser_load_requested.emit()
            if not event.wait(60):
                raise RuntimeError("等待直播浏览器加载超时")
            if self._live_browser_load_error:
                raise RuntimeError(self._live_browser_load_error)

    def _on_live_browser_load_requested(self) -> None:
        try:
            self._ensure_live_browser_loaded()
        except Exception as exc:
            self._live_browser_load_error = str(exc)
        finally:
            event = self._live_browser_load_event
            if event is not None:
                event.set()

    def _unload_live_browser(self) -> None:
        """移除直播页并释放 QtWebEngine 渲染资源。"""
        live_browser = self.live_browser
        if live_browser is None:
            return
        tab_index = self.browser_tabs.indexOf(live_browser)
        if tab_index >= 0:
            self.browser_tabs.removeTab(tab_index)
        self.live_browser = None
        live_browser.deleteLater()
        if self.browser_tabs.count():
            self.browser_tabs.setCurrentIndex(0)

    def _apply_live_browser_setting(self, enabled: bool) -> None:
        if enabled:
            return
        self._unload_live_browser()
        try:
            self.app.submit(self.app.disable_live_browser())
        except Exception:
            pass

    def _apply_chat_browser_setting(self, lazy: bool) -> None:
        if not lazy:
            self._ensure_chat_browser_loaded()

    def _set_button_icon(
        self,
        button: QPushButton,
        pixmap: QStyle.StandardPixmap,
    ) -> None:
        icon = self.style().standardIcon(pixmap)
        if not icon.isNull():
            button.setIcon(icon)
            button.setIconSize(QSize(16, 16))

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("controlPanel")
        panel.setMinimumWidth(470)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        tabs = QTabWidget()
        tabs.setObjectName("settingsTabs")
        tabs.setDocumentMode(True)

        status_tab = QWidget()
        status_layout = QVBoxLayout(status_tab)
        status_layout.setContentsMargins(0, 6, 0, 0)
        status_layout.addWidget(self._build_status_group())
        status_layout.addStretch(1)
        tabs.addTab(status_tab, "运行状态")

        message_tab = QWidget()
        message_layout = QVBoxLayout(message_tab)
        message_layout.setContentsMargins(0, 6, 0, 0)
        message_layout.setSpacing(10)
        message_layout.addWidget(self._build_settings_group())
        message_layout.addStretch(1)
        tabs.addTab(message_tab, "消息设置")

        friends_tab = QWidget()
        friends_layout = QVBoxLayout(friends_tab)
        friends_layout.setContentsMargins(0, 6, 0, 0)
        friends_layout.addWidget(self._build_friends_group(), 1)
        tabs.addTab(friends_tab, "好友选择")

        badge_tab = QWidget()
        badge_layout = QVBoxLayout(badge_tab)
        badge_layout.setContentsMargins(0, 6, 0, 0)
        badge_layout.setSpacing(10)
        badge_layout.addWidget(self._build_badge_group())
        badge_layout.addStretch(1)
        tabs.addTab(badge_tab, "自动续灯牌")

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(0, 6, 0, 0)
        log_layout.addWidget(self._build_log_group(), 1)
        tabs.addTab(log_tab, "运行日志")

        layout.addWidget(tabs, 5)
        layout.addLayout(self._build_buttons())
        return panel

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("运行状态")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        self.label_browser = QLabel("未启动")
        self.label_live_browser = QLabel("未启动")
        self.label_login = QLabel("未知")
        self.label_today = QLabel("未发送")
        self.label_next = QLabel("-")
        self.label_badge = QLabel("未开启")
        bold = QFont()
        bold.setBold(True)
        for label in (
            self.label_browser,
            self.label_live_browser,
            self.label_login,
            self.label_today,
            self.label_next,
            self.label_badge,
        ):
            label.setFont(bold)

        rows = (
            ("内置浏览器", self.label_browser),
            ("直播浏览器", self.label_live_browser),
            ("登录状态", self.label_login),
            ("今日发送", self.label_today),
            ("下次发送", self.label_next),
            ("续灯牌", self.label_badge),
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
        self.check_chat_lazy = QCheckBox("启动时不加载私信浏览器（到发送时再加载）")
        self.input_message = QPlainTextEdit()
        self.input_message.setFixedHeight(78)
        self.radio_random = QRadioButton("随机抽一行")
        self.radio_whole = QRadioButton("整条发送")
        self.label_message_hint = QLabel("")
        self.label_message_hint.setWordWrap(True)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.radio_random)
        mode_row.addWidget(self.radio_whole)
        mode_row.addStretch(1)
        mode_box = QVBoxLayout()
        mode_box.setSpacing(2)
        mode_box.addLayout(mode_row)
        mode_box.addWidget(self.label_message_hint)

        grid.addWidget(QLabel("发送时间"), 0, 0)
        grid.addWidget(self.input_time, 0, 1)
        grid.addWidget(QLabel("HH:MM（北京时间）"), 0, 2)
        grid.addWidget(self.check_daily, 1, 1, 1, 2)
        grid.addWidget(self.check_missed, 2, 1, 1, 2)
        grid.addWidget(self.check_chat_lazy, 3, 1, 1, 2)
        grid.addWidget(QLabel("发送方式"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addLayout(mode_box, 4, 1, 1, 2)
        grid.addWidget(QLabel("消息内容"), 5, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.input_message, 5, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return group

    def _build_badge_group(self) -> QGroupBox:
        group = QGroupBox("自动续粉丝灯牌")
        group.setObjectName("badgeCard")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self.check_live_browser = QCheckBox("允许开播后按需加载直播浏览器")
        self.check_badge_enabled = QCheckBox("开启定时监控直播间")
        self.label_badge_count = QLabel("未配置直播间")
        self.label_badge_count.setObjectName("badgeCount")
        self.label_badge_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.input_badge_urls = QPlainTextEdit()
        self.input_badge_urls.setObjectName("urlEditor")
        self.input_badge_urls.setFixedHeight(66)
        self.input_badge_urls.setPlaceholderText(
            "每行填写一个直播间地址，可添加多个\n"
            "支持直接粘贴抖音分享文案中的 live.douyin.com 链接"
        )
        self.spin_badge_interval = QSpinBox()
        self.spin_badge_interval.setRange(1, 1440)
        self.spin_badge_interval.setSuffix(" 分钟")
        self.spin_badge_interval.setFixedWidth(112)
        self.spin_badge_watch = QSpinBox()
        self.spin_badge_watch.setRange(1, 180)
        self.spin_badge_watch.setSuffix(" 分钟")
        self.spin_badge_watch.setFixedWidth(112)
        self.button_badge_test = QPushButton("接口检测开播状态")
        self.button_badge_test.setObjectName("accentButton")
        self._set_button_icon(
            self.button_badge_test,
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self.button_badge_send_test = QPushButton("测试送一次灯牌")
        self.button_badge_send_test.setObjectName("warningButton")
        self._set_button_icon(
            self.button_badge_send_test,
            QStyle.StandardPixmap.SP_DialogApplyButton,
        )
        self.label_badge_hint = QLabel(
            "默认每 10 分钟检测一次；挂机按实际播放时间累计，发现暂停会自动恢复。"
        )
        self.label_badge_hint.setWordWrap(True)

        grid.addWidget(self.check_live_browser, 0, 0, 1, 4)
        grid.addWidget(self.check_badge_enabled, 1, 0, 1, 3)
        grid.addWidget(self.label_badge_count, 1, 3)
        grid.addWidget(QLabel("自定义监控列表"), 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.input_badge_urls, 2, 1, 1, 3)
        grid.addWidget(QLabel("检测间隔"), 3, 0)
        grid.addWidget(self.spin_badge_interval, 3, 1)
        grid.addWidget(QLabel("开播后挂机"), 3, 2)
        grid.addWidget(self.spin_badge_watch, 3, 3)
        test_row = QHBoxLayout()
        test_row.addWidget(self.button_badge_test)
        test_row.addWidget(self.button_badge_send_test)
        test_row.addStretch(1)
        grid.addLayout(test_row, 4, 1, 1, 3)
        grid.addWidget(self.label_badge_hint, 5, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        return group

    def _build_friends_group(self) -> QGroupBox:
        group = QGroupBox("好友选择")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        search_row = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("搜索好友名称，回车可快捷勾选唯一匹配项")
        self.input_search.setClearButtonEnabled(True)
        self.button_scan = QPushButton("读取好友列表")
        self.button_scan.setFixedWidth(120)
        self._set_button_icon(self.button_scan, QStyle.StandardPixmap.SP_BrowserReload)
        search_row.addWidget(self.input_search, 1)
        search_row.addWidget(self.button_scan)
        layout.addLayout(search_row)

        self.label_selected = QLabel("已选好友（0 位，双击可移除）")
        layout.addWidget(self.label_selected)
        self.list_selected = QListWidget()
        self.list_selected.setIconSize(SELECTED_AVATAR_SIZE)
        self.list_selected.setFixedHeight(84)
        self.list_selected.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        layout.addWidget(self.list_selected)

        action_row = QHBoxLayout()
        self.button_all = QPushButton("全选当前结果")
        self.button_none = QPushButton("清空选择")
        self._set_button_icon(self.button_all, QStyle.StandardPixmap.SP_DialogOkButton)
        self._set_button_icon(
            self.button_none,
            QStyle.StandardPixmap.SP_DialogCancelButton,
        )
        self.label_friends = QLabel("还没有读取好友列表，登录后会自动读取一次。")
        self.label_friends.setWordWrap(True)
        action_row.addWidget(self.button_all)
        action_row.addWidget(self.button_none)
        action_row.addWidget(self.label_friends, 1)
        layout.addLayout(action_row)

        self.list_friends = QListWidget()
        self.list_friends.setIconSize(AVATAR_SIZE)
        self.list_friends.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list_friends.setUniformItemSizes(True)
        self.list_friends.setStyleSheet("QListWidget::item { height: 42px; }")
        layout.addWidget(self.list_friends, 1)
        return group

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.button_save = QPushButton("保存设置")
        self.button_reload = QPushButton("打开私信页")
        self.button_send = QPushButton("立即给选中好友发送")
        self.button_data = QPushButton("打开数据目录")
        self.button_save.setObjectName("primaryButton")
        self.button_send.setObjectName("successButton")
        self._set_button_icon(
            self.button_save,
            QStyle.StandardPixmap.SP_DialogSaveButton,
        )
        self._set_button_icon(
            self.button_reload,
            QStyle.StandardPixmap.SP_BrowserReload,
        )
        self._set_button_icon(self.button_send, QStyle.StandardPixmap.SP_ArrowRight)
        self._set_button_icon(self.button_data, QStyle.StandardPixmap.SP_DirOpenIcon)
        for button in (
            self.button_save,
            self.button_reload,
            self.button_send,
            self.button_data,
        ):
            row.addWidget(button)
        return row

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("运行日志")
        layout = QVBoxLayout(group)
        self.text_log = QPlainTextEdit()
        self.text_log.setObjectName("logView")
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
        self.button_all.clicked.connect(self._check_visible_items)
        self.button_none.clicked.connect(self._clear_selection)
        self.input_search.textChanged.connect(self._refresh_friend_list)
        self.input_search.returnPressed.connect(self._quick_select)
        self.list_friends.itemChanged.connect(self._on_friend_item_changed)
        self.list_selected.itemDoubleClicked.connect(self._remove_selected_item)
        self.radio_random.toggled.connect(self._update_message_hint)
        self.input_message.textChanged.connect(self._update_message_hint)
        self.input_badge_urls.textChanged.connect(self._update_badge_hint)
        self.check_live_browser.toggled.connect(self._on_live_browser_toggled)
        self.check_badge_enabled.toggled.connect(self._on_badge_enabled_toggled)
        self.check_badge_enabled.toggled.connect(self._update_badge_hint)
        self.spin_badge_interval.valueChanged.connect(self._update_badge_hint)
        self.spin_badge_watch.valueChanged.connect(self._update_badge_hint)
        self.button_badge_test.clicked.connect(self._badge_test_clicked)
        self.button_badge_send_test.clicked.connect(self._badge_send_test_clicked)
        self.chat_browser_load_requested.connect(self._on_chat_browser_load_requested)
        self.live_browser_load_requested.connect(self._on_live_browser_load_requested)
        self.send_finished.connect(self._on_send_finished)
        self.friends_ready.connect(self._on_friends_ready)
        self.badge_finished.connect(self._on_badge_finished)
        self.badge_test_finished.connect(self._on_badge_test_finished)

    # ---------- 表单数据 ----------
    def _load_form(self) -> None:
        config = self.app.config_snapshot()
        self.input_time.setText(str(config.get("send_time") or "09:00"))
        self.check_daily.setChecked(bool(config.get("daily_enabled")))
        self.check_missed.setChecked(bool(config.get("missed_run")))
        self.check_chat_lazy.setChecked(bool(config.get("chat_browser_lazy")))
        self.input_message.setPlainText(str(config.get("message") or ""))
        if str(config.get("message_mode")) == "random_line":
            self.radio_random.setChecked(True)
        else:
            self.radio_whole.setChecked(True)
        self.check_live_browser.setChecked(bool(config.get("live_browser_enabled")))
        self.check_badge_enabled.setChecked(bool(config.get("badge_renewal_enabled")))
        self.input_badge_urls.setPlainText(
            "\n".join(str(url) for url in (config.get("badge_live_urls") or []))
        )
        self.spin_badge_interval.setValue(
            int(config.get("badge_check_interval_minutes") or 10)
        )
        self.spin_badge_watch.setValue(int(config.get("badge_watch_minutes") or 20))
        self._update_message_hint()
        self._update_badge_hint()
        self._selected_names = {
            str(name) for name in (config.get("target_names") or [])
        }

    def _on_live_browser_toggled(self, checked: bool) -> None:
        if not checked:
            self.check_badge_enabled.setChecked(False)
        self._update_badge_hint()

    def _on_badge_enabled_toggled(self, checked: bool) -> None:
        if checked:
            self.check_live_browser.setChecked(True)
        self._update_badge_hint()

    def _update_message_hint(self) -> None:
        text = self.input_message.toPlainText()
        if self.radio_random.isChecked():
            count = len(split_candidates(text))
            self.label_message_hint.setText(
                f"当前 {count} 条候选，每次随机抽一条发送（会尽量避开上次发过的那条）"
            )
        else:
            lines = len([line for line in text.splitlines() if line.strip()])
            self.label_message_hint.setText(f"当前 {lines} 行，会作为一条消息整体发出")

    def _update_badge_hint(self) -> None:
        try:
            live_urls = normalize_live_urls(self.input_badge_urls.toPlainText())
        except ValueError as exc:
            self.label_badge_count.setText("地址格式有误")
            self.label_badge_hint.setText(f"请检查直播间地址：{exc}")
            return
        count = len(live_urls)
        self.label_badge_count.setText(
            f"已配置 {count} 个直播间" if count else "未配置直播间"
        )
        if not self.check_live_browser.isChecked():
            self.label_badge_hint.setText(
                "未允许按需加载直播浏览器，保存后不会创建直播页面；"
                "需要续灯牌时再勾选上方选项。"
            )
            return
        if self.check_badge_enabled.isChecked():
            if count:
                self.label_badge_hint.setText(
                    f"已开启：每 {self.spin_badge_interval.value()} 分钟检测一次，"
                    f"通过接口确认开播后才创建直播页面，并挂满 {self.spin_badge_watch.value()} 分钟；"
                    "挂机按实际播放时间累计，暂停会自动恢复。"
                )
            else:
                self.label_badge_hint.setText("已开启，但还没有配置直播间地址，请每行填写一个地址。")
        else:
            self.label_badge_hint.setText(
                "每行一个地址，可添加多个主播；挂机按直播视频的实际播放时间累计。"
            )

    def _load_cached_friends(self) -> None:
        cached = read_cache()
        if cached:
            self._friends = list(cached)
            self._refresh_friend_list()
            self._refresh_selected_list()
            self.label_friends.setText(f"已载入上次读取的好友（{len(cached)} 位），可点「读取好友列表」刷新。")
        else:
            self._refresh_selected_list()

    # ---------- 列表渲染 ----------
    def _ordered_selected(self) -> list:
        return order_selected_names(self._friends, self._selected_names)

    def _icon_for(self, name: str) -> QIcon:
        for item in self._friends:
            if str(item.get("name") or "") == name:
                path = str(item.get("avatarPath") or "")
                if path and Path(path).exists():
                    return QIcon(path)
                break
        return QIcon()

    def _refresh_friend_list(self) -> None:
        keyword = self.input_search.text()
        self._loading = True
        try:
            self.list_friends.clear()
            for name in filter_friend_names(self._friends, keyword):
                node = QListWidgetItem(name)
                node.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                )
                node.setCheckState(
                    Qt.CheckState.Checked
                    if name in self._selected_names
                    else Qt.CheckState.Unchecked
                )
                icon = self._icon_for(name)
                if not icon.isNull():
                    node.setIcon(icon)
                node.setData(Qt.ItemDataRole.UserRole, name)
                self.list_friends.addItem(node)
        finally:
            self._loading = False
        self._update_friend_hint()

    def _refresh_selected_list(self) -> None:
        names = self._ordered_selected()
        self._loading = True
        try:
            self.list_selected.clear()
            for name in names:
                node = QListWidgetItem(name)
                icon = self._icon_for(name)
                if not icon.isNull():
                    node.setIcon(icon)
                node.setData(Qt.ItemDataRole.UserRole, name)
                self.list_selected.addItem(node)
        finally:
            self._loading = False
        self.label_selected.setText(f"已选好友（{len(names)} 位，双击可移除）")

    def _update_friend_hint(self) -> None:
        visible = self.list_friends.count()
        total = len(self._friends)
        if not total:
            return
        keyword = self.input_search.text().strip()
        if keyword:
            self.label_friends.setText(
                f"搜索到 {visible} 位（共 {total} 位），"
                f"已勾选 {len(self._selected_names)} 位。"
            )
        else:
            self.label_friends.setText(
                f"共 {total} 位好友，已勾选 {len(self._selected_names)} 位。"
            )

    # ---------- 选择交互 ----------
    def _on_friend_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        name = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_names.add(name)
        else:
            self._selected_names.discard(name)
        self._refresh_selected_list()
        self._update_friend_hint()

    def _check_visible_items(self) -> None:
        for index in range(self.list_friends.count()):
            self.list_friends.item(index).setCheckState(Qt.CheckState.Checked)
        self._refresh_selected_list()

    def _clear_selection(self) -> None:
        self._selected_names.clear()
        self._loading = True
        try:
            for index in range(self.list_friends.count()):
                self.list_friends.item(index).setCheckState(Qt.CheckState.Unchecked)
        finally:
            self._loading = False
        self._refresh_selected_list()
        self._update_friend_hint()

    def _remove_selected_item(self, item: QListWidgetItem) -> None:
        name = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        self._selected_names.discard(name)
        self._loading = True
        try:
            for index in range(self.list_friends.count()):
                node = self.list_friends.item(index)
                if str(node.data(Qt.ItemDataRole.UserRole) or node.text()) == name:
                    node.setCheckState(Qt.CheckState.Unchecked)
        finally:
            self._loading = False
        self._refresh_selected_list()
        self._update_friend_hint()

    def _quick_select(self) -> None:
        """搜索框回车：只有一个匹配项时直接勾选，多个匹配时提示继续输入。"""
        visible = self.list_friends.count()
        if visible == 1:
            self.list_friends.item(0).setCheckState(Qt.CheckState.Checked)
            self.input_search.clear()
        elif visible == 0:
            self.label_friends.setText("没有匹配的好友，换个关键词试试。")

    def _checked_names(self) -> list:
        return self._ordered_selected()

    # ---------- 交互动作 ----------
    def _save_clicked(self) -> None:
        if self._save():
            QMessageBox.information(self, "提示", "设置已保存")

    def _save(self) -> bool:
        names = self._checked_names()
        try:
            badge_urls = normalize_live_urls(self.input_badge_urls.toPlainText())
            chat_browser_lazy = self.check_chat_lazy.isChecked()
            live_browser_enabled = self.check_live_browser.isChecked()
            if self.check_badge_enabled.isChecked() and not live_browser_enabled:
                self.check_live_browser.setChecked(True)
                live_browser_enabled = True
            if not live_browser_enabled and self.app.badge_status_snapshot().get("running"):
                raise ValueError("自动续灯牌正在运行，请等待完成后再关闭直播浏览器")
            if self.check_badge_enabled.isChecked() and not badge_urls:
                raise ValueError("开启自动续灯牌前，请至少填写一个直播间地址")
            self.app.update_config(
                {
                    "target_names": names,
                    "message": self.input_message.toPlainText().strip(),
                    "message_mode": (
                        "random_line" if self.radio_random.isChecked() else "whole"
                    ),
                    "send_time": self.input_time.text().strip(),
                    "daily_enabled": self.check_daily.isChecked(),
                    "missed_run": self.check_missed.isChecked(),
                    "chat_browser_lazy": chat_browser_lazy,
                    "live_browser_enabled": live_browser_enabled,
                    "badge_renewal_enabled": self.check_badge_enabled.isChecked(),
                    "badge_live_urls": badge_urls,
                    "badge_check_interval_minutes": self.spin_badge_interval.value(),
                    "badge_watch_minutes": self.spin_badge_watch.value(),
                }
            )
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"保存设置时出错：{exc}")
            return False
        self._apply_chat_browser_setting(chat_browser_lazy)
        self._apply_live_browser_setting(live_browser_enabled)
        self.label_selected.setText(f"已选好友（{len(names)} 位，双击可移除）")
        return True

    def _reload_clicked(self) -> None:
        browser = self._show_chat_browser_tab()
        browser.load_url(self.app.start_url())

    def _scan_friends(self, auto: bool = False) -> None:
        if self._busy:
            return
        self._show_chat_browser_tab()
        self._busy = True
        self.button_scan.setEnabled(False)
        if not auto:
            self.label_friends.setText("正在读取好友列表（会先刷新私信页再滚动到底部），请稍候……")

        def worker() -> None:
            try:
                friends = self.app.submit(self.app.scan_friends()).result(timeout=900)
                self.friends_ready.emit(
                    {
                        "ok": True,
                        "friends": friends,
                        "auto": auto,
                        "mode": "full",
                    }
                )
            except Exception as exc:
                self.friends_ready.emit({"ok": False, "detail": str(exc), "auto": auto})

        threading.Thread(target=worker, name="scan-friends", daemon=True).start()

    def _ensure_friends(self) -> None:
        """启动后先做快速校验：缓存名单还能对上就直接用，不再整表重读。"""
        if self._busy:
            return
        self._show_chat_browser_tab()
        self._busy = True
        self.button_scan.setEnabled(False)

        def worker() -> None:
            try:
                result = self.app.submit(
                    self.app.ensure_friends_ready()
                ).result(timeout=900)
                self.friends_ready.emit(
                    {
                        "ok": True,
                        "friends": result.get("friends") or [],
                        "auto": True,
                        "mode": result.get("mode") or "cache",
                    }
                )
            except Exception as exc:
                self.friends_ready.emit({"ok": False, "detail": str(exc), "auto": True})

        threading.Thread(target=worker, name="check-friends", daemon=True).start()

    def _on_friends_ready(self, payload: dict) -> None:
        self._busy = False
        self.button_scan.setEnabled(True)
        if not payload.get("ok"):
            self.label_friends.setText(f"读取好友列表失败：{payload.get('detail')}")
            if not payload.get("auto"):
                QMessageBox.warning(self, "读取失败", str(payload.get("detail")))
            return
        friends = payload.get("friends") or []
        self._friends = list(friends)
        self._refresh_friend_list()
        self._refresh_selected_list()
        mode = str(payload.get("mode") or "")
        if mode == "cache":
            self.label_friends.setText(
                f"已沿用上次的好友名单（{len(friends)} 位），需要完整刷新时点「读取好友列表」。"
            )
        elif mode == "empty":
            self.label_friends.setText("私信页还没就绪，登录后会自动读取好友列表。")
        if not friends:
            self.label_friends.setText("没有读取到好友，请确认内置浏览器已经登录并停留在私信页。")

    def _send_now_clicked(self) -> None:
        if not self._save():
            return
        self._show_chat_browser_tab()
        names = self._checked_names()
        if not names:
            QMessageBox.warning(self, "提示", "请先搜索并勾选要发送的好友")
            return
        preview = "、".join(names[:5]) + ("……" if len(names) > 5 else "")
        confirm = QMessageBox.question(
            self,
            "确认",
            f"确定现在给这 {len(names)} 位好友发送吗？\n{preview}",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)

        def worker() -> None:
            try:
                result = self.app.submit(
                    self.app.run_send_now(targets=names, reason="手动")
                ).result(timeout=1800)
            except Exception as exc:
                result = {"ok": False, "detail": str(exc)}
            self.send_finished.emit(result)

        threading.Thread(target=worker, name="manual-send", daemon=True).start()

    def _badge_test_clicked(self) -> None:
        self.check_live_browser.setChecked(True)
        if not self._save():
            return
        urls = normalize_live_urls(self.input_badge_urls.toPlainText())
        if not urls:
            QMessageBox.warning(self, "提示", "请先填写至少一个直播间地址")
            return
        self._set_badge_actions_busy(True)

        def worker() -> None:
            try:
                result = self.app.submit(
                    self.app.run_badge_renewal(
                        urls=urls,
                        force=True,
                        reason="界面手动检测",
                    )
                ).result(timeout=2400)
            except Exception as exc:
                result = {"ok": False, "detail": str(exc), "results": []}
            self.badge_finished.emit(result)

        threading.Thread(
            target=worker,
            name="manual-badge-renewal",
            daemon=True,
        ).start()

    def _badge_send_test_clicked(self) -> None:
        confirm = QMessageBox.question(
            self,
            "测试送灯牌",
            "测试会实际赠送一次粉丝灯牌，可能消耗钻石，并且不会进入 20 分钟挂机。\n"
            "只测试监控列表中的第一个直播间，确定继续吗？",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.check_live_browser.setChecked(True)
        if not self._save():
            return
        urls = normalize_live_urls(self.input_badge_urls.toPlainText())
        if not urls:
            QMessageBox.warning(self, "提示", "请先填写至少一个直播间地址")
            return
        self._set_badge_actions_busy(True)

        def worker() -> None:
            try:
                result = self.app.submit(
                    self.app.test_badge_gift(urls=urls[:1])
                ).result(timeout=2400)
            except Exception as exc:
                result = {"ok": False, "detail": str(exc), "results": []}
            self.badge_test_finished.emit(result)

        threading.Thread(
            target=worker,
            name="test-badge-gift",
            daemon=True,
        ).start()

    def _on_send_finished(self, result: dict) -> None:
        self._set_busy(False)
        detail = str(result.get("detail") or "未知原因")
        if result.get("ok"):
            QMessageBox.information(self, "发送完成", detail)
        else:
            QMessageBox.critical(self, "发送失败", detail)

    def _on_badge_finished(self, result: dict) -> None:
        self._set_badge_actions_busy(False)
        detail = str(result.get("detail") or "未知原因")
        if result.get("ok"):
            QMessageBox.information(self, "续灯牌检测完成", detail)
        else:
            QMessageBox.critical(self, "续灯牌检测失败", detail)

    def _on_badge_test_finished(self, result: dict) -> None:
        self._set_badge_actions_busy(False)
        detail = str(result.get("detail") or "未知原因")
        if result.get("ok"):
            QMessageBox.information(self, "灯牌测试完成", detail)
        else:
            QMessageBox.critical(self, "灯牌测试失败", detail)

    def _open_data_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (
            self.button_save,
            self.button_reload,
            self.button_send,
            self.button_scan,
        ):
            button.setEnabled(not busy)

    def _set_badge_actions_busy(self, busy: bool) -> None:
        self.button_badge_test.setEnabled(not busy)
        self.button_badge_send_test.setEnabled(not busy)
        self.check_live_browser.setEnabled(not busy)
        self.check_badge_enabled.setEnabled(not busy)

    # ---------- 状态与日志刷新 ----------
    def _start_polling(self) -> None:
        threading.Thread(
            target=self._status_loop,
            name="status-poll",
            daemon=True,
        ).start()
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
            if not browser.get("loaded"):
                self.label_browser.setText("按需加载")
            elif browser.get("started"):
                self.label_browser.setText("运行中")
            else:
                self.label_browser.setText("加载中")
            live_browser = status.get("live_browser") or {}
            if not live_browser.get("enabled"):
                self.label_live_browser.setText("未启用")
            elif live_browser.get("loaded") and live_browser.get("started"):
                self.label_live_browser.setText("运行中")
            elif live_browser.get("loaded"):
                self.label_live_browser.setText("加载中")
            else:
                self.label_live_browser.setText("等待开播")
            logged_in = status.get("logged_in")
            self.label_login.setText(
                "已登录"
                if logged_in is True
                else ("未登录" if logged_in is False else "未知")
            )
            today = status.get("today") or {}
            total = int(today.get("total") or 0)
            sent_count = int(today.get("sentCount") or 0)
            self.label_today.setText(f"{sent_count}/{total} 已发送" if total else "未设置好友")
            next_run = str(status.get("next_run_at") or "")
            self.label_next.setText(
                next_run[5:16].replace("T", " ")
                if len(next_run) >= 16
                else "-"
            )
            self._refresh_badge_status(status.get("badge") or {})
            self._maybe_auto_scan(logged_in)

        self.text_log.setPlainText("\n".join(recent_lines(300)))
        self.text_log.verticalScrollBar().setValue(
            self.text_log.verticalScrollBar().maximum()
        )

    def _refresh_badge_status(self, badge: dict) -> None:
        if not badge.get("enabled"):
            self.label_badge.setText("未开启")
            return
        if badge.get("running"):
            self.label_badge.setText("检测中")
            return
        states = badge.get("states") or {}
        latest_status = ""
        for entry in states.values():
            if isinstance(entry, dict) and entry.get("status"):
                latest_status = str(entry.get("status"))
                break
        if latest_status == "sent":
            self.label_badge.setText("已续灯牌")
        elif latest_status == "not_live":
            self.label_badge.setText("等待开播")
        elif latest_status == "failed":
            self.label_badge.setText("检测失败")
        else:
            next_check = str(badge.get("next_check_at") or "")
            if len(next_check) >= 16:
                self.label_badge.setText(f"等待检测 {next_check[11:16]}")
            else:
                self.label_badge.setText("等待检测")

    def _maybe_auto_scan(self, logged_in) -> None:
        """登录成功后自动读取一次好友列表。"""
        if (
            logged_in is True
            and self._last_logged_in is not True
            and not self._auto_scanned
            and not self._busy
        ):
            self._auto_scanned = True
            self.label_friends.setText("检测到已登录，正在校验好友名单……")
            self._ensure_friends()
        self._last_logged_in = logged_in

    # ---------- 生命周期 ----------
    def closeEvent(self, event) -> None:  # noqa: N802
        confirm = QMessageBox.question(
            self,
            "退出确认",
            "退出后定时发送和自动续灯牌都会停止，确定要退出吗？",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._closing = True
        self._release_chat_browser_loader()
        self._release_live_browser_loader()
        try:
            self.app.stop()
        except Exception:
            pass
        event.accept()
