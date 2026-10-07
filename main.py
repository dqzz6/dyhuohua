"""程序入口：启动内置浏览器、定时服务与图形界面。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def already_running_url(timeout: float = 2.0):
    """如果已经有一个实例在运行，返回它的控制接口地址。"""
    from app.paths import RUNTIME_PATH

    try:
        info = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url = str(info.get("control_api") or "").strip()
    token = str(info.get("token") or "").strip()
    if not url or not token:
        return None
    try:
        request = urllib.request.Request(
            url + "/status",
            headers={"X-Douyin-Token": token},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 200:
                return url
    except Exception:
        return None
    return None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="抖音自动消息：内置浏览器 + 定时发送 + 自动续灯牌")
    parser.add_argument("--port", type=int, default=0, help="本地控制接口端口，0 表示使用配置文件里的值")
    parser.add_argument("--instance", default="", help="多开实例名称，留空表示默认实例")
    parser.add_argument("--selftest", action="store_true", help="只做逻辑自检，不启动内置浏览器")
    parser.add_argument(
        "--selftest-browser",
        action="store_true",
        help="逻辑自检并完整跑一遍内置浏览器流程",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.instance:
        os.environ["DOUYIN_INSTANCE"] = str(args.instance)

    if args.selftest or args.selftest_browser:
        from app.selftest import run_selftest

        return run_selftest(include_browser=args.selftest_browser)

    from app.paths import INSTANCE_NAME, PROFILE_DIR

    running_url = already_running_url()
    if running_url:
        message = (
            f"{INSTANCE_NAME}实例已经在运行了，不用重复打开。\n"
            f"控制接口：{running_url}"
        )
        print(message)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.information(None, "提示", message)
        except Exception:
            pass
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from app.embedded import EmbeddedBrowser, prepare_debug_port
    from app.gui import MainWindow
    from app.service import Application

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    debug_port = prepare_debug_port()
    qt_app = QApplication(sys.argv[:1])
    app_name = (
        "抖音自动消息"
        if INSTANCE_NAME == "默认"
        else f"抖音自动消息-{INSTANCE_NAME}"
    )
    qt_app.setApplicationName(app_name)

    application = Application(debug_port)
    if args.port:
        application.update_config({"control_api_port": args.port})

    browser = EmbeddedBrowser(PROFILE_DIR, profile_name=app_name)

    def create_live_browser() -> EmbeddedBrowser:
        live_browser = EmbeddedBrowser(
            PROFILE_DIR,
            profile_name=app_name,
            profile=browser.profile,
        )
        live_browser.load_url("https://live.douyin.com/")
        return live_browser

    live_browser = create_live_browser() if application.live_browser_enabled else None
    browser.load_url(application.start_url())
    application.start()
    window = MainWindow(
        application,
        browser,
        live_browser=live_browser,
        live_browser_factory=create_live_browser,
    )
    window.show()

    exit_code = qt_app.exec()
    application.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
