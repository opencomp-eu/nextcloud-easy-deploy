from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import apply, integration
from tests.conftest import base_config


def test_compose_files_follow_office_talk_and_proxy_mode(tmp_path):
    config = base_config(tmp_path)
    assert apply.derive_compose_files(config) == [
        "docker-compose.yml", "office.yml", "talk.yml",
        "integrate.yml", "integrate-office.yml", "integrate-talk.yml",
    ]
    config = base_config(tmp_path, office={"enabled": False}, talk={"enabled": False},
                         proxy={"mode": "standalone"})
    assert apply.derive_compose_files(config) == ["docker-compose.yml", "caddy.yml"]


def test_validate_rejects_missing_or_shared_office_domain(tmp_path):
    with pytest.raises(ValueError, match="office.domain is required"):
        apply.validate_config(base_config(tmp_path, office={"enabled": True}))
    with pytest.raises(ValueError, match="must differ"):
        apply.validate_config(base_config(tmp_path, office={"enabled": True, "domain": "cloud.acme.test"}))
    with pytest.raises(ValueError, match="nextcloud.domain"):
        apply.validate_config({"nextcloud": {"data_dir": "/x"}})
    apply.validate_config(base_config(tmp_path, office={"enabled": False}))


def test_caddy_routes_dav_talk_signaling_and_office(tmp_path):
    blocks = apply.site_blocks(base_config(tmp_path))
    assert "cloud.acme.test {" in blocks
    assert "redir /.well-known/caldav /remote.php/dav/ 301" in blocks
    assert "handle_path /standalone-signaling/* {" in blocks
    assert "reverse_proxy nextcloud_talk:8081" in blocks
    assert "reverse_proxy nextcloud:80" in blocks
    assert "office.acme.test {\n\treverse_proxy nextcloud_collabora:9980\n}" in blocks

    plain = apply.site_blocks(base_config(tmp_path, office={"enabled": False}, talk={"enabled": False}))
    assert "standalone-signaling" not in plain
    assert "office.acme.test" not in plain


def test_secrets_are_generated_once_and_private(tmp_path, state):
    first = apply.load_or_create_secrets(base_config(tmp_path))
    assert set(apply.SECRET_KEYS) <= set(first)
    assert "BORG_PASSPHRASE" not in first
    assert stat.S_IMODE(apply.SECRETS_PATH.stat().st_mode) == 0o600
    again = apply.load_or_create_secrets(base_config(tmp_path, backup={"enabled": True}))
    assert {k: again[k] for k in apply.SECRET_KEYS} == {k: first[k] for k in apply.SECRET_KEYS}
    assert again["BORG_PASSPHRASE"]


def test_compose_env_carries_only_enabled_components(tmp_path, state):
    secrets = apply.load_or_create_secrets()
    env = dict(line.split("=", 1) for line in apply.compose_env_lines(base_config(tmp_path), secrets))
    assert env["NEXTCLOUD_DOMAIN"] == "cloud.acme.test"
    assert env["NEXTCLOUD_DATA_DIR"] == str(tmp_path / "data" / "data")
    assert env["OFFICE_DOMAIN"] == "office.acme.test"
    assert env["TURN_SECRET"] == secrets["TURN_SECRET"]
    assert env["TRUSTED_PROXIES"] == "172.16.0.0/12 192.168.0.0/16 10.0.0.0/8"
    assert "NED_CADDYFILE" not in env

    config = base_config(tmp_path, office={"enabled": False}, talk={"enabled": False},
                         proxy={"mode": "standalone"})
    env = dict(line.split("=", 1) for line in apply.compose_env_lines(config, secrets))
    assert "OFFICE_DOMAIN" not in env and "TURN_SECRET" not in env
    assert env["NED_CADDYFILE"] == str(apply.CADDYFILE.resolve())


