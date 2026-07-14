import os
import platform
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from .scripts_runner import allowed_scripts


class Check(object):
    def __init__(self, name, value, status, detail=""):
        self.name = name
        self.value = value
        self.status = status
        self.detail = detail


class Metric(object):
    def __init__(self, name, value, unit="", percent=0, status="OK", detail="", metadata=None):
        self.name = name
        self.value = value
        self.unit = unit
        self.percent = percent
        self.status = status
        self.detail = detail
        self.metadata = metadata or []


class Snapshot(object):
    def __init__(self, hostname, os_name, arch, checked_at, oas_checks, service_checks, resource_checks, metrics):
        self.hostname = hostname
        self.os_name = os_name
        self.arch = arch
        self.checked_at = checked_at
        self.oas_checks = oas_checks
        self.service_checks = service_checks
        self.resource_checks = resource_checks
        self.metrics = metrics
        self.checks = oas_checks + service_checks + resource_checks


class ResourceCollector(object):
    def __init__(self, cfg):
        self.cfg = cfg

    def snapshot(self):
        oas_checks = self._oas_checks()
        if platform.system().lower() == "linux":
            service_checks = self._service_checks()
            metrics = self._linux_metrics()
            resource_checks = self._linux_checks()
        else:
            service_checks = [Check("OAS/OHS Runtime", platform.system(), "WARN", "Linux 서버에서만 서비스 상태를 수집합니다.")]
            metrics = [Metric("Runtime OS", platform.system(), "", 0, "WARN", "운영 대상은 Linux입니다. 현재 환경에서는 일부 지표가 제한됩니다.")]
            resource_checks = [Check("Runtime OS", platform.system(), "WARN", "운영 대상은 Linux입니다. 현재 환경에서는 일부 OS 점검이 제한됩니다.")]
        return Snapshot(platform.node(), platform.system(), platform.machine(), time.time(), oas_checks, service_checks, resource_checks, metrics)

    def _oas_checks(self):
        checks = [
            self._path_check("ORACLE_HOME", self.cfg.oas.oracle_home, "FMW/OAS Oracle Home"),
            self._path_check("DOMAIN_HOME", self.cfg.oas.domain_home, "OAS domain home"),
            self._path_check("bitools/bin", self.cfg.oas.bitools_bin, "OAS 관리 스크립트 경로", executable=True),
            self._path_check("OPatch", os.path.join(self.cfg.oas.oracle_home, "OPatch", "opatch"), "OPatch 실행 파일", executable=True),
        ]
        for script in allowed_scripts(getattr(self.cfg.scripts, "allowed", [])):
            checks.append(self._path_check("Script {0}".format(script), os.path.join(self.cfg.oas.bitools_bin, script), "허용된 OAS 관리 스크립트", executable=True))
        ohs = getattr(self.cfg, "ohs", None)
        if ohs and getattr(ohs, "monitor_local", False):
            checks.extend([
                self._path_check("OHS ORACLE_HOME", getattr(ohs, "oracle_home", ""), "OHS Oracle Home"),
                self._path_check("OHS DOMAIN_HOME", getattr(ohs, "domain_home", ""), "OHS domain home"),
            ])
        return checks

    def _path_check(self, name, path, detail, executable=False):
        context = "수집: 경로 존재·읽기{0} 권한 확인 · 역할: {1}".format("·실행" if executable else "", detail)
        if not path:
            return Check(name, "", "WARN", context)
        p = Path(path)
        if not p.exists():
            return Check(name, path, "WARN", "{0}; 경로 없음".format(context))
        if not os.access(path, os.R_OK):
            return Check(name, path, "WARN", "{0}; 읽기 권한 없음".format(context))
        if executable and not os.access(path, os.X_OK):
            return Check(name, path, "WARN", "{0}; 실행 권한 없음".format(context))
        return Check(name, path, "OK", context)

    def _service_checks(self):
        """Collect the runtime signals that matter while OAS is starting."""
        processes = runtime_process_text()
        component_states, status_error = oas_component_statuses(self.cfg)
        checks = [
            process_service_check("Node Manager", processes, ["nodemanager", "node manager"], "WebLogic 기동과 Managed Server 제어"),
            process_service_check("WebLogic AdminServer", processes, ["weblogic.name=adminserver", "weblogic.name=admin_server"], "OAS 도메인 관리 및 Managed Server 기동"),
            process_service_check("WebLogic BI Managed Server", processes, ["bi_server"], "OAS 애플리케이션 서비스 구동"),
            process_service_check("OBICCS (obiccs1)", processes, ["obiccs"], "Cluster Controller: BI 시스템 컴포넌트 제어"),
            process_service_check("OBIS (obis1)", processes, ["nqsserver", "obis1"], "BI Server: 논리 SQL 및 분석 질의 처리"),
            process_service_check("OBIPS (obips1)", processes, ["sawserver", "obips"], "Presentation Services: Analytics/Classic 웹 요청 처리"),
            process_service_check("OBIJH (obijh1)", processes, ["javahost", "obijh"], "JavaHost: 시각화·차트 등 Java 기반 처리"),
            process_service_check("OBISCH (obisch1)", processes, ["nqscheduler", "obisch"], "Scheduler: 에이전트와 예약 작업 처리"),
            catalog_endpoint_check(self.cfg),
        ]
        checks[3:8] = [
            system_component_check("OBICCS (obiccs1)", "obiccs1", component_states, status_error, "Cluster Controller: BI system component control"),
            system_component_check("OBIS (obis1)", "obis1", component_states, status_error, "BI Server query processing"),
            system_component_check("OBIPS (obips1)", "obips1", component_states, status_error, "Presentation Services request processing"),
            system_component_check("OBIJH (obijh1)", "obijh1", component_states, status_error, "JavaHost processing"),
            system_component_check("OBISCH (obisch1)", "obisch1", component_states, status_error, "Scheduler processing"),
        ]
        ohs = getattr(self.cfg, "ohs", None)
        if ohs and getattr(ohs, "monitor_local", False):
            checks.append(process_service_check("OHS HTTP Server", processes, ["httpd", "/ohs/"], "HTTP/REST 요청 프록시 및 전달"))
        return checks

    def _linux_metrics(self):
        metrics = []
        metrics.append(load_metric())
        metrics.append(memory_metric())
        metrics.append(swap_metric())
        metrics.append(filesystem_metric("/u01"))
        return metrics

    def _linux_checks(self):
        checks = [
            command_check("Load Average", ["uptime"], "1·5·15분 시스템 부하 확인"),
            command_check("Memory", ["free", "-m"], "메모리와 swap 사용량 확인"),
            command_check("Filesystem /u01", ["df", "-h", "/u01"], "OAS 및 Admin Lite 운영 파일시스템 여유 공간 확인"),
            listener_check(),
            process_check(),
        ]
        return checks


