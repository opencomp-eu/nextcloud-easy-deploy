"""Sign-in creates the Stalwart mailbox before Mail can lock the account out."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.mailbox_sync import install_mailbox_sync, render_path, render_service


class _Occ:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        self.calls.append(args)
        return subprocess.CompletedProcess(list(args), 0, "", "")


def test_service_creates_mailboxes_then_acks_the_sign_in(tmp_path: Path):
    stalwart = tmp_path / "stalwart-easy-deploy"
    stalwart.mkdir()
    sync_dir = tmp_path / "data" / "easydeploy-mail-sync"
    text = render_service(stalwart, sync_dir)
    assert "--mailboxes-only --wait" in text
    assert f"--ack-mailbox-sync {sync_dir}" in text
    assert "User=root" in text
    assert f"PathExistsGlob={sync_dir}/requests/*" in render_path(sync_dir)


def test_install_wires_the_unit_and_enables_the_app_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "nextcloud-easy-deploy"
    stalwart = tmp_path / "stalwart-easy-deploy"
    (stalwart / "scripts").mkdir(parents=True)
    (stalwart / "scripts" / "groupware.py").write_text("")
    project.mkdir()
    unit_dir = tmp_path / "units"
    seen: list[list[str]] = []

    def run_command(command, check=False, **kwargs):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("EASYDEPLOY_ASSUME_SYSTEMD", "1")
    occ = _Occ()
    notes = install_mailbox_sync(
        {"nextcloud": {"data_dir": str(tmp_path / "ncdata")}},
        occ,
        project_root=project,
        unit_dir=unit_dir,
        run_command=run_command,
    )
    assert notes[0].startswith("Mailbox sync:")
    service = (unit_dir / "nextcloud-easy-deploy-mailbox-sync.service").read_text()
    assert "--mailboxes-only" in service
    assert (unit_dir / "nextcloud-easy-deploy-mailbox-sync.path").is_file()
    assert ["docker", "exec", "-u", "www-data", "nextcloud", "mkdir", "-p"] == seen[0][:7]
    assert ["systemctl", "enable", "--now", "nextcloud-easy-deploy-mailbox-sync.path"] in seen
    assert occ.calls == [("config:app:set", "easydeploy_mail", "mailbox_sync", "--value=yes")]


def test_install_turns_the_flag_off_without_stalwart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EASYDEPLOY_ASSUME_SYSTEMD", "1")
    occ = _Occ()
    notes = install_mailbox_sync(
        {"nextcloud": {"data_dir": str(tmp_path / "ncdata")}},
        occ,
        project_root=tmp_path / "nextcloud-easy-deploy",
        unit_dir=tmp_path / "units",
        run_command=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    assert "not installed" in notes[0]
    assert occ.calls == [("config:app:set", "easydeploy_mail", "mailbox_sync", "--value=no")]
