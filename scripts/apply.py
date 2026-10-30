#!/usr/bin/env python3
"""Render configuration from deploy.yaml, start the stack, and configure Nextcloud."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "easydeploy-lib" / "python"))
import backup_config  # noqa: E402
import hostfs  # noqa: E402
import edlog  # noqa: E402

from scripts import configure, integration  # noqa: E402
from scripts.occ import Occ  # noqa: E402

COMPOSE_DIR = PROJECT_ROOT / "compose"
COMPOSE_PROJECT_NAME = "nextcloud-easy-deploy"
STATE_DIR = PROJECT_ROOT / ".nextcloud-easy-deploy"
SECRETS_PATH = STATE_DIR / "secrets.yaml"
COMPOSE_ENV_PATH = STATE_DIR / "compose.env"
DEPLOY_PATH = PROJECT_ROOT / "deploy.yaml"
CADDY_TEMPLATE = PROJECT_ROOT / "caddy" / "Caddyfile.template"
CADDYFILE = PROJECT_ROOT / "caddy" / "Caddyfile"
INTEGRATION_DIR = STATE_DIR / "integration"
INTEGRATION_CADDY_FRAGMENT = INTEGRATION_DIR / "caddy.caddy"
RUNTIME_OVERLAY_PATH = STATE_DIR / "compose" / "runtime.yml"
# start.sh, stop.sh and backup hooks read this so they use the same files as apply.
COMPOSE_FILES_LIST = STATE_DIR / "compose" / "files.txt"
DEFAULT_INTEGRATE_NETWORK = "easydeploy-net"
KIT_NETWORK = "nextcloud-net"

WWW_DATA_UID = 33
# Docker's default address pools: Caddy reaches Nextcloud from one of these.
TRUSTED_PROXIES = ("172.16.0.0/12", "192.168.0.0/16", "10.0.0.0/8")
DEFAULT_APPS = ("calendar", "contacts", "deck", "mail", "spreed", "richdocuments")
OFFICE_APP = "richdocuments"
TALK_APP = "spreed"

PLACEHOLDER_DOMAINS = frozenset({"cloud.example.com", "office.example.com", "example.com"})

SECRET_KEYS = (
    "POSTGRES_ADMIN_PASSWORD",
    "NEXTCLOUD_DB_PASSWORD",
    "REDIS_PASSWORD",
    "NEXTCLOUD_ADMIN_PASSWORD",
    "COLLABORA_ADMIN_PASSWORD",
    "TURN_SECRET",
    "SIGNALING_SECRET",
    "INTERNAL_SECRET",
)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_yaml(path: Path) -> dict:
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False)


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    if "{{" in rendered:
        missing = sorted({part.split("}")[0] for part in rendered.split("{{")[1:]})
        raise ValueError(f"Unresolved template placeholders: {', '.join(missing)}")
    return rendered


def load_config(path: Path = DEPLOY_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path.name}. Copy deploy.yaml.example to deploy.yaml or run wizard.sh."
        )
    return load_yaml(path)


def section(config: dict, name: str) -> dict:
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def proxy_mode(config: dict) -> str:
    mode = str(section(config, "proxy").get("mode") or "standalone").strip().lower()
    if mode not in {"standalone", "integrate"}:
        raise ValueError("proxy.mode must be 'standalone' or 'integrate'")
    return mode


def nextcloud_domain(config: dict) -> str:
    return str(section(config, "nextcloud").get("domain") or "").strip()


def office_enabled(config: dict) -> bool:
    return to_bool(section(config, "office").get("enabled", True))


def office_domain(config: dict) -> str:
    return str(section(config, "office").get("domain") or "").strip()


def talk_enabled(config: dict) -> bool:
    return to_bool(section(config, "talk").get("enabled", True))


def turn_port(config: dict) -> int:
    return int(section(config, "talk").get("turn_port") or 3478)


def admin_user(config: dict) -> str:
    return str(section(config, "nextcloud").get("admin_user") or "admin").strip() or "admin"


def backup_enabled(config: dict) -> bool:
    return to_bool(section(config, "backup").get("enabled", False))


def backup_secret_keys(config: dict) -> tuple[str, ...]:
    """Secrets generated for the shared backup pipeline when it is enabled."""
    return ("BORG_PASSPHRASE",) if backup_enabled(config) else ()


def validate_backup_config(path: Path | None = None) -> None:
    path = path or DEPLOY_PATH
    if not path.exists():
        return
    backup_config.load_backup_settings(path)


def image(config: dict, name: str, default_image: str, default_tag: str) -> str:
    block = section(config, name)
    return f"{block.get('image') or default_image}:{block.get('tag') or default_tag}"


def validate_config(config: dict) -> None:
    domain = nextcloud_domain(config)
    if not domain:
        raise ValueError("nextcloud.domain is required")
    if not str(section(config, "nextcloud").get("data_dir") or "").strip():
        raise ValueError("nextcloud.data_dir is required")
    if office_enabled(config):
        office = office_domain(config)
        if not office:
            raise ValueError("office.domain is required when office.enabled is true")
        if office == domain:
            raise ValueError("office.domain must differ from nextcloud.domain")
    if talk_enabled(config) and not 1 <= turn_port(config) <= 65535:
        raise ValueError("talk.turn_port must be a port number")
    proxy_mode(config)
    placeholders = sorted({domain, office_domain(config)} & PLACEHOLDER_DOMAINS)
    if placeholders:
        print(
            f"Warning: {', '.join(placeholders)} is a placeholder; certificates will not be issued.",
            file=sys.stderr,
        )


def wanted_apps(config: dict, *, oidc: bool, mail: bool) -> list[str]:
    configured = section(config, "nextcloud").get("apps")
    apps = [str(a).strip() for a in (configured if isinstance(configured, list) else DEFAULT_APPS)]
    apps = [a for a in apps if a]
    if not office_enabled(config):
        apps = [a for a in apps if a != OFFICE_APP]
    if mail and "mail" not in apps:
        apps.append("mail")
    if oidc:
        apps.append("user_oidc")
    return list(dict.fromkeys(apps))


def derive_compose_files(config: dict) -> list[str]:
    files = ["docker-compose.yml"]
    if office_enabled(config):
        files.append("office.yml")
    if talk_enabled(config):
        files.append("talk.yml")
    if proxy_mode(config) == "integrate":
        files.append("integrate.yml")
        if office_enabled(config):
            files.append("integrate-office.yml")
        if talk_enabled(config):
            files.append("integrate-talk.yml")
    else:
        files.append("caddy.yml")
    return files


def compose_file_paths(config: dict) -> list[Path]:
    paths = [COMPOSE_DIR / name for name in derive_compose_files(config)]
    if RUNTIME_OVERLAY_PATH.is_file():
        paths.append(RUNTIME_OVERLAY_PATH)
    return paths


def random_secret() -> str:
    # Hex keeps values safe in compose env files, Redis arguments and URLs.
    return secrets.token_hex(24)


def load_or_create_secrets(config: dict | None = None) -> dict:
    data = load_yaml(SECRETS_PATH) if SECRETS_PATH.is_file() else {}
    keys = list(SECRET_KEYS)
    if config is not None:
        keys.extend(backup_secret_keys(config))
    for key in keys:
        if not str(data.get(key) or "").strip():
            data[key] = random_secret()
    save_yaml(SECRETS_PATH, data)
    SECRETS_PATH.chmod(0o600)
    return data


def data_paths(config: dict) -> dict[str, Path]:
    root = Path(str(section(config, "nextcloud")["data_dir"])).expanduser()
    return {"root": root, "html": root / "html", "data": root / "data", "postgres": root / "postgres"}


def chown_path(path: Path, uid: int, gid: int) -> None:
    try:
        hostfs.chown_path(path, uid, gid)
    except OSError:
        pass


def ensure_data_dirs(config: dict) -> None:
    hostfs.ensure_writable_directory(str(data_paths(config)["root"]))
    paths = data_paths(config)
    for name in ("html", "data", "postgres"):
        paths[name].mkdir(parents=True, exist_ok=True)
    for name in ("html", "data"):
        chown_path(paths[name], WWW_DATA_UID, WWW_DATA_UID)
    try:
        paths["data"].chmod(0o770)
    except OSError:
        pass


def ensure_audit_log(spool: integration.AuditSpool | None) -> None:
    """Nextcloud (www-data) appends to the spool file the engine (root) drains."""
    if spool is None:
        return
    try:
        spool.spool_dir.mkdir(parents=True, exist_ok=True)
        spool.log_path.touch(exist_ok=True)
        spool.log_path.chmod(0o666)
    except OSError as exc:
        print(f"Warning: cannot prepare audit log {spool.log_path}: {exc}", file=sys.stderr)


def pinned_hosts(config: dict, *, oidc: integration.OidcProvider | None,
                 mail: integration.MailProvisioning | None) -> dict[str, list[str]]:
    """Public names each container must reach through Caddy (or Stalwart) on this host."""
    domain = nextcloud_domain(config)
    nextcloud = [domain]
    if office_enabled(config):
        nextcloud.append(office_domain(config))
    if oidc is not None:
        nextcloud.append(oidc.domain)
    if mail is not None:
        nextcloud.append(mail.mail_host)
    hosts = {
        "nextcloud": list(dict.fromkeys(nextcloud)),
        "nextcloud_cron": list(dict.fromkeys(nextcloud)),
    }
    if office_enabled(config):
        hosts["nextcloud_collabora"] = [domain]
    if talk_enabled(config):
        hosts["nextcloud_talk"] = [domain]
    return hosts


def runtime_overlay(config: dict, *, oidc: integration.OidcProvider | None,
                    mail: integration.MailProvisioning | None,
                    spool: integration.AuditSpool | None) -> dict:
    services: dict[str, dict[str, Any]] = {}
    for service, hosts in pinned_hosts(config, oidc=oidc, mail=mail).items():
        services[service] = {"extra_hosts": [f"{host}:host-gateway" for host in hosts if host]}
    if spool is not None:
        mount = f"{spool.spool_dir}:{configure.AUDIT_CONTAINER_DIR}"
        for service in ("nextcloud", "nextcloud_cron"):
            services[service]["volumes"] = [mount]
    return {"services": services}


def render_runtime_overlay(config: dict, **integrations: Any) -> None:
    RUNTIME_OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUNTIME_OVERLAY_PATH.open("w") as handle:
        yaml.safe_dump(runtime_overlay(config, **integrations), handle, default_flow_style=False)


def nextcloud_caddy_block(config: dict) -> str:
    domain = nextcloud_domain(config)
    talk = ""
    if talk_enabled(config):
        talk = (
            f"\thandle_path {configure.SIGNALING_PATH}* {{\n"
            "\t\treverse_proxy nextcloud_talk:8081\n"
            "\t}\n"
        )
    return (
        f"{domain} {{\n"
        "\tencode zstd gzip\n"
        '\theader Strict-Transport-Security "max-age=31536000; includeSubDomains"\n'
        "\tredir /.well-known/carddav /remote.php/dav/ 301\n"
        "\tredir /.well-known/caldav /remote.php/dav/ 301\n"
        f"{talk}"
        "\thandle {\n"
        "\t\treverse_proxy nextcloud:80\n"
        "\t}\n"
        "}"
    )


def office_caddy_block(config: dict) -> str:
    return (
        f"{office_domain(config)} {{\n"
        "\treverse_proxy nextcloud_collabora:9980\n"
        "}"
    )


def site_blocks(config: dict) -> str:
    blocks = [nextcloud_caddy_block(config)]
    if office_enabled(config):
        blocks.append(office_caddy_block(config))
    return "\n\n".join(blocks)


def render_caddyfile(config: dict) -> None:
    rendered = render_template(CADDY_TEMPLATE.read_text(), {"SITE_BLOCKS": site_blocks(config)})
    CADDYFILE.parent.mkdir(parents=True, exist_ok=True)
    CADDYFILE.write_text(rendered + "\n")


def render_integration_fragment(config: dict) -> None:
    INTEGRATION_DIR.mkdir(parents=True, exist_ok=True)
    INTEGRATION_CADDY_FRAGMENT.write_text(site_blocks(config) + "\n")


def compose_env_lines(config: dict, secrets: dict) -> list[str]:
    nextcloud = section(config, "nextcloud")
    paths = data_paths(config)
    lines = [
        f"NEXTCLOUD_IMAGE={image(config, 'nextcloud', 'docker.io/library/nextcloud', '34.0.5-apache')}",
        f"POSTGRES_IMAGE={image(config, 'postgres', 'docker.io/library/postgres', '16-alpine')}",
        f"REDIS_IMAGE={image(config, 'redis', 'docker.io/library/redis', '7.4.12-alpine')}",
        f"NEXTCLOUD_DOMAIN={nextcloud_domain(config)}",
        f"NEXTCLOUD_HTML_DIR={paths['html']}",
        f"NEXTCLOUD_DATA_DIR={paths['data']}",
        f"NEXTCLOUD_POSTGRES_DIR={paths['postgres']}",
        f"NEXTCLOUD_ADMIN_USER={admin_user(config)}",
        f"NEXTCLOUD_ADMIN_PASSWORD={secrets['NEXTCLOUD_ADMIN_PASSWORD']}",
        f"NEXTCLOUD_DB_PASSWORD={secrets['NEXTCLOUD_DB_PASSWORD']}",
        f"POSTGRES_ADMIN_PASSWORD={secrets['POSTGRES_ADMIN_PASSWORD']}",
        f"REDIS_PASSWORD={secrets['REDIS_PASSWORD']}",
        f"TRUSTED_PROXIES={' '.join(TRUSTED_PROXIES)}",
        f"PHP_MEMORY_LIMIT={nextcloud.get('php_memory_limit') or '1G'}",
        f"PHP_UPLOAD_LIMIT={nextcloud.get('php_upload_limit') or '16G'}",
    ]
    if office_enabled(config):
        lines += [
            f"COLLABORA_IMAGE={image(config, 'office', 'docker.io/collabora/code', '26.04.5.2.1')}",
            f"OFFICE_DOMAIN={office_domain(config)}",
            f"COLLABORA_ADMIN_PASSWORD={secrets['COLLABORA_ADMIN_PASSWORD']}",
        ]
    if talk_enabled(config):
        lines += [
            f"TALK_IMAGE={image(config, 'talk', 'ghcr.io/nextcloud-releases/aio-talk', '20261021_180331')}",
            f"TALK_TURN_PORT={turn_port(config)}",
            f"TURN_SECRET={secrets['TURN_SECRET']}",
            f"SIGNALING_SECRET={secrets['SIGNALING_SECRET']}",
            f"INTERNAL_SECRET={secrets['INTERNAL_SECRET']}",
        ]
    if proxy_mode(config) == "standalone":
        lines.append(f"NED_CADDYFILE={CADDYFILE.resolve()}")
    return lines


def write_compose_env(config: dict, secrets: dict) -> None:
    COMPOSE_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPOSE_ENV_PATH.write_text("\n".join(compose_env_lines(config, secrets)) + "\n")
    COMPOSE_ENV_PATH.chmod(0o600)


def read_integrations(config: dict) -> dict[str, Any]:
    return {
        "oidc": integration.oidc_provider(config),
        "mail": integration.mail_provisioning(config),
        "spool": integration.audit_spool(),
    }


def render_runtime_artifacts(config: dict, secrets: dict, integrations: dict[str, Any]) -> None:
    ensure_data_dirs(config)
    ensure_audit_log(integrations["spool"])
    render_runtime_overlay(config, **integrations)
    if proxy_mode(config) == "integrate":
        render_integration_fragment(config)
    else:
        render_caddyfile(config)
    write_compose_env(config, secrets)
    COMPOSE_FILES_LIST.write_text(
        "".join(f"{path.resolve()}\n" for path in compose_file_paths(config))
    )


def docker_compose_cmd() -> list[str]:
    if shutil.which("docker"):
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        if result.returncode == 0:
            return ["docker", "compose"]
    compose = shutil.which("docker-compose")
    if compose:
        return [compose]
    raise RuntimeError("Docker Compose v2 is required (docker compose)")


def compose_env() -> dict[str, str]:
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = COMPOSE_PROJECT_NAME
    if COMPOSE_ENV_PATH.is_file():
        for line in COMPOSE_ENV_PATH.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def run_compose(*args: str) -> None:
    cmd = docker_compose_cmd()
    for compose_file in compose_file_paths(load_config()):
        cmd.extend(["-f", str(compose_file)])
    cmd.extend(args)
    try:
        subprocess.run(cmd, cwd=COMPOSE_DIR, check=True, env=compose_env())
    except subprocess.CalledProcessError:
        if subprocess.run(["docker", "inspect", "nextcloud"], capture_output=True).returncode == 0:
            print("\n--- nextcloud container logs (last 80 lines) ---", file=sys.stderr)
            subprocess.run(["docker", "logs", "nextcloud", "--tail", "80"], check=False)
        raise


def ensure_docker_network(name: str) -> None:
    if subprocess.run(["docker", "network", "inspect", name], capture_output=True).returncode != 0:
        subprocess.run(["docker", "network", "create", name], check=True)


def stop_standalone_caddy() -> None:
    if subprocess.run(["docker", "inspect", "nextcloud_caddy"], capture_output=True).returncode == 0:
        print("Stopping standalone nextcloud_caddy (integrate mode uses easydeploy-engine Caddy)…")
        subprocess.run(["docker", "stop", "nextcloud_caddy"], check=False)
        subprocess.run(["docker", "rm", "nextcloud_caddy"], check=False)


def reconcile_runtime(config: dict, *, skip_pull: bool = False) -> None:
    ensure_docker_network(KIT_NETWORK)
    if proxy_mode(config) == "integrate":
        ensure_docker_network(DEFAULT_INTEGRATE_NETWORK)
        stop_standalone_caddy()
    if not skip_pull:
        print("Pulling Nextcloud stack images…")
        run_compose("pull")
    print("Starting Nextcloud stack…")
    try:
        run_compose("up", "-d", "--wait", "--remove-orphans")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Docker Compose failed while starting the Nextcloud stack.") from exc


def configure_nextcloud(config: dict, secrets: dict, integrations: dict[str, Any],
                        occ: Occ | None = None) -> list[str]:
    occ = occ or Occ()
    print("Waiting for Nextcloud to finish installing or upgrading…")
    occ.wait_installed()
    domain = nextcloud_domain(config)
    nextcloud = section(config, "nextcloud")
    oidc, mail, spool = integrations["oidc"], integrations["mail"], integrations["spool"]

    notes = configure.configure_site(occ, configure.SiteSettings(
        domain=domain,
        trusted_proxies=TRUSTED_PROXIES,
        default_phone_region=str(nextcloud.get("default_phone_region") or "").strip(),
    ))
    notes += configure.ensure_apps(
        occ,
        wanted_apps(config, oidc=oidc is not None, mail=mail is not None),
        update=to_bool(nextcloud.get("update_apps", True)),
        disable=() if office_enabled(config) else (OFFICE_APP,),
    )
    enabled, _ = occ.apps()
    if oidc is not None and "user_oidc" in enabled:
        notes += configure.configure_oidc(occ, oidc)
    if OFFICE_APP in enabled:
        notes += configure.configure_office(occ, domain, enabled=office_enabled(config))
    if talk_enabled(config) and TALK_APP in enabled:
        notes += configure.configure_talk(occ, configure.TalkSettings(
            domain=domain,
            turn_port=turn_port(config),
            turn_secret=secrets["TURN_SECRET"],
            signaling_secret=secrets["SIGNALING_SECRET"],
        ))
    if mail is not None and "mail" in enabled:
        notes += configure.configure_mail(occ, mail)
    notes += configure.configure_audit(occ, spool)
    return notes


def print_summary(config: dict, notes: list[str]) -> None:
    domain = nextcloud_domain(config)
    print()
    print("=== Deployment summary ===")
    print(f"  Nextcloud:     https://{domain}")
    if office_enabled(config):
        print(f"  Office:        https://{office_domain(config)} (Collabora)")
    if talk_enabled(config):
        port = turn_port(config)
        print(f"  Talk:          signaling at https://{domain}{configure.SIGNALING_PATH}, "
              f"TURN on port {port} (open TCP and UDP {port} in the firewall)")
    print(f"  Local admin:   {admin_user(config)} (password: {SECRETS_PATH} NEXTCLOUD_ADMIN_PASSWORD)")
    print(f"                 sign in at https://{domain}/login?direct=1 when Kanidm sign-in is on")
    print(f"  Proxy:         {proxy_mode(config)}")
    print(f"  Data:          {data_paths(config)['root']}")
    for note in notes:
        print(f"  {note}")


def reconcile_backup_schedule() -> None:
    """Reconcile the timer through backup.sh, which uses the emitted plan name."""
    try:
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "backup.sh"), "--schedule"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        print(f"Backup schedule: {detail.strip()}", file=sys.stderr)
        return
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    print(f"Backup schedule: {result.stdout.strip() or 'Automatic backup timer reconciled.'}")


def apply_configuration(*, skip_runtime: bool = False, skip_pull: bool = False) -> None:
    config = load_config()
    validate_config(config)
    validate_backup_config(DEPLOY_PATH)
    secrets = load_or_create_secrets(config)
    integrations = read_integrations(config)
    render_runtime_artifacts(config, secrets, integrations)
    notes: list[str] = []
    if not skip_runtime:
        reconcile_runtime(config, skip_pull=skip_pull)
        notes = configure_nextcloud(config, secrets, integrations)
        from scripts.update import record_current_lock

        record_current_lock()
    if not edlog.is_quiet():
        print_summary(config, notes)
    else:
        for note in notes:
            if note.startswith("Warning:"):
                print(note, file=sys.stderr)
    reconcile_backup_schedule()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply nextcloud-easy-deploy configuration")
    parser.add_argument("--skip-runtime", action="store_true",
                        help="Render configs and secrets only; do not run docker compose")
    parser.add_argument("--skip-pull", action="store_true",
                        help="Skip docker compose pull before up")
    args = parser.parse_args()
    try:
        apply_configuration(skip_runtime=args.skip_runtime, skip_pull=args.skip_pull)
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
