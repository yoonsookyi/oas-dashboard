import os

from .command import run_command, truncate
from .storage import Job


class PatchState(object):
    def __init__(self, oracle_home, opatch_path, last_command="", last_output=""):
        self.oracle_home = oracle_home
        self.opatch_path = opatch_path
        self.last_command = last_command
        self.last_output = last_output

    def to_dict(self):
        return {
            "oracle_home": self.oracle_home,
            "opatch_path": self.opatch_path,
            "last_command": self.last_command,
            "last_output": self.last_output,
        }


class PatchService(object):
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.state = PatchState(
            oracle_home=cfg.oas.oracle_home,
            opatch_path=os.path.join(cfg.oas.oracle_home, "OPatch", "opatch"),
        )

    def state_dict(self):
        saved = self.store.get_json("patch_state", {})
        data = self.state.to_dict()
        data.update(saved)
        return data

    def inventory(self):
        result = run_command([self.state.opatch_path, "lsinventory"], timeout=300, log_dir=os.path.join(self.cfg.paths.log_dir, "jobs"))
        self._record("opatch_lsinventory", result.command, result.status, result.exit_code, result.output, result.started_at, result.ended_at, result.log_path)
        if result.status != "SUCCESS":
            raise RuntimeError(result.output or "opatch lsinventory failed")

    def _record(self, job_type, command, status, exit_code, output, started, ended, log_path):
        command_text = " ".join(command)
        state = self.state.to_dict()
        state["last_command"] = command_text
        state["last_output"] = output
        self.store.set_json("patch_state", state)
        self.store.add(Job(job_type, command_text, status, truncate(output), exit_code, started, ended, log_path))