def runtime_process_text():
    try:
        proc = subprocess.run(["ps", "-eo", "args"], universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5, check=False)
        return proc.stdout.lower() if proc.returncode == 0 else ""
    except Exception:
        return ""


def oas_component_statuses(cfg):
    """Read OAS system-component states from bitools/status.sh."""
    status_path = os.path.join(cfg.oas.bitools_bin, "status.sh")
    if not os.path.isfile(status_path) or not os.access(status_path, os.X_OK):
        return {}, "status.sh is not executable: {0}".format(status_path)
    try:
        proc = subprocess.run(
            [status_path], cwd=cfg.oas.bitools_bin, universal_newlines=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False,
        )
    except Exception as exc:
        return {}, "status.sh failed: {0}".format(exc)

    states = {}
    pattern = re.compile(r"^\s*(\S+)\s+\S+\s+.*\s+(RUNNING|STARTING|SHUTDOWN|FAILED|UNKNOWN)\s*$", re.IGNORECASE)
    for line in (proc.stdout or "").splitlines():
        match = pattern.match(line)
        if match:
            states[match.group(1).lower()] = match.group(2).upper()
    if states:
        return states, ""
    output = (proc.stdout or "").strip().replace("\n", " ")
    return {}, "status.sh output could not be parsed{0}".format(": " + output[:240] if output else "")


def system_component_check(name, component, states, status_error, role):
    state = states.get(component.lower())
    detail = "Collection: status.sh\nRole: {0}".format(role)
    if not state:
        if status_error:
            detail = "{0}\n{1}".format(detail, status_error)
        return Check(name, "UNKNOWN", "WARN", detail)
    return Check(name, state, "OK" if state == "RUNNING" else "WARN", detail)


def process_service_check(name, process_text, signatures, role):
    active = any(signature in process_text for signature in signatures)
    state = "실행 중" if active else "시작 대기"
    status = "OK" if active else "WARN"
    return Check(name, state, status, "수집: ps -eo args\n역할: {0}".format(role))


