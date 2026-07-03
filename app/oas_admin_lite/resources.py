import os
import platform
import re
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


class Metric(object):
    def __init__(self, name, value, unit="", percent=0, status="OK", detail=""):
        self.name = name
        self.value = value
        self.unit = unit
        self.percent = percent
        self.status = status
        self.detail = detail


class Snapshot(object):
    def __init__(self, hostname, os_name, arch, checked_at, oas_checks, resource_checks, metrics):
        self.hostname = hostname
        self.os_name = os_name
        self.arch = arch
        self.checked_at = checked_at
        self.oas_checks = oas_checks
        self.resource_checks = resource_checks
        self.metrics = metrics
        self.checks = oas_checks + resource_checks


class ResourceCollector(object):
    def __init__(self, cfg):
        self.cfg = cfg

    def snapshot(self):
        oas_checks = self._oas_checks()
        if platform.system().lower() == "linux":
            metrics = self._linux_metrics()
            resource_checks = self._linux_checks()
        else:
            metrics = [Metric("Runtime OS", platform.system(), "", 0, "WARN", "운영 대상은 Linux입니다. 현재 환경에서는 일부 지표가 제한됩니다.")]
            resource_checks = [Check("Runtime OS", platform.system(), "WARN", "운영 대상은 Linux입니다. 현재 환경에서는 일부 OS 점검이 제한됩니다.")]
        return Snapshot(platform.node(), platform.system(), platform.machine(), time.time(), oas_checks, resource_checks, metrics)

    def _oas_checks(self):
        return [
            self._path_check("ORACLE_HOME", self.cfg.oas.oracle_home, "FMW/OAS Oracle Home"),
            self._path_check("DOMAIN_HOME", self.cfg.oas.domain_home, "OAS domain home"),
            self._path_check("bitools/bin", self.cfg.oas.bitools_bin, "OAS 관리 스크립트 경로"),
            self._path_check("OPatch", os.path.join(self.cfg.oas.oracle_home, "OPatch", "opatch"), "OPatch 실행 파일"),
        ]

    def _path_check(self, name, path, detail):
        if not path:
            return Check(name, "", "WARN", detail)
        p = Path(path)
        if not p.exists():
            return Check(name, path, "WARN", "{0}; 경로 없음".format(detail))
        if not os.access(path, os.R_OK):
            return Check(name, path, "WARN", "{0}; 읽기 권한 없음".format(detail))
        return Check(name, path, "OK", detail)

    def _linux_metrics(self):
        metrics = []
        metrics.append(load_metric())
        metrics.append(memory_metric())
        metrics.append(swap_metric())
        metrics.append(filesystem_metric("/u01"))
        return metrics

    def _linux_checks(self):
        checks = [
            command_check("Load Average", ["uptime"]),
            command_check("Memory", ["free", "-m"]),
            command_check("Filesystem /u01", ["df", "-h", "/u01"]),
            listener_check(),
            process_check(),
        ]
        return checks


def load_metric():
    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or 1
        percent = int(min(100, round((load1 / float(cores)) * 100)))
        status = threshold_status(percent, 70, 90)
        return Metric("Load", "{0:.2f}".format(load1), "1m", percent, status, "CPU cores: {0}, 5m: {1:.2f}, 15m: {2:.2f}".format(cores, load5, load15))
    except Exception as exc:
        return Metric("Load", "N/A", "", 0, "WARN", str(exc))


def memory_metric():
    info = meminfo()
    total = int(info.get("MemTotal", 0))
    available = int(info.get("MemAvailable", info.get("MemFree", 0)))
    if total <= 0:
        return Metric("Memory", "N/A", "", 0, "WARN", "MemTotal을 읽을 수 없습니다.")
    used = total - available
    percent = int(round((used / float(total)) * 100))
    status = threshold_status(percent, 75, 90)
    return Metric("Memory", human_kb(used), "/ {0}".format(human_kb(total)), percent, status, "Available: {0}".format(human_kb(available)))


