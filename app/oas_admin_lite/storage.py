from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Job:
    type: str
    command: str
    status: str
    message: str = ""
    exit_code: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0
    log_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(os.path.dirname(db_path)).mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists jobs (
                    id integer primary key autoincrement,
                    type text not null,
                    command text not null,
                    status text not null,
                    exit_code integer not null,
                    started_at real not null,
                    ended_at real not null,
                    log_path text not null,
                    message text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists kv (
                    key text primary key,
                    value text not null
                )
                """
            )

    def add(self, job: Job) -> int:
        if not job.started_at:
            job.started_at = time.time()
        if not job.ended_at:
            job.ended_at = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                insert into jobs(type, command, status, exit_code, started_at, ended_at, log_path, message)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job.type, job.command, job.status, job.exit_code, job.started_at, job.ended_at, job.log_path, job.message),
            )
            return int(cur.lastrowid)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select * from jobs order by started_at desc, id desc limit ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_json(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        with self._connect() as conn:
            conn.execute(
                "insert into kv(key, value) values(?, ?) on conflict(key) do update set value=excluded.value",
                (key, payload),
            )

    def get_json(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute("select value from kv where key = ?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row["value"])
