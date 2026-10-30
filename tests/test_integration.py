from __future__ import annotations

import pytest
import yaml

from scripts import integration


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


def _provider_sidecar(tmp_path, secret_file):
    return _write(tmp_path / "oidc-provider.yaml", {
        "provider": "kanidm",
        "name": "Kanidm",
        "domain": "idm.acme.test",
        "discovery_url": "https://idm.acme.test/oauth2/openid/nextcloud/.well-known/openid-configuration",
        "client_id": "nextcloud",
        "client_secret": {"file": str(secret_file), "key": "client_secret"},
        "scopes": "openid profile email",
        "mapping": {"uid": "preferred_username", "groups": "nextcloud_groups"},
        "group_whitelist_regex": "^admin$",
    })


def test_oidc_provider_resolves_secret_reference(tmp_path):
    secret_file = _write(tmp_path / "kanidm" / "nextcloud.yaml", {"client_secret": "abc"})
    provider = integration.oidc_provider({}, _provider_sidecar(tmp_path, secret_file))
    assert provider.domain == "idm.acme.test"
    assert provider.mapping["groups"] == "nextcloud_groups"
    assert provider.secret() == "abc"


def test_oidc_secret_missing_until_kanidm_applies(tmp_path):
    provider = integration.oidc_provider({}, _provider_sidecar(tmp_path, tmp_path / "absent.yaml"))
    with pytest.raises(integration.MissingSecret):
        provider.secret()


def test_oidc_opt_out_and_absent_sidecar(tmp_path):
    sidecar = _provider_sidecar(tmp_path, tmp_path / "s.yaml")
    assert integration.oidc_provider({"auth": {"oidc": {"managed": False}}}, sidecar) is None
    assert integration.oidc_provider({}, tmp_path / "none.yaml") is None


def test_resolve_secret_follows_dotted_keys(tmp_path):
    path = _write(tmp_path / "s.yaml", {"outer": {"inner": "v"}})
    assert integration.resolve_secret({"file": str(path), "key": "outer.inner"}) == "v"
    with pytest.raises(integration.MissingSecret):
        integration.resolve_secret({"file": str(path), "key": "outer.other"})


def test_mail_provisioning_maps_stalwart_sidecar(tmp_path):
    secrets = _write(tmp_path / "stalwart" / "secrets.yaml", {"RECOVERY_ADMIN_PASSWORD": "master"})
    sidecar = _write(tmp_path / "mail.yaml", {
        "provider": "stalwart",
        "email_domain": "acme.test",
        "imap": {"host": "mail.acme.test", "port": 993, "ssl": "ssl"},
        "smtp": {"host": "mail.acme.test", "port": 465, "ssl": "ssl"},
        "sieve": {"host": "mail.acme.test", "port": 4190, "ssl": "tls"},
        "master_user": "admin",
        "master_user_separator": "%",
        "master_password": {"file": str(secrets), "key": "RECOVERY_ADMIN_PASSWORD"},
    })
    mail = integration.mail_provisioning({}, sidecar)
    data = mail.provisioning_data()
    assert data["provisioningDomain"] == "acme.test"
    assert data["imapUser"] == data["smtpUser"] == data["sieveUser"] == "%EMAIL%"
    assert (data["imapHost"], data["imapPort"], data["imapSslMode"]) == ("mail.acme.test", 993, "ssl")
    assert (data["sievePort"], data["sieveSslMode"]) == (4190, "tls")
    assert data["masterPasswordEnabled"] is True
    assert data["masterUser"] == "%admin"
    assert data["masterPassword"] == "master"
    assert integration.mail_provisioning({"mail": {"managed": False}}, sidecar) is None


def test_audit_spool_sidecar(tmp_path):
    sidecar = _write(tmp_path / "audit.yaml", {"enabled": True, "spool_dir": str(tmp_path / "spool")})
    spool = integration.audit_spool(sidecar)
    assert spool.log_path == tmp_path / "spool" / "nextcloud.jsonl"
    assert integration.audit_spool(tmp_path / "none.yaml") is None
