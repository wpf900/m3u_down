from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
)
ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\n\r\t]+')


class HLSError(Exception):
    pass


class Cancelled(Exception):
    pass


def _ffmpeg_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir))
        dirs.extend([exe_dir, exe_dir / "_internal", meipass])
        if exe_dir.name == "MacOS":
            dirs.extend([exe_dir.parent / "Frameworks", exe_dir.parent / "Resources"])
    dirs.append(Path(__file__).resolve().parent / "vendor")
    return dirs


def find_ffmpeg() -> str | None:
    names = ("ffmpeg.exe", "ffmpeg")
    for folder in _ffmpeg_search_dirs():
        for name in names:
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).is_file():
            return candidate
    return None


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
    else:
        kwargs["start_new_session"] = True
    return kwargs


def extract_items(text: str) -> list[tuple[str, str]]:
    """Parse paste text into (episode_or_file_name, url). Name may be empty."""
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip().strip("'\"").rstrip("，,")
        if not line:
            continue
        name = ""
        url = ""
        if "$" in line:
            left, right = line.split("$", 1)
            match = URL_RE.search(right) or URL_RE.search(line)
            if match:
                url = match.group(0).rstrip(").,;]")
                name = left.strip().strip("'\"")
        else:
            match = URL_RE.search(line)
            if match:
                url = match.group(0).rstrip(").,;]")
                prefix = line[: match.start()].strip().rstrip("#,，:：-|")
                if prefix:
                    name = prefix
            elif line.startswith(("http://", "https://")):
                url = line.rstrip(").,;]")
        if not url or url in seen:
            continue
        seen.add(url)
        title = safe_filename(name) if name else ""
        if title == "未命名视频":
            title = ""
        items.append((title, url))
    return items


def extract_urls(text: str) -> list[str]:
    return [url for _, url in extract_items(text)]


