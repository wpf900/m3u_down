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


def _alert(title: str, message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        return
    print(f"{title}: {message}", file=sys.stderr)


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            _alert("流影启动失败", f"{exc}\n\n{details[-1500:]}")
        except Exception:
            pass

    sys.excepthook = hook


_install_excepthook()

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
ICON_ICNS = ROOT / "assets" / "icon.icns"
SUPPORT = support_dir()
SETTINGS_PATH = SUPPORT / "settings.json"
TASKS_DB = SUPPORT / "tasks.db"
DEFAULT_OUTPUT = str(Path.home() / "Downloads" / "流影")


def _write_error_log(text: str) -> Path:
    path = support_dir() / "error.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


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
        self.window = None
        self._windows_host = None
        self.settings = load_settings()
        self.manager = DownloadManager(find_ffmpeg(), TASKS_DB)
        self.manager.max_tasks = int(self.settings["task_workers"])
        self._zoomed = False
        self._saved: tuple[int, int, int, int] | None = None
        self._quitting = False

    def get_bootstrap(self) -> dict:
        snap = self.manager.snapshot()
        return {
            "settings": self.settings,
            "tasks": snap.get("active", []),
            "history": snap.get("history", []),
            "ffmpeg": self.manager.ffmpeg or "",
            "host": "edge" if sys.platform == "win32" else "webview",
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
        if sys.platform == "win32":
            from windows_host import pick_folder

            path = pick_folder(self.settings.get("output_dir") or DEFAULT_OUTPUT)
            if not path:
                return None
            self.settings["output_dir"] = path
            save_settings(self.settings)
            return path
        if not self.window:
            return None
        import webview

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
            return {"ok": False, "error": "请先粘贴至少一个视频链接"}
        options = dict(self.settings)
        options["filename"] = filename
        tasks = self.manager.enqueue(items, options)
        snap = self.manager.snapshot()
        return {"ok": True, "tasks": snap.get("active", []), "history": snap.get("history", []), "count": len(tasks)}

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

    def batch_retry(self, task_ids: list) -> dict:
        return self.manager.batch_retry(list(task_ids or []))

    def batch_remove(self, task_ids: list) -> dict:
        return self.manager.batch_remove(list(task_ids or []))

    def batch_cancel(self, task_ids: list) -> dict:
        return self.manager.batch_cancel(list(task_ids or []))

    def batch_pause(self, task_ids: list) -> dict:
        return self.manager.batch_pause(list(task_ids or []))

    def batch_resume(self, task_ids: list) -> dict:
        return self.manager.batch_resume(list(task_ids or []))

    def snapshot(self) -> dict:
        return self.manager.snapshot()

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
        host = self._windows_host
        self._windows_host = None
        if host:
            try:
                host.stop()
            except Exception:
                pass
        if window:
            try:
                window.destroy()
            except Exception:
                pass
        threading.Thread(target=_force_exit, daemon=True, name="liuying-exit").start()


def _force_exit() -> None:
    time.sleep(0.2)
    os._exit(0)


def _ensure_foreground_app() -> None:
    if sys.platform != "darwin" or getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        from ctypes import POINTER, Structure, byref, c_int, c_uint32

        class ProcessSerialNumber(Structure):
            _fields_ = [("highLongOfPSN", c_uint32), ("lowLongOfPSN", c_uint32)]

        lib = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        transform = lib.TransformProcessType
        transform.argtypes = [POINTER(ProcessSerialNumber), c_int]
        transform.restype = c_int
        psn = ProcessSerialNumber(0, 0)
        transform(byref(psn), 1)
    except Exception:
        pass


def _dock_icon_path() -> Path | None:
    for path in (ICON_ICNS, ICON):
        if path.is_file():
            return path.resolve()
    return None


def _apply_dock_icon() -> None:
    if sys.platform != "darwin" or getattr(sys, "frozen", False):
        return
    path = _dock_icon_path()
    if not path:
        return
    try:
        import AppKit

        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
        if not image:
            return
        image.setSize_(AppKit.NSMakeSize(128, 128))
        app = AppKit.NSApplication.sharedApplication()
        app.setApplicationIconImage_(image)
        app.dockTile().display()
    except Exception:
        pass


def _schedule_dock_icon() -> None:
    if sys.platform != "darwin" or getattr(sys, "frozen", False):
        return
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_apply_dock_icon)
    except Exception:
        _apply_dock_icon()


def main() -> None:
    SUPPORT.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).resolve().parent)
    html = ROOT / "web" / "index.html"
    if not html.is_file():
        raise FileNotFoundError(
            "找不到界面文件 web/index.html。\n"
            "请解压整个压缩包，在含有 Liuying.exe 和 _internal 的文件夹里运行，不要只拷贝 exe。"
        )
    api = Api()
    if sys.platform == "win32":
        from windows_host import run_windows

        run_windows(api, ROOT / "web", SUPPORT)
        api.quit()
        return

    _ensure_foreground_app()

    import webview

    webview.settings["ALLOW_FILE_URLS"] = True
    window = webview.create_window(
        "流影",
        url=str(html),
        js_api=api,
        width=900,
        height=720,
        min_size=(760, 580),
        frameless=True,
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

    if sys.platform == "darwin" and not getattr(sys, "frozen", False):
        window.events.shown += _schedule_dock_icon
        window.events.loaded += _schedule_dock_icon

    webview.start(
        private_mode=True,
        http_server=True,
        storage_path=str(support_dir() / "webview"),
    )
    api.quit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except BaseException as exc:
        details = "".join(traceback.format_exception(exc))
        try:
            log_path = _write_error_log(details)
            log_hint = f"\n\n详细日志：\n{log_path}"
        except Exception:
            log_hint = f"\n\n{details}"
        if isinstance(exc, FileNotFoundError) and "web/index.html" in str(exc):
            message = str(exc)
        else:
            message = f"{exc}{log_hint}"
        try:
            _alert("流影启动失败", message)
        except Exception:
            pass
    os._exit(0)
