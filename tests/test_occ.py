from __future__ import annotations

import json
import subprocess

import pytest

from scripts.occ import Occ, OccError


def _completed(command: list[str], stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, code, stdout, "")


def _scripted(responses: list[tuple[str, str, int]]):
    """Match each docker-exec command to the next unused reply with that argument."""
    calls: list[list[str]] = []
    remaining = list(responses)

    def runner(command, **_kwargs):
        calls.append(command)
        for index, (needle, stdout, code) in enumerate(remaining):
            if needle in command:
                remaining.pop(index)
                return _completed(command, stdout, code)
        return _completed(command, "", 1)

    return runner, calls


def _status(needs_upgrade: bool, *, code: int = 0) -> tuple[str, str, int]:
    payload = {
        "installed": True,
        "maintenance": False,
        "needsDbUpgrade": needs_upgrade,
        "version": "34.0.5.1",
    }
    return "status", json.dumps(payload), code


def test_wait_installed_returns_when_nextcloud_is_already_ready():
    runner, calls = _scripted([_status(False)])
    result = Occ(runner=runner).wait_installed(timeout=1, interval=0, sleep=lambda _seconds: None)
    assert result["installed"] is True
    assert not any("upgrade" in command for command in calls)


def test_wait_installed_upgrades_when_apache_is_up_and_the_database_is_behind():
    runner, calls = _scripted([
        _status(True),
        ("/proc/1/cmdline", "apache2\x00-D\x00FOREGROUND", 0),
        ("upgrade", "Update successful\n", 0),
        _status(False),
    ])
    result = Occ(runner=runner).wait_installed(timeout=1, interval=0, sleep=lambda _seconds: None)
    assert result["needsDbUpgrade"] is False
    assert any(command[-1] == "upgrade" for command in calls)


def test_wait_installed_leaves_the_entrypoint_to_finish_its_own_upgrade():
    slept = {"calls": 0}

    def sleep(_seconds: float) -> None:
        slept["calls"] += 1
        if slept["calls"] > 1:
            raise AssertionError("kept waiting after the entrypoint finished")

    pending = {"value": True}

    def runner(command, **_kwargs):
        if "status" in command:
            return _completed(command, json.dumps({
                "installed": True, "maintenance": False, "needsDbUpgrade": pending["value"],
            }))
        if "/proc/1/cmdline" in command:
            pending["value"] = False
            return _completed(command, "/entrypoint.sh\x00apache2-foreground")
        if "upgrade" in command:
            raise AssertionError("occ upgrade must wait until the entrypoint has exited")
        return _completed(command, "", 1)

    result = Occ(runner=runner).wait_installed(timeout=5, interval=0, sleep=sleep)
    assert result["needsDbUpgrade"] is False
    assert slept["calls"] == 1


def test_wait_installed_reports_an_upgrade_that_does_not_clear_the_flag():
    runner, _calls = _scripted([
        _status(True),
        ("/proc/1/cmdline", "apache2-foreground", 0),
        ("upgrade", "", 0),
        _status(True),
    ])
    with pytest.raises(RuntimeError, match="still needs a database upgrade"):
        Occ(runner=runner).wait_installed(timeout=5, interval=0, sleep=lambda _seconds: None)


def test_wait_installed_surfaces_a_failed_upgrade():
    runner, _calls = _scripted([
        _status(True, code=1),
        ("/proc/1/cmdline", "apache2-foreground", 0),
        ("upgrade", "Update failed\n", 1),
    ])
    with pytest.raises(OccError, match="upgrade"):
        Occ(runner=runner).wait_installed(timeout=5, interval=0, sleep=lambda _seconds: None)