def catalog_endpoint_check(cfg):
    oas = cfg.oas
    endpoint = (getattr(oas, "catalog_api_url", "") or "").strip()
    if not endpoint:
        base = (getattr(oas, "catalog_base_url", "") or "").strip()
        path = (getattr(oas, "catalog_api_path", "") or "").strip()
        endpoint = base.rstrip("/") + "/" + path.lstrip("/") if base else ""
    parsed = urlparse(endpoint)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return Check("Catalog REST Endpoint", "설정 필요", "WARN", "수집: TCP 연결 확인\n역할: OAS/OHS를 통한 Catalog REST 수집 경로 확인")
    target = "{0}:{1}".format(host, port)
    try:
        conn = socket.create_connection((host, port), timeout=3)
        conn.close()
        return Check("Catalog REST Endpoint", "연결 가능 ({0})".format(target), "OK", "수집: TCP 연결 확인\n역할: OAS/OHS를 통한 Catalog REST 수집 경로 확인")
    except Exception:
        return Check("Catalog REST Endpoint", "연결 대기 ({0})".format(target), "WARN", "수집: TCP 연결 확인\n역할: OAS/OHS를 통한 Catalog REST 수집 경로 확인")


def load_metric():
    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or 1
        percent = int(min(100, round((load1 / float(cores)) * 100)))
        status = threshold_status(percent, 70, 90)
        return Metric("Load", "{0:.2f}".format(load1), "1m", percent, status, metadata=[("수집", "os.getloadavg()"), ("역할", "CPU 코어 대비 1분 부하"), ("CPU cores", str(cores)), ("5m / 15m", "{0:.2f} / {1:.2f}".format(load5, load15))])
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
    return Metric("Memory", human_kb(used), "/ {0}".format(human_kb(total)), percent, status, metadata=[("수집", "/proc/meminfo"), ("역할", "사용 가능 메모리 기준 사용량"), ("Available", human_kb(available))])


def swap_metric():
    info = meminfo()
    total = int(info.get("SwapTotal", 0))
    free = int(info.get("SwapFree", 0))
    if total <= 0:
        return Metric("Swap", "0", "", 0, "OK", "Swap not configured")
    used = total - free
    percent = int(round((used / float(total)) * 100))
    status = threshold_status(percent, 40, 75)
    return Metric("Swap", human_kb(used), "/ {0}".format(human_kb(total)), percent, status, metadata=[("수집", "/proc/meminfo"), ("역할", "swap 사용량 및 여유 공간"), ("Free", human_kb(free))])


def filesystem_metric(path):
    if not os.path.exists(path):
        return Metric("Disk {0}".format(path), "N/A", "", 0, "WARN", "경로 없음")
    usage = shutil.disk_usage(path)
    percent = int(round((usage.used / float(usage.total)) * 100))
    status = threshold_status(percent, 75, 90)
    return Metric("Disk {0}".format(path), human_bytes(usage.used), "/ {0}".format(human_bytes(usage.total)), percent, status, metadata=[("수집", "shutil.disk_usage({0})".format(path)), ("역할", "OAS 및 Admin Lite 운영 파일시스템 여유 공간"), ("Free", human_bytes(usage.free))])


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
        return "HIGH"
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
        return Check("OAS/OHS Listener Ports", "", "WARN", "명령: ss -lnt 또는 netstat -lnt\n역할: OAS/OHS TCP LISTEN 상태 확인; 명령을 찾을 수 없습니다.")
    patterns = [":7001", ":7002", ":7777", ":9500", ":9502", ":9503", ":9704", ":9804"]
    try:
        proc = subprocess.run(cmd, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5, check=False)
    except Exception as exc:
        return Check("OAS/OHS Listener Ports", "", "WARN", "명령: {0}\n역할: OAS/OHS TCP LISTEN 상태 확인; {1}".format(" ".join(cmd), exc))
    if proc.returncode != 0:
        return Check("OAS/OHS Listener Ports", proc.stdout.strip(), "WARN", "명령: {0}\n역할: OAS/OHS TCP LISTEN 상태 확인".format(" ".join(cmd)))

    # Filter before applying the display limit. The full ss output can be long
    # enough that a relevant OHS line (for example :7777) appears after a
    # generic command output truncation point.
    lines = [line.strip() for line in proc.stdout.splitlines() if any(pattern in line for pattern in patterns)]
    if not lines:
        return Check("OAS/OHS Listener Ports", "공통 OAS/OHS 포트가 listen 목록에서 감지되지 않았습니다.", "WARN", "명령: {0}\n역할: OAS/OHS TCP LISTEN 상태 확인".format(" ".join(cmd)))
    port_labels = {
        ":7777": "7777: OHS HTTP/REST",
        ":9500": "9500: WebLogic Administration",
        ":9502": "9502: OAS managed service",
        ":7001": "7001: WebLogic listener",
        ":7002": "7002: WebLogic SSL listener",
        ":9503": "9503: OAS/WebLogic listener",
        ":9704": "9704: OAS listener",
        ":9804": "9804: OAS listener",
    }
    detected = [pattern for pattern in patterns if any(pattern in line for line in lines)]
    detail = "명령: {0}\n역할: {1}".format(" ".join(cmd), " · ".join(port_labels[pattern] for pattern in detected))
    return Check("OAS/OHS Listener Ports", "\n".join(lines[:40]), "OK", detail)


