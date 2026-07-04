import os
import shlex
import time

from .command import run_command, truncate
from .storage import Job


BLOCKED_SCRIPTS = {"importarchive.sh"}


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


def allowed_scripts(items):
    return [item for item in (items or []) if item not in BLOCKED_SCRIPTS]


class ScriptService(object):
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = ScriptState(cfg.oas.bitools_bin, allowed_scripts(cfg.scripts.allowed))

    def state_dict(self):
        saved = self.store.get_json("script_state", {})
        data = self.state.to_dict()
        data["allowed"] = allowed_scripts(data.get("allowed"))
        last_command = saved.get("last_command", data["last_command"])
        if last_command and any(script in last_command for script in data["allowed"]):
            data["last_command"] = last_command
            data["last_output"] = saved.get("last_output", data["last_output"])
        return data

    def preview(self, script, raw_args, stdin_text=""):
        command = self._command(script, raw_args)
        output = "\uc2a4\ud06c\ub9bd\ud2b8\ub294 \uc2e4\ud589\ud558\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4. \uc2e4\ud589\ub420 \uba85\ub839\uc5b4\ub97c \ud655\uc778\ud55c \ub4a4 \uc2e4\ud589 \ubc84\ud2bc\uc744 \ub204\ub974\uc138\uc694."
        self._record("script_command_check", command, "SUCCESS", 0, output, time.time(), time.time(), "")

    def run(self, script, raw_args, stdin_text=""):
        command = self._command(script, raw_args)
        input_text = self._stdin_payload(stdin_text)
        result = run_command(
            command,
            cwd=self.cfg.oas.bitools_bin,
            timeout=3600,
            log_dir=os.path.join(self.cfg.paths.log_dir, "jobs"),
            input_text=input_text,
        )
        self._record("script_run", result.command, result.status, result.exit_code, result.output, result.started_at, result.ended_at, result.log_path)
        if result.status != "SUCCESS":
            raise RuntimeError(result.output or "script execution failed")

    def _command(self, script, raw_args):
        script = (script or "").strip()
        if script in BLOCKED_SCRIPTS:
            raise ValueError("script is blocked: {0}".format(script))
        if script not in allowed_scripts(self.cfg.scripts.allowed):
            raise ValueError("script is not allowed: {0}".format(script))
        if any(sep in script for sep in ("/", "\\")):
            raise ValueError("script name must not contain path separators")
        path = os.path.join(self.cfg.oas.bitools_bin, script)
        args = shlex.split(raw_args or "")
        return [path] + args

    def _stdin_payload(self, stdin_text):
        if not stdin_text:
            return None
        if stdin_text.endswith("\n"):
            return stdin_text
        return stdin_text + "\n"

    def _record(self, job_type, command, status, exit_code, output, started, ended, log_path):
        command_text = " ".join(shlex.quote(item) for item in command)
        state = self.state.to_dict()
        state["last_command"] = command_text
        state["last_output"] = output
        self.store.set_json("script_state", state)
        self.store.add(Job(job_type, command_text, status, truncate(output), exit_code, started, ended, log_path))
