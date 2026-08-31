from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    segment_workers INTEGER NOT NULL,
    headers_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    done INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT '',
    tmp_dir TEXT NOT NULL DEFAULT '',
    seg_state BLOB,
    sort_order INTEGER NOT NULL DEFAULT 0,
    finished_at TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    series TEXT NOT NULL DEFAULT ''
);
"""


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "finished_at" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN finished_at TEXT NOT NULL DEFAULT ''"
            )
        if "archived" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        if "series" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN series TEXT NOT NULL DEFAULT ''"
            )
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert(self, data: dict[str, Any], sort_order: int) -> None:
        payload = (
            data["id"],
            data["url"],
            data["name"],
            data["output_dir"],
            int(data["segment_workers"]),
            json.dumps(data.get("headers") or {}, ensure_ascii=False),
            data["status"],
            int(data.get("done") or 0),
            int(data.get("total") or 0),
            int(data.get("downloaded") or 0),
            data.get("error") or "",
            data.get("output") or "",
            data.get("tmp_dir") or "",
            data.get("seg_state"),
            sort_order,
            data.get("finished_at") or "",
            1 if data.get("archived") else 0,
            data.get("series") or "",
        )
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, url, name, output_dir, segment_workers, headers_json,
                        status, done, total, downloaded, error, output, tmp_dir,
                        seg_state, sort_order, finished_at, archived, series
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        url=excluded.url,
                        name=excluded.name,
                        output_dir=excluded.output_dir,
                        segment_workers=excluded.segment_workers,
                        headers_json=excluded.headers_json,
                        status=excluded.status,
                        done=excluded.done,
                        total=excluded.total,
                        downloaded=excluded.downloaded,
                        error=excluded.error,
                        output=excluded.output,
                        tmp_dir=excluded.tmp_dir,
                        seg_state=excluded.seg_state,
                        sort_order=excluded.sort_order,
                        finished_at=excluded.finished_at,
                        archived=excluded.archived,
                        series=excluded.series
                    """,
                    payload,
                )
                conn.commit()

    def delete(self, task_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()

    def load_all(self) -> list[sqlite3.Row]:
        with self._lock:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT * FROM tasks ORDER BY sort_order ASC, rowid ASC"
                ).fetchall()
