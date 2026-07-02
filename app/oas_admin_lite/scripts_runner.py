from __future__ import annotations

import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from .command import run_command, truncate
from .config import AppConfig
from .storage import Job, JobStore


@dataclass
class ScriptState:
    bitools_bin: str
    allowed: list[str]
    last_command: str = ""
    last_output: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class ScriptService:
    def __init__(self, cfg: AppConfig, store: JobStore):
        self.cfg = cfg
        self.store = store
        self.state = ScriptState(cfg.oas.bitools_bin, cfg.scripts.allowed)

    def state_dict(self) -> dict:
        saved = self.store.get_json("script_state", {})
        data = self.state.to_dict()
        data.update(saved)
        return data

    def preview(self, script: str, raw_args: str) -> None:
        command = self._command(script, raw_args)
        output = "Preview only. Oracle 문서 기준 service instance/BAR 스크립트는 offline 실행 조건을 확인해야 합니다."
        self._record("script_preview", command, "SUCCESS", 0, output, time.time(), time.time(), "")

    def run(self, script: str, raw_args: str) -> None:
        command = self._command(script, raw_args)
        result = run_command(command, cwd=self.cfg.oas.bitools_bin, timeout=3600, log_dir=os.path.join(self.cfg.paths.log_dir, "jobs"))
        self._record("script_run", result.command, result.status, result.exit_code, result.output, result.started_at, result.ended_at, result.log_path)
        if result.status != "SUCCESS":
            raise RuntimeError(result.output or "script execution failed")

    def _command(self, script: str, raw_args: str) -> list[str]:
        script = (script or "").strip()
        if script not in self.cfg.scripts.allowed:
            raise ValueError(f"script is not allowed: {script}")
        if any(sep in script for sep in ("/", "\\")):
            raise ValueError("script name must not contain path separators")
        path = os.path.join(self.cfg.oas.bitools_bin, script)
        args = shlex.split(raw_args or "")
        return [path, *args]

    def _record(self, job_type: str, command: list[str], status: str, exit_code: int, output: str, started: float, ended: float, log_path: str) -> None:
        command_text = " ".join(shlex.quote(item) for item in command)
        state = self.state.to_dict()
        state["last_command"] = command_text
        state["last_output"] = output
        self.store.set_json("script_state", state)
        self.store.add(Job(job_type, command_text, status, truncate(output), exit_code, started, ended, log_path))
