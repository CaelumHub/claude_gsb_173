# -*- coding: utf-8 -*-
"""
HTTP 接口服务 + 静态资源托管。

纯标准库实现（``http.server.ThreadingHTTPServer``），不依赖 Flask 等第三方库。
职责：
  * 托管前端静态页面（frontend/ 下 10 个 HTML 页面 + css/js）；
  * 提供 REST 风格的 JSON 接口，覆盖项目/版本管理、编译、运行、调试、剖析。

线程池模型：每个请求一个线程，读改写 JSON 文件时依赖 storage 层的文件锁保证
并发安全（服务内部是单进程多线程，文件锁 + 进程内线程锁双重保护）。
"""

import json
import os
import re
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from . import config
from . import service as service_mod

_SERVICE = None


def get_service() -> service_mod.Service:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = service_mod.Service()
    return _SERVICE


_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "MiniLangPlatform/1.0"

    # ---- 日志（静默，避免刷屏） ----
    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------------
    # 路由入口
    # ------------------------------------------------------------------
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PATCH(self):
        self._route("PATCH")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PUT(self):
        self._route("PUT")

    def _route(self, method):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/"):
                self._handle_api(method, path, parsed.query)
            else:
                self._serve_static(path)
        except Exception as e:  # 兜底，避免线程崩溃
            self._json(500, {"ok": False, "error": f"服务器内部错误: {e}"})

    # ------------------------------------------------------------------
    # API 路由
    # ------------------------------------------------------------------
    def _handle_api(self, method, path, query):
        svc = get_service()
        body = _read_body(self) if method in ("POST", "PATCH", "PUT") else {}

        # ---- 项目 ----
        if path == "/api/projects" and method == "GET":
            return self._json(200, {"ok": True, "projects": svc.list_projects()})
        if path == "/api/projects" and method == "POST":
            p = svc.create_project(body.get("name", ""), body.get("source", ""),
                                   body.get("language", "minilang"))
            return self._json(200, {"ok": True, "project": p})

        m = re.match(r"^/api/projects/([^/]+)/versions/([^/]+)/diff/([^/]+)$", path)
        if m and method == "GET":
            diff = svc.diff_versions(m.group(1), m.group(2), m.group(3))
            return self._json(200, {"ok": True, "diff": diff})

        m = re.match(r"^/api/projects/([^/]+)/versions/([^/]+)/restore$", path)
        if m and method == "POST":
            ver = svc.restore_version(m.group(1), m.group(2))
            return self._json(200, {"ok": bool(ver), "version": ver})

        m = re.match(r"^/api/projects/([^/]+)/versions/([^/]+)$", path)
        if m and method == "GET":
            ver = svc.get_version(m.group(1), m.group(2))
            return self._json(200, {"ok": bool(ver), "version": ver})

        m = re.match(r"^/api/projects/([^/]+)/versions$", path)
        if m and method == "GET":
            return self._json(200, {"ok": True, "versions": svc.list_versions(m.group(1))[::-1]})
        if m and method == "POST":
            ver = svc.save_version(m.group(1), body.get("source", ""), body.get("message", ""))
            return self._json(200, {"ok": True, "version": ver})

        m = re.match(r"^/api/projects/([^/]+)/run$", path)
        if m and method == "POST":
            rec = svc.record_run(m.group(1), body.get("version_id"), body.get("source", ""),
                                 body.get("options", {}))
            return self._json(200, {"ok": True, "run": rec})

        m = re.match(r"^/api/projects/([^/]+)$", path)
        if m and method == "GET":
            p = svc.get_project(m.group(1))
            return self._json(200, {"ok": bool(p), "project": p})
        if m and method == "PATCH":
            p = svc.update_project(m.group(1), body)
            return self._json(200, {"ok": bool(p), "project": p})
        if m and method == "DELETE":
            svc.delete_project(m.group(1))
            return self._json(200, {"ok": True})

        # ---- 编译 / 运行 ----
        if path == "/api/compile" and method == "POST":
            view = svc.compile_view(body.get("source", ""), body.get("detail", "all"))
            return self._json(200, {"ok": True, "result": view})
        if path == "/api/run" and method == "POST":
            opts = body.get("options", {})
            if isinstance(opts, dict) and isinstance(opts.get("sample_interval_ms"), (int, float)):
                opts["sample_interval_ms"] = int(opts["sample_interval_ms"])
            out = svc.run(body.get("source", ""), opts)
            return self._json(200, {"ok": True, "result": out})

        # ---- 调试 ----
        if path == "/api/debug/start" and method == "POST":
            state = svc.debug_start(body.get("source", ""), body.get("breakpoints", []),
                                    body.get("project_id"), body.get("version_id"))
            return self._json(200, state)
        if path == "/api/debug" and method == "GET":
            return self._json(200, {"ok": True, "sessions": svc.debug_sessions_list()})

        m = re.match(r"^/api/debug/([^/]+)/state$", path)
        if m and method == "GET":
            return self._json(200, svc.debug_state(m.group(1)))
        m = re.match(r"^/api/debug/([^/]+)/command$", path)
        if m and method == "POST":
            state = svc.debug_command(m.group(1), body.get("command", "continue"),
                                      body.get("breakpoints"))
            return self._json(200, state)
        m = re.match(r"^/api/debug/([^/]+)/stop$", path)
        if m and method == "POST":
            return self._json(200, svc.debug_stop(m.group(1)))

        # ---- 设置 ----
        if path == "/api/settings" and method == "GET":
            settings = storage_read_settings()
            return self._json(200, {"ok": True, "settings": settings})
        if path == "/api/settings" and method == "POST":
            settings = storage_write_settings(body)
            return self._json(200, {"ok": True, "settings": settings})

        return self._json(404, {"ok": False, "error": f"未知接口: {method} {path}"})

    # ------------------------------------------------------------------
    # 静态资源
    # ------------------------------------------------------------------
    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        # 防目录穿越
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(config.FRONTEND_DIR, rel))
        if not full.startswith(config.FRONTEND_DIR):
            return self._json(403, {"ok": False, "error": "禁止访问"})
        if not os.path.isfile(full):
            # 找不到页面时回退到 index
            full = os.path.join(config.FRONTEND_DIR, "index.html")
            if not os.path.isfile(full):
                return self._json(404, {"ok": False, "error": "页面不存在"})
        ext = os.path.splitext(full)[1].lower()
        ctype = _MIME.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return self._json(404, {"ok": False, "error": "无法读取文件"})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------
    # JSON 输出
    # ------------------------------------------------------------------
    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,PUT,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,PUT,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ---------------------------------------------------------------------------
# 设置存储
# ---------------------------------------------------------------------------
def storage_read_settings():
    path = os.path.join(config.SETTINGS_DIR, "settings.json")
    default = {"theme": "light", "font_size": 14, "tab_size": 4,
               "auto_save": True, "sample_interval_ms": 1.0}
    data = storage_read_json(path, None)
    if data is None:
        return default
    for k, v in default.items():
        data.setdefault(k, v)
    return data


def storage_write_settings(body):
    from . import storage
    path = os.path.join(config.SETTINGS_DIR, "settings.json")
    current = storage_read_settings()
    for k, v in body.items():
        if k in current:
            current[k] = v
    storage.write_json(path, current)
    return current


def storage_read_json(path, default):
    from . import storage
    return storage.read_json(path, default)


def serve(host=None, port=None):
    host = host or config.DEFAULT_HOST
    port = port or config.DEFAULT_PORT
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[MiniLang] 在线编译器与解释器调试平台已启动: http://{host}:{port}")
    print(f"[MiniLang] 前端目录: {config.FRONTEND_DIR}")
    print(f"[MiniLang] 数据目录: {config.DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
