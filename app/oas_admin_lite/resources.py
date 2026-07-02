import os
import platform
import shutil
import subprocess
import time
from pathlib import Path


class Check(object):
    def __init__(self, name, value, status, detail=""):
        self.name = name
        self.value = value
        self.status = status
        self.detail = detail


class Snapshot(object):
    def __init__(self, hostname, os_name, arch, checked_at, checks):
        self.hostname = hostname
        self.os_name = os_name
        self.arch = arch
        self.checked_at = checked_at
        self.checks = checks


class ResourceCollector(object):
    def __init__(self, cfg):
        self.cfg = cfg

    def snapshot(self):
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

    def _path_check(self, name, path, detail):
        if not path:
            return Check(name, "", "WARN", detail)
        p = Path(path)
        if not p.exists():
            return Check(name, path, "WARN", "{0}; 경로 없음".format(detail))
        if not os.access(path, os.R_OK):
            return Check(name, path, "WARN", "{0}; 읽기 권한 없음".format(detail))
        return Check(name, path, "OK", detail)

    def _linux_checks(self):
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


def command_check(name, command, timeout=5):
    try:
        proc = subprocess.run(command, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        value = proc.stdout.strip()
        if len(value) > 1200:
            value = value[:1200] + "..."
        return Check(name, value, "OK" if proc.returncode == 0 else "WARN", " ".join(command))
    except Exception as exc:
        return Check(name, "", "WARN", str(exc))