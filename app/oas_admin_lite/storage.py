import json
import os
import sqlite3
import time
from pathlib import Path


class Job(object):
    def __init__(self, type, command, status, message="", exit_code=0, started_at=0.0, ended_at=0.0, log_path=""):
        self.type = type
        self.command = command
        self.status = status
        self.message = message
        self.exit_code = exit_code
        self.started_at = started_at
        self.ended_at = ended_at
        self.log_path = log_path

    def to_dict(self):
        return {
            "type": self.type,
            "command": self.command,
            "status": self.status,
            "message": self.message,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "log_path": self.log_path,
        }


class JobStore(object):
    def __init__(self, db_path):
        self.db_path = db_path
        Path(os.path.dirname(db_path)).mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
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
            conn.commit()
        finally:
            conn.close()

    def add(self, job):
        if not job.started_at:
            job.started_at = time.time()
        if not job.ended_at:
            job.ended_at = time.time()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                insert into jobs(type, command, status, exit_code, started_at, ended_at, log_path, message)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job.type, job.command, job.status, job.exit_code, job.started_at, job.ended_at, job.log_path, job.message),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def list(self, limit=100):
        conn = self._connect()
        try:
            rows = conn.execute(
                "select * from jobs order by started_at desc, id desc limit ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def set_json(self, key, value):
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        conn = self._connect()
        try:
            conn.execute("delete from kv where key = ?", (key,))
            conn.execute("insert into kv(key, value) values(?, ?)", (key, payload))
            conn.commit()
        finally:
            conn.close()

    def get_json(self, key, default=None):
        conn = self._connect()
        try:
            row = conn.execute("select value from kv where key = ?", (key,)).fetchone()
            if not row:
                return default
            return json.loads(row["value"])
        finally:
            conn.close()