def process_check():
    command = ["ps", "-eo", "pid,ppid,comm,args"]
    keywords = ["weblogic", "nodemanager", "node manager", "bi_server", "obis", "obips", "sawserver", "javahost", "ohs", "httpd"]
    try:
        proc = subprocess.run(command, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5, check=False)
    except Exception as exc:
        return Check("OAS/OHS Processes", "", "WARN", "명령: {0}\n역할: OAS/OHS 핵심 프로세스 감지; {1}".format(" ".join(command), exc))
    if proc.returncode != 0:
        return Check("OAS/OHS Processes", proc.stdout.strip(), "WARN", "명령: {0}\n역할: OAS/OHS 핵심 프로세스 감지".format(" ".join(command)))
    rows = []
    for line in proc.stdout.splitlines():
        lower = line.lower()
        if any(keyword in lower for keyword in keywords) and "oas_admin_lite" not in lower:
            rows.append(format_process_row(line))
    if not rows:
        return Check("OAS/OHS Processes", "OAS/OHS 관련 프로세스가 ps 결과에서 감지되지 않았습니다.", "WARN", "명령: {0}\n역할: OAS/OHS 핵심 프로세스 감지".format(" ".join(command)))
    rows.sort(key=lambda row: (row[0], row[1]))
    header = "역할                         PID    PPID  프로세스        실행 경로/명령"
    value = "\n".join([header] + [row[2] for row in rows[:40]])
    return Check("OAS/OHS Processes", value, "OK", "명령: {0}\n역할: OAS/OHS 핵심 프로세스 감지 · 감지 {1}개, 최대 40개 행 표시".format(" ".join(command), len(rows)))


def format_process_row(line):
    parts = line.strip().split(None, 3)
    if len(parts) < 3:
        return 99, "", line.strip()
    pid, ppid, process = parts[:3]
    args = parts[3] if len(parts) > 3 else process
    role, rank = process_role(process, args)
    command = shorten(args, 160)
    rendered = "{0:<27} {1:>7} {2:>7}  {3:<14} {4}".format(role, pid, ppid, process, command)
    return rank, pid, rendered


def process_role(process, args):
    text = "{0} {1}".format(process, args).lower()
    if "httpd" in text or "/ohs/" in text:
        return "OHS HTTP Server", 10
    if "nodemanager" in text or "node manager" in text:
        return "WebLogic Node Manager", 20
    if "sawserver" in text or "obips" in text:
        return "OAS Presentation Services", 30
    if "obis" in text:
        return "OAS BI Server", 40
    if "javahost" in text:
        return "OAS JavaHost", 50
    if "weblogic" in text and "bi_server" in text:
        return "OAS WebLogic Managed", 60
    if "weblogic" in text:
        return "WebLogic Server", 70
    return "OAS/OHS related", 90


def shorten(value, limit):
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def command_check(name, command, role, timeout=5):
    try:
        proc = subprocess.run(command, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        value = proc.stdout.strip()
        if len(value) > 2000:
            value = value[:2000] + "..."
        return Check(name, value, "OK" if proc.returncode == 0 else "WARN", "명령: {0}\n역할: {1}".format(" ".join(command), role))
    except Exception as exc:
        return Check(name, "", "WARN", "명령: {0}\n역할: {1}; {2}".format(" ".join(command), role, exc))