def swap_metric():
    info = meminfo()
    total = int(info.get("SwapTotal", 0))
    free = int(info.get("SwapFree", 0))
    if total <= 0:
        return Metric("Swap", "0", "", 0, "OK", "Swap not configured")
    used = total - free
    percent = int(round((used / float(total)) * 100))
    status = threshold_status(percent, 40, 75)
    return Metric("Swap", human_kb(used), "/ {0}".format(human_kb(total)), percent, status, "Free: {0}".format(human_kb(free)))


def filesystem_metric(path):
    if not os.path.exists(path):
        return Metric("Disk {0}".format(path), "N/A", "", 0, "WARN", "경로 없음")
    usage = shutil.disk_usage(path)
    percent = int(round((usage.used / float(usage.total)) * 100))
    status = threshold_status(percent, 75, 90)
    return Metric("Disk {0}".format(path), human_bytes(usage.used), "/ {0}".format(human_bytes(usage.total)), percent, status, "Free: {0}".format(human_bytes(usage.free)))


def meminfo():
    result = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    result[parts[0].rstrip(":")] = int(parts[1])
    except Exception:
        pass
    return result


def threshold_status(percent, warn, fail):
    if percent >= fail:
        return "FAILED"
    if percent >= warn:
        return "WARN"
    return "OK"


def human_kb(value):
    return human_bytes(int(value) * 1024)


def human_bytes(value):
    value = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return "{0:.1f}{1}".format(value, unit) if unit != "B" else "{0:.0f}{1}".format(value, unit)
        value = value / 1024.0


def listener_check():
    if shutil.which("ss"):
        cmd = ["ss", "-lnt"]
    elif shutil.which("netstat"):
        cmd = ["netstat", "-lnt"]
    else:
        return Check("OAS/OHS Listener Ports", "", "WARN", "ss/netstat 명령을 찾을 수 없습니다.")
    check = command_check("OAS/OHS Listener Ports", cmd)
    if check.status != "OK":
        return check
    patterns = [":7001", ":7002", ":7777", ":9500", ":9502", ":9503", ":9704", ":9804"]
    lines = [line for line in check.value.splitlines() if any(pattern in line for pattern in patterns)]
    if not lines:
        return Check("OAS/OHS Listener Ports", "공통 OAS/OHS 포트가 listen 목록에서 감지되지 않았습니다.", "WARN", "확인 포트: {0}".format(", ".join(patterns)))
    return Check("OAS/OHS Listener Ports", "\n".join(lines), "OK", "감지 포트: {0}".format(", ".join(patterns)))


def process_check():
    check = command_check("OAS/OHS Processes", ["ps", "-eo", "pid,ppid,comm,args"], timeout=5)
    if check.status != "OK":
        return check
    keywords = ["weblogic", "nodemanager", "node manager", "bi_server", "obis", "obips", "sawserver", "javahost", "ohs", "httpd"]
    lines = []
    for line in check.value.splitlines():
        lower = line.lower()
        if any(keyword in lower for keyword in keywords) and "oas_admin_lite" not in lower:
            lines.append(line)
    if not lines:
        return Check("OAS/OHS Processes", "OAS/OHS 관련 프로세스가 ps 결과에서 감지되지 않았습니다.", "WARN", "검색 키워드: {0}".format(", ".join(keywords)))
    return Check("OAS/OHS Processes", "\n".join(lines[:40]), "OK", "최대 40개 행 표시")


def command_check(name, command, timeout=5):
    try:
        proc = subprocess.run(command, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        value = proc.stdout.strip()
        if len(value) > 2000:
            value = value[:2000] + "..."
        return Check(name, value, "OK" if proc.returncode == 0 else "WARN", " ".join(command))
    except Exception as exc:
        return Check(name, "", "WARN", str(exc))