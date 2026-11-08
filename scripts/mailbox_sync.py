"""Create a Stalwart mailbox before Nextcloud Mail's first IMAP login.

Mail gives up for three hours after three authentication failures. A person
added in the Kanidm admin UI only received a mailbox on the five-minute sync,
so signing in during that gap locked Mail. Sign-in drops a file under the
Nextcloud data directory; this unit runs the mailbox sync and then moves the
file to done/, which is what the sign-in request is waiting for.
"""

from __future__ import annotations

import os
import pwd
import shlex
import shutil
import subprocess
from pathlib import Path

UNIT_NAME = "nextcloud-easy-deploy-mailbox-sync"
APP_CONFIG_KEY = "mailbox_sync"


def stalwart_kit_root(project_root: Path) -> Path | None:
    root = project_root.parent / "stalwart-easy-deploy"
    if (root / "scripts" / "groupware.py").is_file():
        return root
    return None


def sync_directory(data_dir: Path) -> Path:
    return data_dir / "easydeploy-mail-sync"


def _python(stalwart_root: Path) -> str:
    venv = stalwart_root / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return shutil.which("python3") or "python3"


def _account(stalwart_root: Path) -> str:
    try:
        return pwd.getpwuid(stalwart_root.resolve().stat().st_uid).pw_name
    except KeyError:
        return "root"


def _as_account(account: str, command: str) -> str:
    """Run command as the kit owner. Root keeps the acknowledgement step."""
    runuser = shutil.which("runuser")
    if runuser:
        return f"{runuser} -u {account} -- {command}"
    su = shutil.which("su") or "/bin/su"
    return f"{su} -s /bin/bash {account} -c {shlex.quote(command)}"


def render_service(stalwart_root: Path, sync_dir: Path) -> str:
    python = _python(stalwart_root)
    account = _account(stalwart_root)
    module = f"{python} -m scripts.groupware"
    sync = _as_account(account, f"cd {shlex.quote(str(stalwart_root))} && {module} --mailboxes-only --wait")
    return "\n".join(
        [
            "[Unit]",
            "Description=Create Stalwart mailboxes when someone signs in to Nextcloud",
            "Wants=network-online.target docker.service",
            "After=network-online.target docker.service",
            "",
            "[Service]",
            "Type=oneshot",
            "User=root",
            "Group=root",
            f"WorkingDirectory={stalwart_root}",
            f"ExecStart={sync}",
            f"ExecStartPost={module} --ack-mailbox-sync {sync_dir}",
            "",
        ]
    )


def render_path(sync_dir: Path, unit_name: str = UNIT_NAME) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Watch for Nextcloud sign-ins that need a Stalwart mailbox",
            "",
            "[Path]",
            f"PathExistsGlob={sync_dir}/requests/*",
            f"Unit={unit_name}.service",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def _systemd_available() -> bool:
    if os.environ.get("EASYDEPLOY_ASSUME_SYSTEMD") == "1":
        return True
    return Path("/run/systemd/system").exists()


def _privileged(unit_dir: Path) -> bool:
    if os.geteuid() == 0:
        return False
    if unit_dir.exists():
        return not os.access(unit_dir, os.W_OK)
    return not os.access(unit_dir.parent, os.W_OK)


def _run(run_command, args: list[str], *, privileged: bool, check: bool) -> None:
    command = ["systemctl", *args]
    if privileged:
        if not shutil.which("sudo"):
            raise RuntimeError("Installing the mailbox sync requires sudo")
        command.insert(0, "sudo")
    run_command(command, check=check)


def _write(path: Path, content: str, *, privileged: bool) -> None:
    if not privileged:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return
    import tempfile

    with tempfile.NamedTemporaryFile("w", delete=False) as temporary:
        temporary.write(content)
        source = Path(temporary.name)
    try:
        subprocess.run(["sudo", "install", "-m", "0644", str(source), str(path)], check=True)
    finally:
        source.unlink(missing_ok=True)


def _remove(path: Path, *, privileged: bool) -> None:
    if not path.exists():
        return
    if privileged:
        subprocess.run(["sudo", "rm", "-f", str(path)], check=True)
        return
    path.unlink()


def _set_flag(occ, enabled: bool) -> None:
    occ.run(
        "config:app:set",
        "easydeploy_mail",
        APP_CONFIG_KEY,
        "--value=yes" if enabled else "--value=no",
    )


def _ensure_queue(run_command) -> None:
    """www-data owns the data directory, so create the queue inside the container."""
    run_command(
        [
            "docker",
            "exec",
            "-u",
            "www-data",
            "nextcloud",
            "mkdir",
            "-p",
            "/var/www/html/data/easydeploy-mail-sync/requests",
            "/var/www/html/data/easydeploy-mail-sync/done",
        ],
        check=True,
    )


def install_mailbox_sync(
    config: dict,
    occ,
    *,
    project_root: Path | None = None,
    unit_dir: Path | None = None,
    run_command=subprocess.run,
) -> list[str]:
    """Install the sign-in mailbox sync, or turn it off when Stalwart is absent."""
    from scripts.apply import data_paths

    root = project_root or Path(__file__).resolve().parent.parent
    unit_dir = unit_dir or Path(os.environ.get("EASYDEPLOY_SYSTEMD_UNIT_DIR", "/etc/systemd/system"))
    stalwart = stalwart_kit_root(root)
    service_path = unit_dir / f"{UNIT_NAME}.service"
    path_path = unit_dir / f"{UNIT_NAME}.path"
    if stalwart is None:
        _disable(occ, service_path, path_path, unit_dir=unit_dir, run_command=run_command)
        return ["Mailbox sync: Stalwart is not installed beside Nextcloud, so sign-in does not wait for one."]

    if not _systemd_available():
        return ["Warning: mailbox sync needs systemd, so a new mailbox can still lag the first sign-in."]

    sync_dir = sync_directory(data_paths(config)["data"])
    try:
        _ensure_queue(run_command)
        privileged = _privileged(unit_dir)
        _write(service_path, render_service(stalwart, sync_dir), privileged=privileged)
        _write(path_path, render_path(sync_dir), privileged=privileged)
        _run(run_command, ["daemon-reload"], privileged=privileged, check=True)
        _run(run_command, ["enable", "--now", f"{UNIT_NAME}.path"], privileged=privileged, check=True)
        _set_flag(occ, True)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        return [f"Warning: mailbox sync was not installed ({exc}). A new mailbox can still lag the first sign-in."]
    return ["Mailbox sync: a Stalwart mailbox is created when its owner signs in, before Mail connects."]


def _disable(occ, service_path: Path, path_path: Path, *, unit_dir: Path, run_command) -> None:
    if not _systemd_available():
        return
    if not service_path.exists() and not path_path.exists():
        try:
            _set_flag(occ, False)
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            pass
        return
    try:
        privileged = _privileged(unit_dir)
        _run(run_command, ["disable", "--now", path_path.name], privileged=privileged, check=False)
        _remove(path_path, privileged=privileged)
        _remove(service_path, privileged=privileged)
        _run(run_command, ["daemon-reload"], privileged=privileged, check=True)
        _set_flag(occ, False)
    except (OSError, subprocess.CalledProcessError, RuntimeError):
        return
