"""Windows UI host: local HTTP + Microsoft Edge --app.

Avoids pywebview/pythonnet, which silently fail in windowed PyInstaller builds.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

EDGE_URL = "https://www.microsoft.com/edge"


def find_edge() -> Path | None:
    names = (
        Path(os_program_files_x86()) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os_program_files()) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(_local_appdata()) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os_program_files_x86()) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os_program_files()) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(_local_appdata()) / "Google" / "Chrome" / "Application" / "chrome.exe",
    )
    for path in names:
        if path.is_file():
            return path
    return None


def os_program_files() -> str:
    import os

    return os.environ.get("PROGRAMFILES", r"C:\Program Files")


def os_program_files_x86() -> str:
    import os

    return os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")


def _local_appdata() -> str:
    import os

    return os.environ.get("LOCALAPPDATA", "")


def pick_folder(initial: str) -> str | None:
    encoded = base64.b64encode((initial or "").encode("utf-16le")).decode("ascii")
    command = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "[void][System.Windows.Forms.Application]::EnableVisualStyles(); "
        "$owner = New-Object System.Windows.Forms.Form; "
        "$owner.TopMost = $true; "
        "$owner.ShowInTaskbar = $false; "
        "$owner.WindowState = 'Minimized'; "
        "[void]$owner.Show(); "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.ShowNewFolderButton = $true; "
        "$d.Description = '选择保存目录'; "
        f"$d.SelectedPath = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded}')); "
        "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { "
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
        "[Console]::Out.Write($d.SelectedPath) }; "
        "$owner.Dispose()"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-WindowStyle",
                "Hidden",
                "-Command",
                command,
            ],
            capture_output=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = completed.stdout.decode("utf-8", errors="ignore").strip()
    return path or None


class _Handler(BaseHTTPRequestHandler):
    api: Any
    web_root: Path
    host: WindowsHost

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self.host.touch()
        rel = unquote(urlparse(self.path).path)
        if rel in ("", "/"):
            rel = "/index.html"
        target = (self.web_root / rel.lstrip("/")).resolve()
        root = self.web_root.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        self.host.touch()
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 2 or parts[0] != "rpc":
            self.send_error(404)
            return
        name = parts[1]
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4_000_000:
            self._json(400, {"error": "请求过大"})
            return
        raw = self.rfile.read(length) if length else b"[]"
        try:
            args = json.loads(raw.decode("utf-8") or "[]")
            if not isinstance(args, list):
                raise ValueError("args must be a list")
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        method = getattr(self.api, name, None)
        if not callable(method):
            self._json(404, {"error": f"未知接口 {name}"})
            return
        try:
            result = method(*args)
        except Exception as exc:
            self._json(500, {"error": str(exc)})
            return
        self._json(200, {"result": result})

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class WindowsHost:
    def __init__(self) -> None:
        self.browser: subprocess.Popen | None = None
        self.httpd: ThreadingHTTPServer | None = None
        self.last_http = 0.0
        self._stopping = False

    def touch(self) -> None:
        self.last_http = time.time()

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        proc = self.browser
        self.browser = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        httpd = self.httpd
        if httpd:
            threading.Thread(target=httpd.shutdown, daemon=True).start()


def run_windows(api: Any, web_root: Path, support: Path) -> None:
    edge = find_edge()
    if not edge:
        raise RuntimeError(
            "未找到 Microsoft Edge（或 Chrome），窗口无法显示。\n"
            f"请安装 Edge 后重试：{EDGE_URL}"
        )
    host = WindowsHost()
    api._windows_host = host

    handler = _Handler
    handler.api = api
    handler.web_root = web_root
    handler.host = host

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host.httpd = httpd
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True, name="liuying-http").start()
    _wait_for_server(port)

    profile = support / "edge-profile"
    profile.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{port}/index.html"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 1
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    host.browser = subprocess.Popen(
        [
            str(edge),
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--window-size=900,720",
            "--disable-features=Translate,InfiniteSessionRestore",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        startupinfo=startup,
        creationflags=flags,
    )
    launched = time.time()
    deadline = launched + 20
    while time.time() < deadline and not host._stopping:
        if host.last_http > launched:
            break
        time.sleep(0.05)
    else:
        if not host._stopping and host.last_http <= launched:
            host.stop()
            raise RuntimeError(
                "没有打开流影窗口。请确认已安装 Microsoft Edge，并允许它运行。"
            )
    while not host._stopping:
        if time.time() - host.last_http > 4:
            break
        time.sleep(0.2)
    host.stop()


def _wait_for_server(port: int, timeout: float = 5.0) -> None:
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/index.html"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.05)
    raise RuntimeError("本地界面服务启动失败。")