def safe_filename(name: str) -> str:
    name = SAFE_NAME_RE.sub(" ", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    return (name or "未命名视频")[:80]


def title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    part = Path(path).name or "video"
    if part.lower().endswith(".m3u8"):
        part = Path(path).stem
        if part.lower() in {"index", "playlist", "master", "video", "chunklist"}:
            parent = Path(path).parent.name
            part = parent or part
    return safe_filename(part)


def parse_attrs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in ATTR_RE.finditer(raw):
        value = match.group(2)
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        out[match.group(1)] = value
    return out


def parse_iv(raw: str, sequence: int) -> bytes:
    if not raw:
        return struct.pack(">QQ", 0, sequence)
    text = raw.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    data = bytes.fromhex(text)
    return data.rjust(16, b"\x00")[-16:]


def decode_key(raw: bytes) -> bytes:
    if len(raw) == 16:
        return raw
    stripped = raw.strip()
    if len(stripped) == 32 and all(chr(b) in "0123456789abcdefABCDEF" for b in stripped):
        return bytes.fromhex(stripped.decode("ascii"))
    if len(raw) > 16:
        return raw[:16]
    raise HLSError("加密密钥长度无效")


def aes128_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if len(data) < 16 or len(data) % 16:
        return data
    plain = AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    try:
        return unpad(plain, 16)
    except ValueError:
        return plain


def unique_path(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / f"{name}.mp4"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = folder / f"{name}-{index}.mp4"
        if not candidate.exists():
            return candidate
        index += 1


def format_speed(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / 1024 / 1024:.1f} MB/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return ""
    total = int(seconds)
    if total < 60:
        return f"剩余 {total} 秒"
    if total < 3600:
        return f"剩余 {total // 60} 分 {total % 60:02d} 秒"
    return f"剩余 {total // 3600} 小时 {(total % 3600) // 60:02d} 分"


@dataclass
class Segment:
    url: str
    sequence: int
    duration: float = 0.0
    key: bytes | None = None
    iv: bytes | None = None
    byte_range: tuple[int, int] | None = None
    is_init: bool = False


def pack_mosaic(states: bytearray, limit: int = 400) -> str:
    n = len(states)
    if n == 0:
        return ""
    if n <= limit:
        return "".join(str(int(flag)) for flag in states)
    cells: list[str] = []
    for index in range(limit):
        start = index * n // limit
        end = max(start + 1, (index + 1) * n // limit)
        chunk = states[start:end]
        if not chunk:
            cells.append("0")
        elif all(flag == 2 for flag in chunk):
            cells.append("2")
        elif any(flag for flag in chunk):
            cells.append("1")
        else:
            cells.append("0")
    return "".join(cells)


def series_folder_name(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.lower().endswith(".mp4"):
        raw = raw[:-4]
    if not raw:
        return ""
    name = safe_filename(raw)
    return "" if name == "未命名视频" else name


def plan_jobs(
    items: list[tuple[str, str]], series: str, output_dir: str
) -> list[tuple[str, str, str]]:
    """Return (url, file_stem, dest_dir) for each episode/link."""
    series = series_folder_name(series)
    dest_root = Path(output_dir)
    multi = len(items) >= 2
    has_titles = any(name for name, _ in items)
    if series and (multi or has_titles):
        dest = dest_root / series
    elif series and len(items) == 1:
        return [(items[0][1], series, str(dest_root))]
    else:
        dest = dest_root
    jobs: list[tuple[str, str, str]] = []
    for name, url in items:
        jobs.append((url, name or title_from_url(url), str(dest)))
    return jobs


@dataclass
class Task:
    id: str
    url: str
    name: str
    output_dir: str
    segment_workers: int
    headers: dict[str, str]
    status: str = "queued"
    done: int = 0
    total: int = 0
    downloaded: int = 0
    speed: float = 0.0
    error: str = ""
    output: str = ""
    tmp_dir: str = ""
    seg_state: bytearray = field(default_factory=bytearray)
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    proc: subprocess.Popen | None = field(default=None, repr=False)
    _tick_t: float = 0.0
    _tick_b: int = 0

    def snapshot(self) -> dict:
        with self.lock:
            done = self.done
            total = self.total
            status = self.status
            speed = self.speed
            downloaded = self.downloaded
            mosaic = pack_mosaic(self.seg_state)
            name = self.name
            error = self.error
            output = self.output
            url = self.url
            task_id = self.id
        progress = 0.0
        if status == "merging":
            progress = 97.0
        elif status == "done":
            progress = 100.0
            mosaic = mosaic.replace("0", "2").replace("1", "2")
        elif total:
            progress = min(96.0, done / total * 96.0)
        remaining = None
        if speed > 0 and total > done and done:
            avg = downloaded / max(done, 1)
            remaining = (total - done) * avg / speed
        return {
            "id": task_id,
            "url": url,
            "name": name,
            "status": status,
            "done": done,
            "total": total,
            "progress": round(progress, 1),
            "speed": format_speed(speed) if status == "downloading" else "",
            "eta": format_eta(remaining) if status == "downloading" else "",
            "error": error,
            "output": output,
            "mosaic": mosaic,
        }


class DownloadManager:
    def __init__(self, ffmpeg: str | None):
        self.ffmpeg = ffmpeg
        self.max_tasks = 3
        self.tasks: dict[str, Task] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._dispatch_loop, daemon=True, name="liuying-dispatch").start()

    def enqueue(self, items: list[tuple[str, str]], options: dict) -> list[dict]:
        output_dir = str(options.get("output_dir") or str(Path.home() / "Downloads" / "流影"))
        workers = max(1, min(64, int(options.get("segment_workers") or 16)))
        self.max_tasks = max(1, min(8, int(options.get("task_workers") or 3)))
        headers = {"User-Agent": str(options.get("user_agent") or DEFAULT_UA)}
        referer = str(options.get("referer") or "").strip()
        if referer:
            headers["Referer"] = referer
        jobs = plan_jobs(items, str(options.get("filename") or ""), output_dir)
        created: list[dict] = []
        with self.lock:
            for url, name, dest_dir in jobs:
                Path(dest_dir).mkdir(parents=True, exist_ok=True)
                task = Task(
                    id=uuid.uuid4().hex[:10],
                    url=url,
                    name=name,
                    output_dir=dest_dir,
                    segment_workers=workers,
                    headers=headers,
                )
                self.tasks[task.id] = task
                self.order.append(task.id)
                created.append(task.snapshot())
        return created

    def pause(self, task_id: str) -> dict:
        task = self._get(task_id)
        if task.status in {"downloading", "parsing"}:
            task.pause_event.set()
            task.status = "paused"
            task.speed = 0
        return task.snapshot()

    def resume(self, task_id: str) -> dict:
        task = self._get(task_id)
        if task.status == "paused":
            task.pause_event.clear()
            task.status = "downloading" if task.total else "parsing"
        return task.snapshot()

    def cancel(self, task_id: str) -> dict:
        task = self._get(task_id)
        if task.status in {"done", "cancelled"}:
            return task.snapshot()
        task.cancel_event.set()
        task.pause_event.clear()
        self._kill_proc(task)
        self._purge_files(task)
        task.speed = 0
        task.status = "cancelled"
        return task.snapshot()

    def retry(self, task_id: str) -> dict:
        task = self._get(task_id)
        if task.status not in {"error", "cancelled"}:
            return task.snapshot()
        self._kill_proc(task)
        self._purge_files(task)
        with task.lock:
            task.status = "queued"
            task.done = 0
            task.total = 0
            task.downloaded = 0
            task.speed = 0.0
            task.error = ""
            task.output = ""
            task.tmp_dir = ""
            task.seg_state = bytearray()
            task.proc = None
            task._tick_t = 0.0
            task._tick_b = 0
        task.cancel_event.clear()
        task.pause_event.clear()
        return task.snapshot()

    def remove(self, task_id: str) -> dict:
        task = self._get(task_id)
        if task.status not in {"error", "cancelled"}:
            raise HLSError("只能删除失败或已取消的任务")
        self._kill_proc(task)
        self._purge_files(task)
        with self.lock:
            self.tasks.pop(task_id, None)
            if task_id in self.order:
                self.order.remove(task_id)
        return {"ok": True, "id": task_id}

    def _kill_proc(self, task: Task) -> None:
        proc = task.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
        task.proc = None

    def _purge_files(self, task: Task) -> None:
        if task.tmp_dir:
            shutil.rmtree(task.tmp_dir, ignore_errors=True)
            task.tmp_dir = ""
        if task.output:
            path = Path(task.output)
            if path.exists() and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass

    def shutdown(self) -> None:
        self._stop.set()
        with self.lock:
            tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel_event.set()
            task.pause_event.clear()
            self._kill_proc(task)
            if task.status not in {"done", "cancelled", "error"}:
                task.status = "cancelled"
                task.speed = 0

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [self.tasks[tid].snapshot() for tid in self.order if tid in self.tasks]

    def _get(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if not task:
            raise HLSError("任务不存在")
        return task

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.12)
            if self._stop.is_set():
                return
            task = None
            with self.lock:
                held = sum(
                    1
                    for item in self.tasks.values()
                    if item.status in {"parsing", "downloading", "merging", "paused"}
                )
                if held < self.max_tasks:
                    for tid in self.order:
                        item = self.tasks[tid]
                        if item.status == "queued":
                            item.status = "parsing"
                            task = item
                            break
            if task:
                threading.Thread(
                    target=self._execute, args=(task,), daemon=True, name=f"task-{task.id}"
                ).start()

    def _execute(self, task: Task) -> None:
        try:
            self._checkpoint(task)
            session = self._session(task)
            task.name = safe_filename(task.name)
            dest = unique_path(Path(task.output_dir), task.name)
            task.output = str(dest)
            try:
                segments, playlist_url = self._load_playlist(session, task.url, task)
            except HLSError as exc:
                if self.ffmpeg and ("SAMPLE-AES" in str(exc) or "暂不支持" in str(exc)):
                    task.status = "merging"
                    self._ffmpeg_direct(task, task.url, dest)
                    if task.cancel_event.is_set():
                        raise Cancelled()
                    if dest.exists() and dest.stat().st_size > 0:
                        task.status = "done"
                        return
                raise
            if not segments:
                raise HLSError("播放列表里没有可用分片")
            with task.lock:
                task.total = len(segments)
                task.seg_state = bytearray(len(segments))
            tmp = dest.parent / f".liuying_{task.id}"
            tmp.mkdir(parents=True, exist_ok=True)
            task.tmp_dir = str(tmp)
            task.status = "downloading"
            self._download_segments(session, task, segments, tmp)
            self._checkpoint(task)
            with task.lock:
                task.seg_state = bytearray(b"\x02" * len(task.seg_state))
            task.status = "merging"
            self._merge(task, tmp, dest, playlist_url)
            if task.cancel_event.is_set():
                raise Cancelled()
            shutil.rmtree(tmp, ignore_errors=True)
            task.tmp_dir = ""
            task.speed = 0
            task.status = "done"
        except Cancelled:
            task.status = "cancelled"
            self._kill_proc(task)
            self._purge_files(task)
        except Exception as exc:
            self._kill_proc(task)
            if task.cancel_event.is_set():
                task.status = "cancelled"
                self._purge_files(task)
            else:
                task.status = "error"
                task.error = str(exc) or "下载失败"
                if task.tmp_dir:
                    shutil.rmtree(task.tmp_dir, ignore_errors=True)
                    task.tmp_dir = ""

    def _session(self, task: Task) -> requests.Session:
        session = requests.Session()
        session.headers.update(task.headers)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=task.segment_workers,
            pool_maxsize=task.segment_workers,
            max_retries=0,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _checkpoint(self, task: Task) -> None:
        if task.cancel_event.is_set():
            raise Cancelled()
        while task.pause_event.is_set():
            if task.cancel_event.is_set():
                raise Cancelled()
            time.sleep(0.15)

    def _fetch(
        self,
        session: requests.Session,
        url: str,
        extra: dict | None = None,
        task: Task | None = None,
    ) -> requests.Response:
        last_error = None
        for attempt in range(3):
            if task:
                self._checkpoint(task)
            try:
                response = session.get(url, timeout=20, headers=extra or {}, allow_redirects=True)
                response.raise_for_status()
                return response
            except Cancelled:
                raise
            except Exception as exc:
                last_error = exc
                time.sleep(0.4 * (attempt + 1))
        raise HLSError(f"请求失败：{last_error}")

    def _load_playlist(
        self, session: requests.Session, url: str, task: Task, depth: int = 0
    ) -> tuple[list[Segment], str]:
        self._checkpoint(task)
        if depth > 4:
            raise HLSError("播放列表嵌套过深")
        text = self._fetch(session, url, task=task).text
        if not text.lstrip().startswith("#EXTM3U"):
            raise HLSError("不是有效的 m3u8 播放列表")
        variants: list[tuple[int, str]] = []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = parse_attrs(line.split(":", 1)[1])
                bandwidth = int(attrs.get("BANDWIDTH") or 0)
                if index + 1 < len(lines) and not lines[index + 1].startswith("#"):
                    variants.append((bandwidth, urljoin(url, lines[index + 1])))
        if variants:
            variants.sort(key=lambda item: item[0], reverse=True)
            return self._load_playlist(session, variants[0][1], task, depth + 1)
        return self._parse_media(session, url, lines, task), url

    def _parse_media(
        self,
        session: requests.Session,
        base: str,
        lines: list[str],
        task: Task,
    ) -> list[Segment]:
        segments: list[Segment] = []
        key: bytes | None = None
        iv_override: bytes | None = None
        sequence = 0
        duration = 0.0
        byterange: tuple[int, int] | None = None
        next_offset = 0
        key_cache: dict[str, bytes] = {}

        def current_iv() -> bytes | None:
            if key is None:
                return None
            if iv_override is not None:
                return iv_override
            return parse_iv("", sequence)

        for line in lines:
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                sequence = int(line.split(":", 1)[1].strip() or 0)
            elif line.startswith("#EXT-X-KEY:"):
                attrs = parse_attrs(line.split(":", 1)[1])
                method = (attrs.get("METHOD") or "NONE").upper()
                if method == "NONE":
                    key = None
                    iv_override = None
                elif method == "AES-128":
                    uri = attrs.get("URI")
                    if not uri:
                        raise HLSError("AES-128 缺少密钥地址")
                    key_url = urljoin(base, uri)
                    if key_url not in key_cache:
                        self._checkpoint(task)
                        key_cache[key_url] = decode_key(self._fetch(session, key_url, task=task).content)
                    key = key_cache[key_url]
                    iv_override = parse_iv(attrs["IV"], sequence) if "IV" in attrs else None
                else:
                    raise HLSError(f"暂不支持 {method} 加密，可换源或用 ffmpeg 直拉")
            elif line.startswith("#EXT-X-MAP:"):
                attrs = parse_attrs(line.split(":", 1)[1])
                init_url = urljoin(base, attrs["URI"])
                init_range = None
                if "BYTERANGE" in attrs:
                    init_range = self._parse_range(attrs["BYTERANGE"], 0)[0]
                segments.append(
                    Segment(
                        url=init_url,
                        sequence=sequence,
                        key=key,
                        iv=current_iv(),
                        byte_range=init_range,
                        is_init=True,
                    )
                )
            elif line.startswith("#EXTINF:"):
                duration = float(line.split(":", 1)[1].split(",")[0] or 0)
            elif line.startswith("#EXT-X-BYTERANGE:"):
                byterange, next_offset = self._parse_range(line.split(":", 1)[1], next_offset)
            elif not line.startswith("#"):
                segments.append(
                    Segment(
                        url=urljoin(base, line),
                        sequence=sequence,
                        duration=duration,
                        key=key,
                        iv=current_iv(),
                        byte_range=byterange,
                    )
                )
                sequence += 1
                duration = 0.0
                if byterange:
                    next_offset = byterange[0] + byterange[1]
                byterange = None
        return segments

    def _parse_range(self, raw: str, fallback_offset: int) -> tuple[tuple[int, int], int]:
        text = raw.strip()
        if "@" in text:
            length_s, offset_s = text.split("@", 1)
            offset = int(offset_s)
        else:
            length_s, offset = text, fallback_offset
        length = int(length_s)
        return (offset, length), offset + length

    def _download_segments(
        self,
        session: requests.Session,
        task: Task,
        segments: list[Segment],
        tmp: Path,
    ) -> None:
        task._tick_t = time.time()
        task._tick_b = 0

        def mark(index: int, flag: int) -> None:
            with task.lock:
                if index < len(task.seg_state):
                    task.seg_state[index] = flag

        def one(index: int, segment: Segment) -> None:
            path = tmp / f"{index:05d}.ts"
            if path.exists() and path.stat().st_size > 0:
                with task.lock:
                    if index < len(task.seg_state):
                        task.seg_state[index] = 2
                    task.done += 1
                return
            mark(index, 1)
            self._checkpoint(task)
            headers = {}
            if segment.byte_range:
                start, length = segment.byte_range
                headers["Range"] = f"bytes={start}-{start + length - 1}"
            data = self._fetch(session, segment.url, headers, task).content
            if segment.key and segment.iv:
                data = aes128_decrypt(data, segment.key, segment.iv)
            path.write_bytes(data)
            now = time.time()
            with task.lock:
                if index < len(task.seg_state):
                    task.seg_state[index] = 2
                task.done += 1
                task.downloaded += len(data)
                elapsed = now - task._tick_t
                if elapsed >= 0.4:
                    task.speed = (task.downloaded - task._tick_b) / elapsed
                    task._tick_t = now
                    task._tick_b = task.downloaded

        workers = max(1, min(task.segment_workers, len(segments)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, index, segment) for index, segment in enumerate(segments)]
            for future in as_completed(futures):
                if task.cancel_event.is_set():
                    pool.shutdown(wait=True, cancel_futures=True)
                    raise Cancelled()
                future.result()

    def _merge(self, task: Task, tmp: Path, dest: Path, playlist_url: str) -> None:
        if not self.ffmpeg:
            raise HLSError("未找到 ffmpeg，请确认已安装并在 PATH 中")
        files = sorted(tmp.glob("*.ts"))
        if not files:
            raise HLSError("没有可合并的分片")
        listing = tmp / "concat.txt"
        lines = []
        for item in files:
            escaped = str(item).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        listing.write_text("\n".join(lines), encoding="utf-8")
        commands = [
            [
                self.ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            [
                self.ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                "-bsf:a",
                "aac_adtstoasc",
                "-movflags",
                "+faststart",
                str(dest),
            ],
        ]
        last_error = ""
        for command in commands:
            result = self._run_ffmpeg(task, command)
            if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                return
            last_error = (result.stderr or result.stdout or "ffmpeg 合并失败").strip()
        self._checkpoint(task)
        self._ffmpeg_direct(task, playlist_url, dest)
        if dest.exists() and dest.stat().st_size > 0:
            return
        raise HLSError(last_error.splitlines()[-1] if last_error else "合并失败")

    def _run_ffmpeg(self, task: Task, command: list[str]) -> subprocess.CompletedProcess:
        self._checkpoint(task)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_popen_kwargs(),
        )
        task.proc = proc
        try:
            while proc.poll() is None:
                if task.cancel_event.is_set():
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        pass
                    raise Cancelled()
                time.sleep(0.12)
            if task.cancel_event.is_set():
                raise Cancelled()
            stdout, stderr = proc.communicate()
            return subprocess.CompletedProcess(command, proc.returncode or 0, stdout, stderr)
        finally:
            task.proc = None

    def _ffmpeg_direct(self, task: Task, url: str, dest: Path) -> None:
        header_lines = "".join(f"{key}: {value}\r\n" for key, value in task.headers.items())
        command = [
            self.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-user_agent",
            task.headers.get("User-Agent", DEFAULT_UA),
            "-headers",
            header_lines,
            "-i",
            url,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(dest),
        ]
        self._run_ffmpeg(task, command)
