import os
import shlex
import time

from .command import run_command, truncate
from .storage import Job


class ScriptState(object):
    def __init__(self, bitools_bin, allowed, last_command="", last_output=""):
        self.bitools_bin = bitools_bin
        self.allowed = allowed
        self.last_command = last_command
        self.last_output = last_output

    def to_dict(self):
        return {
            "bitools_bin": self.bitools_bin,
            "allowed": self.allowed,
            "last_command": self.last_command,
            "last_output": self.last_output,
        }


class ScriptService(object):
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = ScriptState(cfg.oas.bitools_bin, cfg.scripts.allowed)

    def state_dict(self):
        saved = self.store.get_json("script_state", {})
        data = self.state.to_dict()
        data.update(saved)
        return data

    def preview(self, script, raw_args):
        command = self._command(script, raw_args)
        output = "Preview only. Oracle 문서 기준 service instance/BAR 스크립트는 offline 실행 조건을 확인해야 합니다."
        self._record("script_preview", command, "SUCCESS", 0, output, time.time(), time.time(), "")

    def run(self, script, raw_args):
        command = self._command(script, raw_args)
        result = run_command(command, cwd=self.cfg.oas.bitools_bin, timeout=3600, log_dir=os.path.join(self.cfg.paths.log_dir, "jobs"))
        self._record("script_run", result.command, result.status, result.exit_code, result.output, result.started_at, result.ended_at, result.log_path)
        if result.status != "SUCCESS":
            raise RuntimeError(result.output or "script execution failed")

    def _command(self, script, raw_args):
        script = (script or "").strip()
        if script not in self.cfg.scripts.allowed:
            raise ValueError("script is not allowed: {0}".format(script))
        if any(sep in script for sep in ("/", "\\")):
            raise ValueError("script name must not contain path separators")
        path = os.path.join(self.cfg.oas.bitools_bin, script)
        args = shlex.split(raw_args or "")
        return [path] + args

    def _record(self, job_type, command, status, exit_code, output, started, ended, log_path):
        command_text = " ".join(shlex.quote(item) for item in command)
        state = self.state.to_dict()
        state["last_command"] = command_text
        state["last_output"] = output
        self.store.set_json("script_state", state)
        self.store.add(Job(job_type, command_text, status, truncate(output), exit_code, started, ended, log_path))