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
        if result.returncode != 0:
            return None
        try:
            data = parse_json_output(result.stdout)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def wait_installed(self, *, timeout: float = 600, interval: float = 5,
                       sleep: Callable[[float], None] = time.sleep) -> dict:
        """Wait until the image entrypoint has finished installing or upgrading."""
        deadline = time.monotonic() + timeout
        last: dict | None = None
        while True:
            last = self.status()
            if last and last.get("installed") and not last.get("maintenance") \
                    and not last.get("needsDbUpgrade"):
                return last
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
