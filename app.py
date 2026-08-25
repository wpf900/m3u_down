from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")

import webview

from downloader import DEFAULT_UA, DownloadManager, extract_items, find_ffmpeg


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def support_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Liuying"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Liuying"
    return Path.home() / ".config" / "Liuying"


ROOT = resource_dir()
ICON = ROOT / "assets" / "icon.png"
SUPPORT = support_dir()
SETTINGS_PATH = SUPPORT / "settings.json"
DEFAULT_OUTPUT = str(Path.home() / "Downloads" / "流影")
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def _alert(title: str, message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        return
    print(f"{title}: {message}", file=sys.stderr)


def _write_error_log(text: str) -> Path:
    path = support_dir() / "error.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _has_webview2() -> bool:
    if sys.platform != "win32":
        return True
    import winreg

    keys = (
        r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    )
    hives = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    for hive in hives:
        for key in keys:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    version, _ = winreg.QueryValueEx(handle, "pv")
                if version and version != "0.0.0.0":
                    return True
            except OSError:
                continue
    roots = (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "EdgeWebView",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "EdgeWebView",
    )
    return any((root / "Application").is_dir() for root in roots)


def load_settings() -> dict:
    defaults = {
        "output_dir": DEFAULT_OUTPUT,
        "task_workers": 3,
        "segment_workers": 16,
        "user_agent": DEFAULT_UA,
        "referer": "",
    }
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        defaults.update({key: data[key] for key in defaults if key in data})
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        pass
    return defaults


def save_settings(data: dict) -> None:
    SUPPORT.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Api:
    def __init__(self) -> None:
        self.window: webview.Window | None = None
        self.settings = load_settings()
        self.manager = DownloadManager(find_ffmpeg())
        self.manager.max_tasks = int(self.settings["task_workers"])
        self._zoomed = False
        self._saved: tuple[int, int, int, int] | None = None
        self._quitting = False

    def get_bootstrap(self) -> dict:
        return {
            "settings": self.settings,
            "tasks": self.manager.snapshot(),
            "ffmpeg": self.manager.ffmpeg or "",
        }

    def save_prefs(self, prefs: dict) -> dict:
        for key in ("output_dir", "user_agent", "referer"):
            if key in prefs and prefs[key] is not None:
                self.settings[key] = str(prefs[key]).strip()
        if prefs.get("task_workers") is not None:
            self.settings["task_workers"] = max(1, min(8, int(prefs["task_workers"])))
            self.manager.max_tasks = self.settings["task_workers"]
        if prefs.get("segment_workers") is not None:
            self.settings["segment_workers"] = max(1, min(64, int(prefs["segment_workers"])))
        if not self.settings["output_dir"]:
            self.settings["output_dir"] = DEFAULT_OUTPUT
        if not self.settings["user_agent"]:
            self.settings["user_agent"] = DEFAULT_UA
        save_settings(self.settings)
        return self.settings

    def choose_folder(self) -> str | None:
        if not self.window:
            return None
        result = self.window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=self.settings.get("output_dir") or DEFAULT_OUTPUT,
        )
        if not result:
            return None
        path = str(result[0])
        self.settings["output_dir"] = path
        save_settings(self.settings)
        return path

    def start_downloads(self, text: str, prefs: dict) -> dict:
        prefs = prefs or {}
        filename = str(prefs.get("filename") or "").strip()
        self.save_prefs(prefs)
        items = extract_items(text or "")
        if not items:
            return {"ok": False, "error": "请先粘贴至少一个 m3u8 链接"}
        options = dict(self.settings)
        options["filename"] = filename
        tasks = self.manager.enqueue(items, options)
        return {"ok": True, "tasks": tasks, "count": len(tasks)}

    def pause_task(self, task_id: str) -> None:
        self.manager.pause(task_id)

    def resume_task(self, task_id: str) -> None:
        self.manager.resume(task_id)

    def cancel_task(self, task_id: str) -> None:
        self.manager.cancel(task_id)

    def retry_task(self, task_id: str) -> dict:
        return self.manager.retry(task_id)

    def remove_task(self, task_id: str) -> dict:
        return self.manager.remove(task_id)

    def snapshot(self) -> dict:
        return {"tasks": self.manager.snapshot()}

    def reveal(self, path: str) -> None:
        target = Path(path)
        if sys.platform == "darwin":
            if target.exists():
                subprocess.Popen(["open", "-R", str(target)])
            elif target.parent.exists():
                subprocess.Popen(["open", str(target.parent)])
            return
        if sys.platform == "win32":
            if target.exists():
                subprocess.Popen(["explorer", "/select,", str(target)])
            elif target.parent.exists():
                subprocess.Popen(["explorer", str(target.parent)])
            return
        folder = target.parent if target.exists() else target
        if folder.exists():
            subprocess.Popen(["xdg-open", str(folder if folder.is_dir() else folder.parent)])

    def win_min(self) -> None:
        if self.window:
            self.window.minimize()

    def win_zoom(self) -> None:
        if not self.window:
            return
        if self._zoomed and self._saved:
            width, height, x, y = self._saved
            self.window.resize(width, height)
            self.window.move(x, y)
            self._zoomed = False
            return
        self._saved = (self.window.width, self.window.height, self.window.x, self.window.y)
        self.window.maximize()
        self._zoomed = True

    def win_close(self) -> None:
        self.quit()

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        try:
            self.manager.shutdown()
        except Exception:
            pass
        window = self.window
        self.window = None
        if window:
            try:
                window.destroy()
            except Exception:
                pass
        threading.Thread(target=_force_exit, daemon=True, name="liuying-exit").start()