def _provider() -> integration.OidcProvider:
    return integration.OidcProvider(
        name="Kanidm", domain="idm.acme.test",
        discovery_url="https://idm.acme.test/oauth2/openid/nextcloud/.well-known/openid-configuration",
        client_id="nextcloud", client_secret="s3cret", scopes="openid profile email",
        mapping={"uid": "preferred_username", "groups": "nextcloud_groups"},
        group_whitelist_regex="^admin$",
    )


def _mail() -> integration.MailProvisioning:
    endpoint = integration.Endpoint
    return integration.MailProvisioning(
        email_domain="acme.test",
        imap=endpoint("mail.acme.test", 993, "ssl"),
        smtp=endpoint("mail.acme.test", 465, "ssl"),
        sieve=endpoint("mail.acme.test", 4190, "tls"),
        master_user="admin", master_user_separator="%", master_password="pw",
    )


def test_runtime_overlay_pins_public_names_and_mounts_audit_spool(tmp_path):
    spool = integration.AuditSpool(spool_dir=tmp_path / "spool")
    overlay = apply.runtime_overlay(base_config(tmp_path), oidc=_provider(), mail=_mail(), spool=spool)
    services = overlay["services"]
    assert services["nextcloud"]["extra_hosts"] == [
        "cloud.acme.test:host-gateway", "office.acme.test:host-gateway",
        "idm.acme.test:host-gateway", "mail.acme.test:host-gateway",
    ]
    assert services["nextcloud_cron"]["extra_hosts"] == services["nextcloud"]["extra_hosts"]
    assert services["nextcloud_collabora"]["extra_hosts"] == ["cloud.acme.test:host-gateway"]
    assert services["nextcloud_talk"]["extra_hosts"] == ["cloud.acme.test:host-gateway"]
    assert services["nextcloud"]["volumes"] == [f"{tmp_path / 'spool'}:/var/lib/easydeploy-audit"]


def test_runtime_overlay_omits_services_that_are_off(tmp_path):
    config = base_config(tmp_path, office={"enabled": False}, talk={"enabled": False})
    services = apply.runtime_overlay(config, oidc=None, mail=None, spool=None)["services"]
    assert set(services) == {"nextcloud", "nextcloud_cron"}
    assert services["nextcloud"] == {"extra_hosts": ["cloud.acme.test:host-gateway"]}


def test_wanted_apps_follow_integrations(tmp_path):
    config = base_config(tmp_path, office={"enabled": False})
    apps = apply.wanted_apps(config, oidc=True, mail=True)
    assert "richdocuments" not in apps
    assert apps[-1] == "user_oidc"
    config["nextcloud"]["apps"] = ["calendar"]
    assert apply.wanted_apps(config, oidc=False, mail=True) == ["calendar", "mail"]


def test_render_runtime_artifacts_writes_fragment_and_file_list(tmp_path, state):
    config = base_config(tmp_path)
    secrets = apply.load_or_create_secrets(config)
    spool = integration.AuditSpool(spool_dir=tmp_path / "spool")
    apply.render_runtime_artifacts(config, secrets, {"oidc": None, "mail": None, "spool": spool})
    assert "cloud.acme.test {" in apply.INTEGRATION_CADDY_FRAGMENT.read_text()
    files = apply.COMPOSE_FILES_LIST.read_text().split()
    assert files[0].endswith("compose/docker-compose.yml")
    assert files[-1] == str(apply.RUNTIME_OVERLAY_PATH.resolve())
    assert stat.S_IMODE(spool.log_path.stat().st_mode) == 0o666
    for name in ("html", "data", "postgres"):
        assert (tmp_path / "data" / name).is_dir()


