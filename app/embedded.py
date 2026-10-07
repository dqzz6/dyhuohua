"""内置浏览器控件：在软件自己的窗口里嵌入一个 Chromium 页面。"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

DEBUG_ENV = "QTWEBENGINE_REMOTE_DEBUGGING"

# 使用常见桌面 Chrome 标识，避免因为标识过于特殊触发风控。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def prepare_debug_port() -> int:
    """在创建 QApplication 之前分配并写入调试端口。"""
    port = find_free_port()
    os.environ[DEBUG_ENV] = str(port)
    return port


class EmbeddedBrowser(QWebEngineView):
    """软件窗口内的浏览器视图，登录状态保存在 data/browser-profile。"""

    def __init__(
        self,
        profile_dir: Path,
        parent=None,
        *,
        profile_name: str = "抖音自动消息",
        profile: QWebEngineProfile = None,
    ):
        super().__init__(parent)
        self._profile = profile or self._create_profile(Path(profile_dir), profile_name)
        self._page = QWebEnginePage(self._profile, self)
        self.setPage(self._page)
        self._enable_features()

    @property
    def profile(self) -> QWebEngineProfile:
        return self._profile

    def _create_profile(self, profile_dir: Path, profile_name: str) -> QWebEngineProfile:
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile(str(profile_name), self)
        profile.setPersistentStoragePath(str(profile_dir))
        profile.setCachePath(str(profile_dir / "缓存"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        profile.setHttpUserAgent(USER_AGENT)
        return profile

    def _enable_features(self) -> None:
        settings = self._page.settings()
        flags = (
            "JavascriptEnabled",
            "LocalStorageEnabled",
            "JavascriptCanOpenWindows",
            "FullScreenSupportEnabled",
            "PluginsEnabled",
            "ScrollAnimatorEnabled",
        )
        for name in flags:
            attribute = getattr(QWebEngineSettings.WebAttribute, name, None)
            if attribute is None:
                continue
            try:
                settings.setAttribute(attribute, True)
            except Exception:
                continue

    def load_url(self, url: str) -> None:
        from PySide6.QtCore import QUrl

        self.setUrl(QUrl(str(url)))

    def current_url(self) -> str:
        return self.url().toString()