def _force_exit() -> None:
    time.sleep(0.2)
    os._exit(0)


def _strip_zone_identifier(path: Path) -> None:
    ads = f"{path}:Zone.Identifier"
    try:
        os.remove(ads)
    except OSError:
        pass
    try:
        import ctypes

        ctypes.windll.kernel32.DeleteFileW(ads)
    except Exception:
        pass


def _unblock_frozen_binaries() -> None:
    """Clear Mark-of-the-Web so .NET can load pythonnet DLLs from a downloaded zip."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(sys.executable).resolve().parent)
    seen: set[Path] = set()
    suffixes = {".dll", ".exe", ".pyd"}
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        _strip_zone_identifier(root)
        for dirpath, _, filenames in os.walk(root):
            _strip_zone_identifier(Path(dirpath))
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() in suffixes:
                    _strip_zone_identifier(path)


def main() -> None:
    _unblock_frozen_binaries()
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).resolve().parent)
    html = ROOT / "web" / "index.html"
    if not html.is_file():
        raise FileNotFoundError(
            "找不到界面文件 web/index.html。\n"
            "请解压整个压缩包，在含有 Liuying.exe 和 _internal 的文件夹里运行，不要只拷贝 exe。"
        )
    if not _has_webview2():
        raise RuntimeError(
            "未检测到 Microsoft Edge WebView2，窗口无法显示。\n"
            f"请安装：{WEBVIEW2_URL}\n"
            "装好后重新双击 Liuying.exe。"
        )
    api = Api()
    webview.settings["ALLOW_FILE_URLS"] = True
    window = webview.create_window(
        "流影",
        url=str(html),
        js_api=api,
        width=900,
        height=720,
        min_size=(760, 580),
        frameless=sys.platform != "win32",
        easy_drag=False,
        shadow=True,
        background_color="#F3F5EE",
        text_select=True,
    )
    api.window = window
    window.events.closed += api.quit

    def handle_signal(_signum, _frame) -> None:
        api.quit()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    icon = str(ICON) if ICON.exists() else None
    start_kwargs = {
        "icon": icon,
        "private_mode": True,
        "http_server": True,
        "storage_path": str(support_dir() / "webview"),
    }
    if sys.platform == "win32":
        start_kwargs["gui"] = "edgechromium"
    webview.start(**start_kwargs)
    api.quit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        details = "".join(traceback.format_exception(exc))
        log_path = _write_error_log(details)
        if "Python.Runtime" in details:
            message = (
                "Windows 把从网上下载的程序当成了未信任文件，窗口组件无法加载。\n\n"
                "请先关掉本窗口，然后：\n"
                "1. 回到下载的 zip，右键 → 属性 → 勾选「解除锁定」→ 确定\n"
                "2. 删除已解压的文件夹，重新解压\n"
                "3. 再双击 Liuying.exe\n\n"
                f"详细日志：\n{log_path}"
            )
        else:
            message = f"{exc}\n\n详细日志：\n{log_path}"
        _alert("流影启动失败", message)
    os._exit(0)
