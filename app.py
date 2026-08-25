from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

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


def main() -> None:
    api = Api()
    webview.settings["ALLOW_FILE_URLS"] = True
    html = ROOT / "web" / "index.html"
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

    icon = str(ICON) if ICON.exists() else None
    webview.start(icon=icon, private_mode=True, http_server=True)
    api.quit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        os._exit(0)
    os._exit(0)
