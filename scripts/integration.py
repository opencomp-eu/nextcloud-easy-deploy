"""Read the sidecars easydeploy-engine writes into .nextcloud-easy-deploy/integration/.

Each reader returns a typed settings object, or None when the integration is
not wired (no sidecar) or the operator opted out in deploy.yaml. Secrets are
{file, key} references resolved here, at this kit's apply time, because the
owning kit (Kanidm, Stalwart) may only have created them moments earlier.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = PROJECT_ROOT / ".nextcloud-easy-deploy" / "integration"
OIDC_SIDECAR = INTEGRATION_DIR / "oidc-provider.yaml"
MAIL_SIDECAR = INTEGRATION_DIR / "mail.yaml"
AUDIT_SIDECAR = INTEGRATION_DIR / "audit.yaml"
AUDIT_FILENAME = "nextcloud.jsonl"


class MissingSecret(LookupError):
    """A referenced secret file or key does not exist (yet)."""


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def _opted_out(section: Any) -> bool:
    if not isinstance(section, dict) or "managed" not in section:
        return False
    return not _to_bool(section.get("managed"))


def resolve_secret(ref: Any, *, base: Path = PROJECT_ROOT) -> str:
    """Resolve a {file, key} reference (dotted key path) or return a literal string."""
    if isinstance(ref, str):
        return ref
    if not isinstance(ref, dict) or not ref.get("file") or not ref.get("key"):
        raise ValueError(f"Secret reference must be {{file, key}}, got {ref!r}")
    path = Path(str(ref["file"])).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    if not path.is_file():
        raise MissingSecret(f"{path} does not exist")
    value: Any = _load(path)
    for part in str(ref["key"]).split("."):
        if not isinstance(value, dict) or part not in value:
            raise MissingSecret(f"{path} has no key {ref['key']!r}")
        value = value[part]
    text = str(value or "").strip()
    if not text:
        raise MissingSecret(f"{path} key {ref['key']!r} is empty")
    return text


@dataclass(frozen=True)
class OidcProvider:
    name: str
    domain: str
    discovery_url: str
    client_id: str
    client_secret: Any
    scopes: str
    mapping: dict[str, str]
    group_whitelist_regex: str

    def secret(self) -> str:
        return resolve_secret(self.client_secret)


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    ssl: str


@dataclass(frozen=True)
class MailProvisioning:
    email_domain: str
    imap: Endpoint
    smtp: Endpoint
    sieve: Endpoint
    master_user: str
    master_user_separator: str
    master_password: Any

    @property
    def mail_host(self) -> str:
        return self.imap.host

    def provisioning_data(self) -> dict[str, Any]:
        """The Nextcloud Mail provisioning config (Manager::newProvisioning keys)."""
        return {
            "provisioningDomain": self.email_domain,
            "emailTemplate": "%EMAIL%",
            "imapUser": "%EMAIL%",
            "imapHost": self.imap.host,
            "imapPort": self.imap.port,
            "imapSslMode": self.imap.ssl,
            "smtpUser": "%EMAIL%",
            "smtpHost": self.smtp.host,
            "smtpPort": self.smtp.port,
            "smtpSslMode": self.smtp.ssl,
            "sieveEnabled": True,
            "sieveUser": "%EMAIL%",
            "sieveHost": self.sieve.host,
            "sievePort": self.sieve.port,
            "sieveSslMode": self.sieve.ssl,
            "masterPasswordEnabled": True,
            # Mail appends this to the login: alice@example.com%admin
            "masterUser": f"{self.master_user_separator}{self.master_user}",
            "masterPassword": resolve_secret(self.master_password),
            "ldapAliasesProvisioning": False,
            "ldapAliasesAttribute": "",
        }


@dataclass(frozen=True)
class AuditSpool:
    spool_dir: Path

    @property
    def log_path(self) -> Path:
        return self.spool_dir / AUDIT_FILENAME


def _endpoint(data: Any, default_port: int, default_ssl: str, fallback_host: str) -> Endpoint:
    data = data if isinstance(data, dict) else {}
    return Endpoint(
        host=str(data.get("host") or fallback_host).strip(),
        port=int(data.get("port") or default_port),
        ssl=str(data.get("ssl") or default_ssl).strip(),
    )


def oidc_provider(config: dict, path: Path = OIDC_SIDECAR) -> OidcProvider | None:
    oidc = (config.get("auth") or {}).get("oidc") or {}
    if _opted_out(oidc):
        return None
    data = _load(path)
    if not data or str(data.get("provider") or "kanidm").strip().lower() != "kanidm":
        return None
    for key in ("domain", "discovery_url", "client_id", "client_secret"):
        if not data.get(key):
            raise ValueError(f"{path}: missing {key}")
    mapping = data.get("mapping") or {}
    return OidcProvider(
        name=str(data.get("name") or "Kanidm"),
        domain=str(data["domain"]).strip(),
        discovery_url=str(data["discovery_url"]).strip(),
        client_id=str(data["client_id"]).strip(),
        client_secret=data["client_secret"],
        scopes=str(data.get("scopes") or "openid profile email"),
        mapping={str(k): str(v) for k, v in mapping.items()} if isinstance(mapping, dict) else {},
        group_whitelist_regex=str(data.get("group_whitelist_regex") or ""),
    )


def mail_provisioning(config: dict, path: Path = MAIL_SIDECAR) -> MailProvisioning | None:
    if _opted_out(config.get("mail")):
        return None
    data = _load(path)
    if not data:
        return None
    imap = _endpoint(data.get("imap"), 993, "ssl", "")
    if not imap.host or not data.get("email_domain") or not data.get("master_password"):
        raise ValueError(f"{path}: needs email_domain, imap.host and master_password")
    return MailProvisioning(
        email_domain=str(data["email_domain"]).strip(),
        imap=imap,
        smtp=_endpoint(data.get("smtp"), 465, "ssl", imap.host),
        sieve=_endpoint(data.get("sieve"), 4190, "tls", imap.host),
        master_user=str(data.get("master_user") or "admin").strip(),
        master_user_separator=str(data.get("master_user_separator") or "%"),
        master_password=data["master_password"],
    )


def audit_spool(path: Path = AUDIT_SIDECAR) -> AuditSpool | None:
    data = _load(path)
    if not data or not _to_bool(data.get("enabled", True)) or not data.get("spool_dir"):
        return None
    return AuditSpool(spool_dir=Path(str(data["spool_dir"])).expanduser())
