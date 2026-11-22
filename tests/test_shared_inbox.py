"""A shared inbox is a normal mailbox attached to someone's Mail account."""

from __future__ import annotations

import subprocess

import pytest

from scripts.integration import Endpoint, MailProvisioning
from scripts.shared_inbox import (
    attach_mailbox,
    created_account_id,
    ensure_untracked_mailbox,
    exported_accounts,
    imap_login,
    mailbox_label,
    parse_address,
)


def _mail() -> MailProvisioning:
    return MailProvisioning(
        email_domain="example.com",
        imap=Endpoint("mail.example.com", 993, "ssl"),
        smtp=Endpoint("mail.example.com", 465, "ssl"),
        sieve=Endpoint("mail.example.com", 4190, "tls"),
        master_user="admin",
        master_user_separator="%",
        master_password="secret",
    )


class _Occ:
    def __init__(self, export: str = "", created: str = "Account 9 for info@example.com created\n"):
        self.export = export
        self.created = created
        self.run_calls: list[tuple[str, ...]] = []
        self.secret_calls: list[tuple[str, ...]] = []

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        self.run_calls.append(args)
        text = self.export if args[:1] == ("mail:account:export",) else ""
        return subprocess.CompletedProcess(list(args), 0, text, "")

    def run_secret(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        self.secret_calls.append(args)
        return subprocess.CompletedProcess(list(args), 0, self.created, "")


def _jmap(*responses: dict):
    pending = list(responses)

    def jmap(calls):
        assert pending, calls
        return pending.pop(0)

    return jmap


def _listed(domain_id: str, accounts: list[dict]) -> tuple[dict, dict]:
    query = {
        "methodResponses": [
            ["x:Domain/query", {"ids": [domain_id]}, "q"],
            ["x:Account/query", {"ids": [item["id"] for item in accounts]}, "aq"],
        ]
    }
    follow = {
        "methodResponses": [
            ["x:Domain/get", {"list": [{"id": domain_id, "name": "example.com"}]}, "dg"],
            ["x:Account/get", {"list": accounts}, "ag"],
        ]
    }
    return query, follow


def test_address_must_be_on_the_mail_domain():
    assert parse_address("Info@Example.com", "example.com") == ("info", "info@example.com")
    with pytest.raises(ValueError, match="mail domain"):
        parse_address("info@other.test", "example.com")
    with pytest.raises(ValueError, match="mailbox name"):
        parse_address("Info Box@example.com", "example.com")


def test_export_parser_and_login_match_the_master_user_mail_already_uses():
    text = "Account 2:\n- E-Mail: thomas@example.com\n- Name: Thomas\n\nAccount 5:\n- Email: info@example.com\n"
    assert exported_accounts(text) == {"thomas@example.com": 2, "info@example.com": 5}
    assert created_account_id("Account 5 for info@example.com created\n") == 5
    assert imap_login("info@example.com", "%", "admin") == "info@example.com%admin"
    assert mailbox_label("info", None) == "Info"
    assert mailbox_label("info", " Company inbox ") == "Company inbox"


def test_ensure_creates_a_user_mailbox_and_leaves_an_existing_one():
    query, follow = _listed("b", [])
    created = {"methodResponses": [["x:Account/set", {"created": {"info@example.com": {"id": "h"}}}, "c"]]}
    calls = []

    def jmap(method_calls):
        calls.append(method_calls)
        return [query, follow, created][len(calls) - 1]

    assert ensure_untracked_mailbox(jmap, email="info@example.com", domain="example.com", reserved={"admin"}) == "created"
    payload = calls[-1][0][1]["create"]["info@example.com"]
    assert payload["@type"] == "User"
    assert payload["name"] == "info"
    assert payload["domainId"] == "b"

    query, follow = _listed("b", [{"id": "h", "@type": "User", "emailAddress": "info@example.com"}])
    assert ensure_untracked_mailbox(_jmap(query, follow), email="info@example.com", domain="example.com", reserved=set()) == "present"


def test_ensure_refuses_a_group_and_the_recovery_admin_name():
    query, follow = _listed("b", [{"id": "g", "@type": "Group", "emailAddress": "info@example.com"}])
    with pytest.raises(RuntimeError, match="group mailbox"):
        ensure_untracked_mailbox(_jmap(query, follow), email="info@example.com", domain="example.com", reserved=set())
    with pytest.raises(ValueError, match="reserved"):
        ensure_untracked_mailbox(_jmap(), email="admin@example.com", domain="example.com", reserved={"admin"})


def test_attach_creates_once_then_only_syncs():
    occ = _Occ()
    mail = _mail()
    note = attach_mailbox(occ, mail, user="thomas", email="info@example.com", label="Info", password="secret")
    assert note == "info@example.com added to thomas (Mail account 9)"
    assert occ.run_calls[0] == ("user:info", "thomas")
    create = occ.secret_calls[0]
    assert create[:4] == ("mail:account:create", "thomas", "Info", "info@example.com")
    assert create[7] == "info@example.com%admin"
    assert "secret" not in occ.run_calls[-1]
    assert occ.run_calls[-1] == ("mail:account:sync", "9", "--force")

    again = _Occ(export="Account 9:\n- E-Mail: info@example.com\n")
    note = attach_mailbox(again, mail, user="thomas", email="info@example.com", label="Info", password="secret")
    assert "already on thomas" in note
    assert again.secret_calls == []
    assert again.run_calls[-1] == ("mail:account:sync", "9", "--force")
