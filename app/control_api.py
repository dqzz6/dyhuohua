"""本地控制接口：仅监听 127.0.0.1，需要令牌，用于联调、排查与自动化联测。"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .logger import recent_lines

API_HOST = "127.0.0.1"
MAX_PORT_ATTEMPTS = 20


class _Handler(BaseHTTPRequestHandler):
    server_version = "DouyinAutoMessage/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        """静默处理访问日志，避免刷屏。"""
        return

    # ---------- 基础工具 ----------
    def _context(self) -> Dict[str, Any]:
        return self.server.api_context  # type: ignore[attr-defined]

    def _token_ok(self, params) -> bool:
        supplied = self.headers.get("X-Douyin-Token") or (params.get("token", [""])[0])
        return secrets.compare_digest(str(supplied), str(self._context()["token"]))

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        return data if isinstance(data, dict) else {"value": data}

    def _run(self, coroutine, timeout: float = 180.0) -> Any:
        app = self._context()["app"]
        return app.submit(coroutine).result(timeout=timeout)

    # ---------- 路由 ----------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not self._token_ok(params):
            self._send_json({"ok": False, "error": "令牌无效"}, 401)
            return
        self._dispatch("GET", parsed.path, params, {})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not self._token_ok(params):
            self._send_json({"ok": False, "error": "令牌无效"}, 401)
            return
        self._dispatch("POST", parsed.path, params, self._read_body())

    def _dispatch(self, method: str, path: str, params: Dict[str, list], body: Dict[str, Any]) -> None:
        app = self._context()["app"]
        try:
            if method == "GET" and path == "/status":
                self._send_json({"ok": True, "data": self._run(app.status(), 60)})
            elif method == "GET" and path == "/logs":
                count = int((params.get("n", ["200"])[0]) or 200)
                self._send_json({"ok": True, "data": recent_lines(count)})
            elif method == "GET" and path == "/config":
                self._send_json({"ok": True, "data": app.config_snapshot()})
            elif method == "POST" and path == "/config":
                self._send_json({"ok": True, "data": app.update_config(body)})
            elif method == "POST" and path == "/goto":
                url = str(body.get("url") or "").strip()
                if not url:
                    raise ValueError("缺少参数 url")
                self._run(app.browser.goto(url), 90)
                self._send_json({"ok": True, "data": {"url": url}})
            elif method == "POST" and path == "/evaluate":
                script = str(body.get("script") or "").strip()
                if not script:
                    raise ValueError("缺少参数 script")
                self._send_json({"ok": True, "data": self._run(app.evaluate_script(script), 90)})
            elif method == "POST" and path == "/query":
                selector = str(body.get("selector") or "").strip()
                if not selector:
                    raise ValueError("缺少参数 selector")
                limit = int(body.get("limit") or 20)
                self._send_json({"ok": True, "data": self._run(app.query_selector(selector, limit), 90)})
            elif method == "POST" and path == "/click":
                selector = str(body.get("selector") or "").strip()
                if not selector:
                    raise ValueError("缺少参数 selector")
                self._send_json({"ok": True, "data": self._run(app.click(selector), 90)})
            elif method == "POST" and path == "/type":
                selector = str(body.get("selector") or "").strip()
                if not selector:
                    raise ValueError("缺少参数 selector")
                data = self._run(
                    app.type_text(selector, str(body.get("text") or ""), bool(body.get("submit"))),
                    180,
                )
                self._send_json({"ok": True, "data": data})
            elif method == "POST" and path == "/press":
                key = str(body.get("key") or "").strip()
                if not key:
                    raise ValueError("缺少参数 key")
                self._send_json({"ok": True, "data": self._run(app.press(key), 60)})
            elif method == "POST" and path == "/screenshot":
                data = self._run(app.take_screenshot(bool(body.get("full_page"))), 120)
                self._send_json({"ok": True, "data": data})
            elif method == "POST" and path == "/html":
                limit = int(body.get("limit") or 0)
                self._send_json({"ok": True, "data": self._run(app.page_html(limit), 120)})
            elif method == "GET" and path == "/pages":
                self._send_json({"ok": True, "data": self._run(app.pages_info(), 60)})
            elif method == "POST" and path == "/send":
                raw_targets = body.get("targets")
                targets = raw_targets if isinstance(raw_targets, list) else None
                data = self._run(
                    app.run_send_now(
                        targets=targets,
                        message=str(body.get("message") or ""),
                        reason="本地接口",
                    ),
                    300,
                )
                self._send_json({"ok": True, "data": data})
            elif method == "POST" and path == "/friends":
                self._send_json({"ok": True, "data": self._run(app.scan_friends(), 600)})
            elif method == "GET" and path == "/capture":
                keyword = str((params.get("keyword", [""])[0]) or "")
                limit = int((params.get("limit", ["50"])[0]) or 50)
                self._send_json({"ok": True, "data": app.capture_summary(keyword, limit)})
            elif method == "POST" and path == "/capture/body":
                body_text = app.capture_body(int(body.get("index") or 0), int(body.get("limit") or 4000))
                self._send_json({"ok": True, "data": body_text})
            elif method == "POST" and path == "/capture/clear":
                self._send_json({"ok": True, "data": app.capture_clear()})
            elif method == "POST" and path == "/capture/reload":
                self._send_json({"ok": True, "data": self._run(app.capture_reload(), 120)})
            elif method == "GET" and path == "/history":
                self._send_json({"ok": True, "data": app.store.history(50)})
            else:
                self._send_json({"ok": False, "error": f"未知接口：{method} {path}"}, 404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc), "type": type(exc).__name__}, 500)


class ControlServer:
    """本地 HTTP 控制服务，端口被占用时自动向后顺延。"""

    def __init__(self, app, token: str, logger, host: str = API_HOST, port: int = 8791):
        self._app = app
        self._token = token
        self._logger = logger
        self._host = host
        self._port = int(port)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0

    def start(self) -> None:
        last_error: Optional[Exception] = None
        for offset in range(MAX_PORT_ATTEMPTS):
            port = self._port + offset
            try:
                server = ThreadingHTTPServer((self._host, port), _Handler)
            except OSError as exc:
                last_error = exc
                continue
            server.daemon_threads = True
            server.api_context = {"app": self._app, "token": self._token}  # type: ignore[attr-defined]
            self._server = server
            self.port = port
            break
        if self._server is None:
            raise RuntimeError(f"本地控制接口启动失败：{last_error}")

        self._thread = threading.Thread(target=self._server.serve_forever, name="control-api", daemon=True)
        self._thread.start()
        self._logger.info(f"本地控制接口已就绪：http://{self._host}:{self.port}")

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self.port}" if self.port else ""
