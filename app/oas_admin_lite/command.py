from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    command: list[str]
    cwd: str
    status: str
    exit_code: int
    output: str
    started_at: float
    ended_at: float
    log_path: str


def run_command(command: list[str], cwd: str = "", timeout: int = 300, log_dir: str = "") -> CommandResult:
    started = time.time()
    output = ""
    exit_code = 0
    status = "SUCCESS"
    try:
        proc = subprocess.run(
            command,
            cwd=cwd or None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = proc.stdout or ""
        exit_code = proc.returncode
        if proc.returncode != 0:
            status = "FAILED"
    except Exception as exc:
        output = str(exc)
        exit_code = 1
        status = "FAILED"
    ended = time.time()
    log_path = ""
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        safe_name = Path(command[0]).name.replace(".", "_")
        log_path = os.path.join(log_dir, f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(started))}-{safe_name}.log")
        Path(log_path).write_text(output, encoding="utf-8", errors="replace")
    return CommandResult(command, cwd, status, exit_code, output, started, ended, log_path)


def truncate(value: str, limit: int = 1200) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "..."
