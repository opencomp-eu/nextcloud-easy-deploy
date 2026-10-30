from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scripts import apply


def base_config(tmp_path: Path, **overrides) -> dict:
    config = {
        "nextcloud": {"domain": "cloud.acme.test", "data_dir": str(tmp_path / "data")},
        "office": {"enabled": True, "domain": "office.acme.test"},
        "talk": {"enabled": True, "turn_port": 3478},
        "proxy": {"mode": "integrate"},
    }
    for key, value in overrides.items():
        config[key] = value
    return config


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every path apply.py writes at a temporary state directory."""
    root = tmp_path / ".nextcloud-easy-deploy"
    paths = {
        "STATE_DIR": root,
        "SECRETS_PATH": root / "secrets.yaml",
        "COMPOSE_ENV_PATH": root / "compose.env",
        "INTEGRATION_DIR": root / "integration",
        "INTEGRATION_CADDY_FRAGMENT": root / "integration" / "caddy.caddy",
        "RUNTIME_OVERLAY_PATH": root / "compose" / "runtime.yml",
        "COMPOSE_FILES_LIST": root / "compose" / "files.txt",
        "CADDYFILE": tmp_path / "Caddyfile",
    }
    for name, value in paths.items():
        monkeypatch.setattr(apply, name, value)
    return root


@dataclass
class FakeOcc:
    """Records occ calls; answers app:list from its own state."""

    enabled: set[str] = field(default_factory=set)
    disabled: set[str] = field(default_factory=set)
    fail: set[str] = field(default_factory=set)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    secret_calls: list[tuple[str, ...]] = field(default_factory=list)
    helpers: list[tuple[str, dict]] = field(default_factory=list)
    helper_stdout: str = "updated\nprovisioned:2\n"

    def _result(self, args: tuple[str, ...]) -> subprocess.CompletedProcess:
        code = 1 if args and args[0] in self.fail else 0
        return subprocess.CompletedProcess(list(args), code, "", "")

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        self.calls.append(args)
        if args[:1] == ("app:install",) and args[1] not in self.fail:
            self.enabled.add(args[1])
        if args[:1] == ("app:enable",):
            self.disabled.discard(args[1])
            self.enabled.add(args[1])
        if args[:1] == ("app:disable",):
            self.enabled.discard(args[1])
            self.disabled.add(args[1])
        return self._result(args)

    def run_secret(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        self.secret_calls.append(args)
        return self._result(args)

    def helper(self, script: str, payload: dict) -> subprocess.CompletedProcess:
        self.helpers.append((script, payload))
        return subprocess.CompletedProcess([script], 0, self.helper_stdout, "")

    def system_set(self, key, value, *subkeys) -> None:
        self.calls.append(("config:system:set", key, *subkeys, repr(value)))

    def app_set(self, app, key, value, *, secret=False) -> None:
        self.calls.append(("config:app:set", app, key, value))

    def apps(self) -> tuple[set[str], set[str]]:
        return set(self.enabled), set(self.disabled)

    def wait_installed(self, **_kwargs) -> dict:
        return {"installed": True}

    def commands(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def fake_occ() -> FakeOcc:
    return FakeOcc()
