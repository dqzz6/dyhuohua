"""程序入口：启动内置浏览器、定时服务与图形界面。"""

from __future__ import annotations

import argparse
import sys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="抖音自动消息：内置浏览器 + 每日定时发送")
    parser.add_argument("--port", type=int, default=0, help="本地控制接口端口，0 表示使用配置文件里的值")
    parser.add_argument("--selftest", action="store_true", help="只做逻辑自检，不启动内置浏览器")
    parser.add_argument("--selftest-browser", action="store_true", help="逻辑自检并完整跑一遍内置浏览器流程")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.selftest or args.selftest_browser:
        from app.selftest import run_selftest

        return run_selftest(include_browser=args.selftest_browser)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from app.embedded import EmbeddedBrowser, prepare_debug_port
    from app.gui import MainWindow
    from app.paths import PROFILE_DIR
    from app.service import Application

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    debug_port = prepare_debug_port()
    qt_app = QApplication(sys.argv[:1])
    qt_app.setApplicationName("抖音自动消息")

    application = Application(debug_port)
    if args.port:
        application.update_config({"control_api_port": args.port})
    application.start()

    browser = EmbeddedBrowser(PROFILE_DIR)
    window = MainWindow(application, browser)
    window.show()
    browser.load_url(application.start_url())

    exit_code = qt_app.exec()
    application.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