def test_configure_nextcloud_runs_every_wired_step(tmp_path, fake_occ):
    fake_occ.enabled = {"admin_audit"}
    secrets = {"TURN_SECRET": "turn-secret-value", "SIGNALING_SECRET": "sig-secret-value"}
    spool = integration.AuditSpool(spool_dir=tmp_path / "spool")
    notes = apply.configure_nextcloud(
        base_config(tmp_path), secrets,
        {"oidc": _provider(), "mail": _mail(), "spool": spool}, occ=fake_occ,
    )
    secret_commands = [call[0] for call in fake_occ.secret_calls]
    assert secret_commands == ["user_oidc:provider", "talk:signaling:add", "talk:turn:add"]
    assert "richdocuments:activate-config" in fake_occ.commands()
    assert [name for name, _payload in fake_occ.helpers] == [
        "mail-imap-compat.php",
        "mail-provisioning.php",
    ]
    assert any(note.startswith("Sign-in: Kanidm") for note in notes)
    assert any(note.startswith("Audit:") for note in notes)
    plain = " ".join(" ".join(call) for call in fake_occ.calls)
    for secret in ("turn-secret-value", "sig-secret-value", "s3cret"):
        assert secret not in plain


def test_configure_nextcloud_skips_talk_when_disabled(tmp_path, fake_occ):
    config = base_config(tmp_path, talk={"enabled": False}, office={"enabled": False})
    apply.configure_nextcloud(config, {}, {"oidc": None, "mail": None, "spool": None}, occ=fake_occ)
    assert fake_occ.secret_calls == []
    assert not any(c.startswith("talk:") for c in fake_occ.commands())
    assert "richdocuments:activate-config" not in fake_occ.commands()


def _compose_config(tmp_path: Path, state: Path, config: dict) -> dict:
    secrets = apply.load_or_create_secrets(config)
    apply.render_runtime_artifacts(
        config, secrets,
        {"oidc": _provider(), "mail": None, "spool": integration.AuditSpool(tmp_path / "spool")},
    )
    cmd = ["docker", "compose", "-p", apply.COMPOSE_PROJECT_NAME]
    for path in apply.COMPOSE_FILES_LIST.read_text().split():
        cmd += ["-f", path]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", **dict(
        line.split("=", 1) for line in apply.COMPOSE_ENV_PATH.read_text().splitlines()
    )}
    result = subprocess.run([*cmd, "config", "--format", "yaml"], cwd=apply.COMPOSE_DIR,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "variable is not set" not in result.stderr
    return yaml.safe_load(result.stdout)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose not installed")
@pytest.mark.parametrize("mode", ["integrate", "standalone"])
def test_compose_model_is_valid(tmp_path, state, mode):
    model = _compose_config(tmp_path, state, base_config(tmp_path, proxy={"mode": mode}))
    services = model["services"]
    expected = {"nextcloud", "nextcloud_cron", "nextcloud_postgres", "nextcloud_redis",
                "nextcloud_collabora", "nextcloud_talk"}
    if mode == "standalone":
        expected.add("caddy")
    assert set(services) == expected
    nextcloud = services["nextcloud"]
    assert "idm.acme.test=host-gateway" in str(nextcloud["extra_hosts"]) \
        or "idm.acme.test:host-gateway" in str(nextcloud["extra_hosts"])
    targets = {volume["target"] for volume in nextcloud["volumes"]}
    assert {"/var/www/html", "/var/www/html/data", "/opt/easydeploy",
            "/var/www/html/custom_apps/easydeploy_mail",
            "/var/lib/easydeploy-audit"} <= targets
    cron_targets = {volume["target"] for volume in services["nextcloud_cron"]["volumes"]}
    assert "/var/www/html/custom_apps/easydeploy_mail" in cron_targets
    assert nextcloud["environment"]["POSTGRES_USER"] == "nextcloud"
    assert ("easydeploy-net" in nextcloud["networks"]) == (mode == "integrate")


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose not installed")
def test_compose_model_without_office_or_talk(tmp_path, state):
    config = base_config(tmp_path, office={"enabled": False}, talk={"enabled": False})
    services = _compose_config(tmp_path, state, config)["services"]
    assert "nextcloud_collabora" not in services and "nextcloud_talk" not in services
