"""Configure a running Nextcloud through occ. Every step is idempotent.

Each step takes an Occ runner plus plain settings and returns notes for the
apply summary, so steps can be tested with a fake runner and reordered or
dropped without touching the others.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from scripts.integration import (
    AuditSpool,
    MailProvisioning,
    MissingSecret,
    OidcProvider,
)
from scripts.occ import Occ, OccError

AUDIT_CONTAINER_DIR = "/var/lib/easydeploy-audit"
AUDIT_FILENAME = "nextcloud.jsonl"
COLLABORA_INTERNAL_URL = "http://nextcloud_collabora:9980"
SIGNALING_PATH = "/standalone-signaling/"
TURN_SCHEMES = "turn"
TURN_PROTOCOLS = "udp,tcp"


@dataclass(frozen=True)
class SiteSettings:
    domain: str
    trusted_proxies: tuple[str, ...]
    default_phone_region: str = ""
    maintenance_window_start: int = 1


@dataclass(frozen=True)
class TalkSettings:
    domain: str
    turn_port: int
    turn_secret: str
    signaling_secret: str

    @property
    def signaling_url(self) -> str:
        return f"wss://{self.domain}{SIGNALING_PATH}"

    @property
    def turn_server(self) -> str:
        return f"{self.domain}:{self.turn_port}"


def configure_site(occ: Occ, site: SiteSettings) -> list[str]:
    occ.system_set("trusted_domains", site.domain, "0")
    for index, cidr in enumerate(site.trusted_proxies):
        occ.system_set("trusted_proxies", cidr, str(index))
    occ.system_set("overwrite.cli.url", f"https://{site.domain}")
    occ.system_set("overwriteprotocol", "https")
    # Kanidm discovery, Collabora WOPI and the Talk backend are reached on
    # host-pinned or Docker-network addresses.
    occ.system_set("allow_local_remote_servers", True)
    occ.system_set("maintenance_window_start", site.maintenance_window_start)
    if site.default_phone_region:
        occ.system_set("default_phone_region", site.default_phone_region)
    occ.run("background:cron")
    for command in ("db:add-missing-indices", "db:add-missing-columns", "db:add-missing-primary-keys"):
        occ.run(command, check=False)
    return []


def ensure_apps(occ: Occ, wanted: list[str], *, update: bool,
                disable: tuple[str, ...] = ()) -> list[str]:
    """Enable (installing if needed) the wanted apps; warn about ones that fail."""
    notes: list[str] = []
    enabled, disabled = occ.apps()
    for app in wanted:
        if app in enabled:
            continue
        if app in disabled:
            occ.run("app:enable", app)
        else:
            result = occ.run("app:install", app, check=False)
            if result.returncode != 0:
                notes.append(f"Warning: could not install the Nextcloud app {app!r} "
                             "(app store unreachable or not compatible yet).")
                continue
        enabled.add(app)
    for app in disable:
        if app in enabled:
            occ.run("app:disable", app)
    if update:
        occ.run("app:update", "--all", check=False)
    return notes


def configure_oidc(occ: Occ, provider: OidcProvider | None) -> list[str]:
    if provider is None:
        return []
    try:
        secret = provider.secret()
    except MissingSecret as exc:
        return [
            f"Warning: Kanidm sign-in not configured yet ({exc}). "
            "Apply the Kanidm kit, then re-run this apply."
        ]
    mapping = provider.mapping
    args = [
        "user_oidc:provider", provider.name,
        f"--clientid={provider.client_id}",
        f"--clientsecret={secret}",
        f"--discoveryuri={provider.discovery_url}",
        f"--scope={provider.scopes}",
        # Accounts keep the Kanidm username as their Nextcloud user id.
        "--unique-uid=0",
        "--send-id-token-hint=1",
        f"--mapping-uid={mapping.get('uid', 'preferred_username')}",
        f"--mapping-display-name={mapping.get('display_name', 'name')}",
        f"--mapping-email={mapping.get('email', 'email')}",
    ]
    if mapping.get("groups"):
        args += [
            f"--mapping-groups={mapping['groups']}",
            "--group-provisioning=1",
            "--group-restrict-login-to-whitelist=0",
        ]
        if provider.group_whitelist_regex:
            args.append(f"--group-whitelist-regex={provider.group_whitelist_regex}")
    occ.run_secret(*args)
    # Send people straight to Kanidm. The local admin keeps /login?direct=1.
    occ.app_set("user_oidc", "allow_multiple_user_backends", "0")
    return [f"Sign-in: Kanidm ({provider.domain}) via user_oidc."]


def configure_office(occ: Occ, domain: str, *, enabled: bool, attempts: int = 24,
                     sleep: Callable[[float], None] = time.sleep) -> list[str]:
    if not enabled:
        return []
    last: OccError | None = None
    for _ in range(attempts):
        try:
            occ.run(
                "richdocuments:activate-config",
                f"--wopi-url={COLLABORA_INTERNAL_URL}",
                f"--callback-url=https://{domain}",
            )
            return []
        except OccError as exc:
            last = exc
            sleep(5)
    return [f"Warning: Collabora is not answering yet ({last}). Re-run apply once "
            "docker logs nextcloud_collabora shows it is ready."]


def configure_talk(occ: Occ, talk: TalkSettings | None) -> list[str]:
    if talk is None:
        return []
    occ.run("talk:signaling:delete", talk.signaling_url)
    occ.run_secret("talk:signaling:add", talk.signaling_url, talk.signaling_secret, "--verify")
    occ.run("talk:turn:delete", TURN_SCHEMES, talk.turn_server, TURN_PROTOCOLS)
    occ.run_secret("talk:turn:add", TURN_SCHEMES, talk.turn_server, TURN_PROTOCOLS,
                   f"--secret={talk.turn_secret}")
    # Fails harmlessly when the server is already listed.
    occ.run("talk:stun:add", talk.turn_server, check=False)
    return []


def configure_mail(occ: Occ, mail: MailProvisioning | None) -> list[str]:
    if mail is None:
        return []
    try:
        data = mail.provisioning_data()
    except MissingSecret as exc:
        return [f"Warning: Nextcloud Mail not provisioned yet ({exc}). "
                "Apply the Stalwart kit, then re-run this apply."]
    # Kanidm sessions have no Nextcloud password, so Mail never stores the
    # Stalwart master password on its own. This app does that at sign-in, and
    # tells the Mail page a password is available so it will sync the mailbox.
    occ.run("app:enable", "easydeploy_mail")
    # QRESYNC leaves the folder uncached, and the bundle's script nonce is the
    # login token rather than the page's content-security nonce.
    occ.helper("mail-imap-compat.php", {})
    result = occ.helper("mail-provisioning.php", data)
    count = next((line.split(":", 1)[1] for line in result.stdout.splitlines()
                  if line.startswith("provisioned:")), "0")
    return [f"Mail: Nextcloud Mail → Stalwart ({mail.mail_host}) for @{mail.email_domain} "
            f"({count} account(s); Kanidm sign-in uses the Stalwart master password)."]


def configure_audit(occ: Occ, spool: AuditSpool | None) -> list[str]:
    enabled, _ = occ.apps()
    if spool is None:
        if "admin_audit" in enabled:
            occ.run("app:disable", "admin_audit")
        return []
    if "admin_audit" not in enabled:
        occ.run("app:enable", "admin_audit")
    occ.system_set("log_type_audit", "file")
    occ.system_set("logfile_audit", f"{AUDIT_CONTAINER_DIR}/{AUDIT_FILENAME}")
    return [f"Audit: admin_audit writes to {spool.log_path} (ingested by easydeploy-engine)."]
