"""Run Nextcloud's occ (and the kit's PHP helpers) inside the nextcloud container."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

CONTAINER = "nextcloud"
WWW_DATA = "www-data"
HELPER_DIR = "/opt/easydeploy"

Runner = Callable[..., subprocess.CompletedProcess]


def parse_json_output(text: str) -> Any:
    """occ may print PHP notices before its JSON; parse from the first bracket."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("occ printed no JSON")
    return json.loads(text[min(starts):])


def _ready(status: dict | None) -> bool:
    return bool(status and status.get("installed") and not status.get("maintenance")
                and not status.get("needsDbUpgrade"))


def _upgrade_pending(status: dict | None) -> bool:
    return bool(status and status.get("installed") and not status.get("maintenance")
                and status.get("needsDbUpgrade"))


class OccError(RuntimeError):
    def __init__(self, args: Sequence[str], result: subprocess.CompletedProcess):
        self.args_list = list(args)
        self.result = result
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {result.returncode}"
        super().__init__(f"occ {' '.join(self.args_list[:2])} failed: {tail}")


@dataclass
class Occ:
    container: str = CONTAINER
    runner: Runner = field(default=subprocess.run)

    def _exec(self, command: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess:
        prefix = ["docker", "exec"]
        if stdin is not None:
            prefix.append("-i")
        return self.runner(
            [*prefix, "-u", WWW_DATA, self.container, *command],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """occ with arguments on the command line. Never pass secrets here."""
        result = self._exec(["php", "occ", "--no-interaction", "--no-ansi", *args])
        if check and result.returncode != 0:
            raise OccError(args, result)
        return result

    def run_secret(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """occ with arguments sent as JSON on stdin, so they stay out of process lists."""
        payload = json.dumps(["--no-interaction", "--no-ansi", *args])
        result = self._exec(["php", f"{HELPER_DIR}/occ-stdin.php"], stdin=payload)
        if check and result.returncode != 0:
            raise OccError(args[:1], result)
        return result

    def helper(self, script: str, payload: dict[str, Any]) -> subprocess.CompletedProcess:
        result = self._exec(["php", f"{HELPER_DIR}/{script}"], stdin=json.dumps(payload))
        if result.returncode != 0:
            raise OccError([script], result)
        return result

    def json(self, *args: str) -> Any:
        return parse_json_output(self.run(*args, "--output=json").stdout)

    def status(self) -> dict | None:
        result = self._exec(["php", "occ", "status", "--output=json", "--no-warnings"])
        try:
            data = parse_json_output(result.stdout or "")
        except ValueError:
            return None
        if not isinstance(data, dict) or "installed" not in data:
            return None
        return data

    def entrypoint_finished(self) -> bool:
        """The image runs upgrades before it replaces itself with Apache."""
        result = self._exec(["cat", "/proc/1/cmdline"])
        if result.returncode != 0 or not result.stdout:
            return False
        command = result.stdout.replace("\x00", " ")
        return "apache2" in command and "entrypoint" not in command

    def wait_installed(self, *, timeout: float = 600, interval: float = 5,
                       sleep: Callable[[float], None] = time.sleep) -> dict:
        """Wait until Nextcloud is installed, idle, and its database matches the code.

        The image entrypoint upgrades Nextcloud when the image version changes. It
        does not notice an already-enabled app whose version changed on a bind
        mount, and then the web UI stays on "Update needed". Once Apache is
        serving, finish that upgrade here.
        """
        deadline = time.monotonic() + timeout
        last: dict | None = None
        attempted = False
        while True:
            last = self.status()
            if _ready(last):
                return last
            if _upgrade_pending(last) and attempted:
                raise RuntimeError(
                    "occ upgrade finished but Nextcloud still needs a database upgrade "
                    f"({json.dumps(last)}). Check: docker logs nextcloud"
                )
            if _upgrade_pending(last) and self.entrypoint_finished():
                attempted = True
                print("Nextcloud still needs a database upgrade; running occ upgrade…",
                      flush=True)
                self.run("upgrade")
                continue
            if time.monotonic() >= deadline:
                state = "not reachable" if last is None else json.dumps(last)
                raise RuntimeError(
                    f"Nextcloud did not finish installing within {int(timeout)}s ({state}). "
                    "Check: docker logs nextcloud"
                )
            sleep(interval)

    def system_set(self, key: str, value: Any, *subkeys: str) -> None:
        args = ["config:system:set", key, *subkeys]
        if isinstance(value, bool):
            args += ["--type=boolean", f"--value={'true' if value else 'false'}"]
        elif isinstance(value, int):
            args += ["--type=integer", f"--value={value}"]
        else:
            args += [f"--value={value}"]
        self.run(*args)

    def app_set(self, app: str, key: str, value: str, *, secret: bool = False) -> None:
        args = ("config:app:set", app, key, f"--value={value}")
        if secret:
            self.run_secret(*args)
        else:
            self.run(*args)

    def apps(self) -> tuple[set[str], set[str]]:
        """(enabled, disabled) app ids."""
        data = self.json("app:list") or {}
        return set((data.get("enabled") or {}).keys()), set((data.get("disabled") or {}).keys())
