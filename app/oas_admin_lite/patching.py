from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .command import run_command, truncate
from .config import AppConfig
from .storage import Job, JobStore


@dataclass
class PatchState:
    oracle_home: str
    opatch_path: str
    allowed_patch_dirs: list[str]
    last_command: str = ""
    last_output: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class PatchService:
    def __init__(self, cfg: AppConfig, store: JobStore):
        self.cfg = cfg
        self.store = store
        self.state = PatchState(
            oracle_home=cfg.oas.oracle_home,
            opatch_path=os.path.join(cfg.oas.oracle_home, "OPatch", "opatch"),
            allowed_patch_dirs=cfg.patch.allowed_patch_dirs,
        )

    def state_dict(self) -> dict:
        saved = self.store.get_json("patch_state", {})
        data = self.state.to_dict()
        data.update(saved)
        return data

    def inventory(self) -> None:
        result = run_command([self.state.opatch_path, "lsinventory"], timeout=300, log_dir=os.path.join(self.cfg.paths.log_dir, "jobs"))
        self._record("opatch_lsinventory", result.command, result.status, result.exit_code, result.output, result.started_at, result.ended_at, result.log_path)
        if result.status != "SUCCESS":
            raise RuntimeError(result.output or "opatch lsinventory failed")

    def precheck(self, patch_path: str) -> None:
        patch_dir = self._validate_patch_path(patch_path)
        output = [
            "패치 사전 점검 결과",
            f"Patch Directory: {patch_dir}",
            f"ORACLE_HOME: {self.cfg.oas.oracle_home}",
            f"OPatch: {self.state.opatch_path}",
            f"Patch dir exists: {Path(patch_dir).is_dir()}",
            f"ORACLE_HOME writable: {os.access(self.cfg.oas.oracle_home, os.W_OK)}",
            f"Patch dir readable: {os.access(patch_dir, os.R_OK)}",
        ]
        readme = find_readme(patch_dir)
        if readme:
            output.append(f"README detected: {readme}")
            output.append(f"Root step hint: {detect_root_hint(readme)}")
        else:
            output.append("README detected: no")
        command = f"precheck {patch_dir}"
        self._record("patch_precheck", [command], "SUCCESS", 0, "\n".join(output), time.time(), time.time(), "")

    def preview(self, patch_path: str) -> None:
        patch_dir = self._validate_patch_path(patch_path)
        command = ["cd", patch_dir, "&&", self.state.opatch_path, "apply"]
        output = "Preview only. 실제 실행 명령을 확인한 뒤 APPLY 입력으로 실행합니다."
        self._record("patch_preview", command, "SUCCESS", 0, output, time.time(), time.time(), "")

    def apply(self, patch_path: str) -> None:
        patch_dir = self._validate_patch_path(patch_path)
        result = run_command([self.state.opatch_path, "apply"], cwd=patch_dir, timeout=3600, log_dir=os.path.join(self.cfg.paths.log_dir, "jobs"))
        self._record("patch_apply", result.command, result.status, result.exit_code, result.output, result.started_at, result.ended_at, result.log_path)
        if result.status != "SUCCESS":
            raise RuntimeError(result.output or "opatch apply failed")

    def _validate_patch_path(self, patch_path: str) -> str:
        if not patch_path or not patch_path.strip():
            raise ValueError("patch path is required")
        candidate = os.path.abspath(os.path.normpath(patch_path.strip()))
        allowed = [os.path.abspath(os.path.normpath(item)) for item in self.cfg.patch.allowed_patch_dirs]
        if not any(candidate == item or candidate.startswith(item + os.sep) for item in allowed):
            raise ValueError("patch path must be under allowed directories: " + ", ".join(self.cfg.patch.allowed_patch_dirs))
        if not os.path.isdir(candidate):
            raise ValueError("patch path must be an existing directory")
        return candidate

    def _record(self, job_type: str, command: list[str], status: str, exit_code: int, output: str, started: float, ended: float, log_path: str) -> None:
        command_text = " ".join(command)
        state = self.state.to_dict()
        state["last_command"] = command_text
        state["last_output"] = output
        self.store.set_json("patch_state", state)
        self.store.add(Job(job_type, command_text, status, truncate(output), exit_code, started, ended, log_path))


def find_readme(patch_dir: str) -> str:
    for name in ("README.txt", "README", "readme.txt", "Readme.txt"):
        path = os.path.join(patch_dir, name)
        if os.path.exists(path):
            return path
    return ""


def detect_root_hint(readme_path: str) -> str:
    try:
        text = Path(readme_path).read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return "unknown"
    needles = ("root.sh", "as root", "root user", "sudo")
    return "manual root step may be required" if any(item in text for item in needles) else "not detected"
