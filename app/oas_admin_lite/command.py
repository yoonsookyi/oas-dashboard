import os
import subprocess
import time
from pathlib import Path


class CommandResult(object):
    def __init__(self, command, cwd, status, exit_code, output, started_at, ended_at, log_path):
        self.command = command
        self.cwd = cwd
        self.status = status
        self.exit_code = exit_code
        self.output = output
        self.started_at = started_at
        self.ended_at = ended_at
        self.log_path = log_path


def run_command(command, cwd="", timeout=300, log_dir=""):
    started = time.time()
    output = ""
    exit_code = 0
    status = "SUCCESS"
    try:
        proc = subprocess.run(
            command,
            cwd=cwd or None,
            universal_newlines=True,
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
        log_path = os.path.join(log_dir, "{0}-{1}.log".format(time.strftime("%Y%m%d-%H%M%S", time.localtime(started)), safe_name))
        Path(log_path).write_text(output, encoding="utf-8", errors="replace")
    return CommandResult(command, cwd, status, exit_code, output, started, ended, log_path)


def truncate(value, limit=1200):
    if len(value) <= limit:
        return value
    return value[:limit] + "..."