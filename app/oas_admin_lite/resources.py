from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig


@dataclass
class Check:
    name: str
    value: str
    status: str
    detail: str = ""


@dataclass
class Snapshot:
    hostname: str
    os_name: str
    arch: str
    checked_at: float
    checks: list[Check]


class ResourceCollector:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def snapshot(self) -> Snapshot:
        checks = [
            self._path_check("App Root", self.cfg.paths.root, "앱 배포 루트"),
            self._path_check("Data Dir", self.cfg.paths.data_dir, "작업 이력 및 수집 결과"),
            self._path_check("ORACLE_HOME", self.cfg.oas.oracle_home, "FMW/OAS Oracle Home"),
            self._path_check("DOMAIN_HOME", self.cfg.oas.domain_home, "OAS domain home"),
            self._path_check("bitools/bin", self.cfg.oas.bitools_bin, "OAS 관리 스크립트 경로"),
        ]
        if platform.system().lower() == "linux":
            checks.extend(self._linux_checks())
        else:
            checks.append(Check("Runtime OS", platform.system(), "WARN", "운영 대상은 Linux입니다. 현재 환경에서는 일부 점검이 제한됩니다."))
        return Snapshot(platform.node(), platform.system(), platform.machine(), time.time(), checks)

    def _path_check(self, name: str, path: str, detail: str) -> Check:
        if not path:
            return Check(name, "", "WARN", detail)
        p = Path(path)
        if not p.exists():
            return Check(name, path, "WARN", f"{detail}; 경로 없음")
        if not os.access(path, os.R_OK):
            return Check(name, path, "WARN", f"{detail}; 읽기 권한 없음")
        return Check(name, path, "OK", detail)

    def _linux_checks(self) -> list[Check]:
        checks = [
            command_check("Load Average", ["uptime"]),
            command_check("Memory", ["free", "-m"]),
            command_check("Filesystem /u01", ["df", "-h", "/u01"]),
        ]
        if shutil.which("ss"):
            checks.append(command_check("Listen Ports", ["ss", "-lnt"]))
        elif shutil.which("netstat"):
            checks.append(command_check("Listen Ports", ["netstat", "-lnt"]))
        else:
            checks.append(Check("Listen Ports", "", "WARN", "ss/netstat 명령을 찾을 수 없습니다."))
        return checks


def command_check(name: str, command: list[str], timeout: int = 5) -> Check:
    try:
        proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        value = proc.stdout.strip()
        if len(value) > 1200:
            value = value[:1200] + "..."
        return Check(name, value, "OK" if proc.returncode == 0 else "WARN", " ".join(command))
    except Exception as exc:
        return Check(name, "", "WARN", str(exc))
