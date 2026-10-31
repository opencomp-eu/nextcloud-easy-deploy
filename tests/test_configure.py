from __future__ import annotations

import json
import subprocess

from scripts import configure
from scripts.integration import AuditSpool, OidcProvider
from scripts.occ import Occ, OccError, parse_json_output


def test_ensure_apps_installs_enables_and_reports_failures(fake_occ):
    fake_occ.enabled = {"calendar"}
    fake_occ.disabled = {"contacts", "richdocuments"}
    fake_occ.enabled.add("richdocuments")
    fake_occ.fail = {"app:install"}
    notes = configure.ensure_apps(fake_occ, ["calendar", "contacts", "deck"], update=False,
                                  disable=("richdocuments",))
    assert ("app:enable", "contacts") in fake_occ.calls
    assert ("app:install", "deck") in fake_occ.calls
    assert ("app:disable", "richdocuments") in fake_occ.calls
    assert ("app:update", "--all") not in fake_occ.calls
    assert notes and "'deck'" in notes[0]


def test_oidc_secret_only_travels_on_stdin(fake_occ):
    provider = OidcProvider(
        name="Kanidm", domain="idm.acme.test", discovery_url="https://idm/x",
        client_id="nextcloud", client_secret="s3cret", scopes="openid profile email",
        mapping={"uid": "preferred_username", "display_name": "name", "email": "email",
                 "groups": "nextcloud_groups"},
        group_whitelist_regex="^admin$",
    )
    configure.configure_oidc(fake_occ, provider)
    (call,) = fake_occ.secret_calls
    assert call[:2] == ("user_oidc:provider", "Kanidm")
    assert "--clientsecret=s3cret" in call
    assert "--mapping-groups=nextcloud_groups" in call
    assert "--group-whitelist-regex=^admin$" in call
    assert ("config:app:set", "user_oidc", "allow_multiple_user_backends", "0") in fake_occ.calls


def test_oidc_waits_for_kanidm_secret(fake_occ, tmp_path):
    provider = OidcProvider(
        name="Kanidm", domain="idm", discovery_url="https://idm/x", client_id="nextcloud",
        client_secret={"file": str(tmp_path / "missing.yaml"), "key": "client_secret"},
        scopes="openid", mapping={}, group_whitelist_regex="",
    )
    notes = configure.configure_oidc(fake_occ, provider)
    assert fake_occ.secret_calls == []
    assert notes[0].startswith("Warning: Kanidm sign-in not configured yet")


def test_talk_replaces_own_servers_idempotently(fake_occ):
    talk = configure.TalkSettings(domain="cloud.acme.test", turn_port=3478,
                                  turn_secret="t", signaling_secret="s")
    configure.configure_talk(fake_occ, talk)
    assert fake_occ.calls[0] == ("talk:signaling:delete", "wss://cloud.acme.test/standalone-signaling/")
    assert fake_occ.secret_calls == [
        ("talk:signaling:add", "wss://cloud.acme.test/standalone-signaling/", "s", "--verify"),
        ("talk:turn:add", "turn", "cloud.acme.test:3478", "udp,tcp", "--secret=t"),
    ]
    assert ("talk:stun:add", "cloud.acme.test:3478") in fake_occ.calls


def test_office_retries_until_collabora_answers():
    attempts = []

    class FlakyOcc:
        def run(self, *args, check=True):
            attempts.append(args)
            if len(attempts) < 3:
                raise OccError(args, subprocess.CompletedProcess(args, 1, "", "not ready"))

    notes = configure.configure_office(FlakyOcc(), "cloud.acme.test", enabled=True, sleep=lambda _: None)
    assert notes == []
    assert attempts[-1] == ("richdocuments:activate-config",
                            "--wopi-url=http://nextcloud_collabora:9980",
                            "--callback-url=https://cloud.acme.test")


def test_mail_enables_the_kanidm_password_bridge(fake_occ):
    from scripts.integration import Endpoint, MailProvisioning

    mail = MailProvisioning(
        email_domain="acme.test",
        imap=Endpoint("mail.acme.test", 993, "ssl"),
        smtp=Endpoint("mail.acme.test", 465, "ssl"),
        sieve=Endpoint("mail.acme.test", 4190, "tls"),
        master_user="admin",
        master_user_separator="%",
        master_password="pw",
    )
    notes = configure.configure_mail(fake_occ, mail)
    assert [name for name, _payload in fake_occ.helpers] == [
        "mail-imap-compat.php",
        "mail-provisioning.php",
    ]
    assert ("app:enable", "easydeploy_mail") in fake_occ.calls
    assert notes and "master password" in notes[0]
    assert "folder(s) cached" in notes[0]


def test_audit_enables_or_disables_admin_audit(fake_occ, tmp_path):
    configure.configure_audit(fake_occ, AuditSpool(tmp_path))
    assert ("app:enable", "admin_audit") in fake_occ.calls
    assert ("config:system:set", "logfile_audit",
            "'/var/lib/easydeploy-audit/nextcloud.jsonl'") in fake_occ.calls
    configure.configure_audit(fake_occ, None)
    assert ("app:disable", "admin_audit") in fake_occ.calls


def test_occ_run_secret_keeps_arguments_off_argv():
    seen = {}

    def runner(cmd, **kwargs):
        seen["cmd"], seen["input"] = cmd, kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    Occ(runner=runner).run_secret("talk:turn:add", "--secret=hunter2")
    assert "hunter2" not in " ".join(seen["cmd"])
    assert seen["cmd"][:2] == ["docker", "exec"] and "-i" in seen["cmd"]
    assert json.loads(seen["input"])[-1] == "--secret=hunter2"


def test_occ_status_and_json_tolerate_php_notices():
    assert parse_json_output('PHP Notice: x\n{"installed":true}') == {"installed": True}

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, 'Warning\n{"installed":false}', "")

    assert Occ(runner=runner).status() == {"installed": False}
