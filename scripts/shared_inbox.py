"""Add a shared inbox to one or more people's Nextcloud Mail.

A Stalwart group mailbox shows up as a folder Mail cannot open: the folder is
not a mailbox, so the app asks for messages and retries forever. A shared
inbox is an ordinary mailbox instead. The mail-users sync does not track it,
so it is left in place, and each person gets it as a second Mail account.
Mail signs in with the same master password as their own mailbox.

    uv run python -m scripts.shared_inbox info@example.com thomas ada
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from scripts.integration import MailProvisioning, resolve_secret
from scripts.occ import Occ

Jmap = Callable[[list], dict]
_LOCAL_PART = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,62}$")
_ACCOUNT_HEADER = re.compile(r"^Account (\d+):\s*$")
_EMAIL_LINE = re.compile(r"^- E-?Mail:\s+(\S+)\s*$", re.IGNORECASE)
_CREATED = re.compile(r"Account (\d+) for \S+ created")

# Same shape as stalwart-easy-deploy scripts.mailboxes.account_create_payload.
# A hand-made account stays out of provisioned-mailboxes.json, so the
# mail-users sync does not delete it when the address is not a person.


def user_mailbox_payload(local: str, domain_id: str) -> dict:
    return {
        "@type": "User",
        "name": local,
        "domainId": domain_id,
        "aliases": {},
        "credentials": {},
        "memberGroupIds": {},
        "roles": {"@type": "User"},
        "permissions": {"@type": "Inherit"},
        "quotas": {},
        "encryptionAtRest": {"@type": "Disabled"},
    }


def parse_address(address: str, domain: str) -> tuple[str, str]:
    """Return (local part, email) when the address is on the mail domain."""
    email = address.strip().casefold()
    local, separator, host = email.partition("@")
    if not separator or not host:
        raise ValueError(f"{address!r} is not an email address")
    if host != domain.strip().casefold():
        raise ValueError(f"{email} is not on the mail domain {domain}")
    if not _LOCAL_PART.match(local):
        raise ValueError(f"{local!r} is not a usable mailbox name")
    return local, email


def mailbox_label(local: str, override: str | None = None) -> str:
    if override and override.strip():
        return override.strip()
    return local[:1].upper() + local[1:]


def imap_login(email: str, separator: str, master_user: str) -> str:
    """The master-user login Mail already uses: alice@example.com%admin."""
    return f"{email}{separator}{master_user}"


def exported_accounts(text: str) -> dict[str, int]:
    """Mail account ids from `occ mail:account:export`, keyed by address."""
    current: int | None = None
    found: dict[str, int] = {}
    for line in text.splitlines():
        header = _ACCOUNT_HEADER.match(line.strip())
        if header:
            current = int(header.group(1))
            continue
        email = _EMAIL_LINE.match(line.strip())
        if email and current is not None:
            found[email.group(1).casefold()] = current
    return found


def created_account_id(text: str) -> int:
    match = _CREATED.search(text)
    if not match:
        detail = text.strip() or "Mail did not report the new account"
        raise RuntimeError(detail)
    return int(match.group(1))


def _method_result(response: dict, call_id: str) -> dict:
    for entry in response.get("methodResponses") or []:
        if not isinstance(entry, list) or len(entry) < 3 or entry[2] != call_id:
            continue
        name, payload = entry[0], entry[1]
        if name == "error" or str(name).endswith("/error"):
            detail = payload if isinstance(payload, dict) else {"description": payload}
            raise RuntimeError(detail.get("description") or detail.get("type") or str(payload))
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected JMAP payload for {call_id}")
        return payload
    raise RuntimeError(f"no JMAP response for {call_id}")


def _accounts_and_domain(jmap: Jmap, domain: str) -> tuple[str, list[dict]]:
    listed = jmap([["x:Domain/query", {}, "q"], ["x:Account/query", {}, "aq"]])
    domain_ids = list(_method_result(listed, "q").get("ids") or [])
    account_ids = list(_method_result(listed, "aq").get("ids") or [])
    follow: list = []
    if domain_ids:
        follow.append(["x:Domain/get", {"ids": domain_ids}, "dg"])
    if account_ids:
        follow.append(["x:Account/get", {"ids": account_ids}, "ag"])
    got = jmap(follow) if follow else {"methodResponses": []}
    domain_id = ""
    if domain_ids:
        for item in _method_result(got, "dg").get("list") or []:
            if str(item.get("name") or "").strip().lower() == domain:
                domain_id = str(item.get("id") or "")
                break
    accounts = list(_method_result(got, "ag").get("list") or []) if account_ids else []
    return domain_id, [item for item in accounts if isinstance(item, dict)]


def ensure_untracked_mailbox(
    jmap: Jmap,
    *,
    email: str,
    domain: str,
    reserved: set[str],
) -> str:
    """Create the mailbox when it is missing. Return 'created' or 'present'.

    A group at this address is refused: Mail cannot open one. The mailbox is
    not added to the mail-users tracking file.
    """
    local, email = parse_address(email, domain)
    if local in {name.strip().casefold() for name in reserved if name.strip()}:
        raise ValueError(f"{email} uses the reserved name {local!r}")
    domain_id, accounts = _accounts_and_domain(jmap, domain)
    existing = next(
        (
            item
            for item in accounts
            if str(item.get("emailAddress") or "").strip().casefold() == email
        ),
        None,
    )
    if existing is not None:
        kind = str(existing.get("@type") or "")
        if kind == "User":
            return "present"
        if kind == "Group":
            raise RuntimeError(
                f"{email} is a Stalwart group mailbox. Nextcloud Mail cannot open "
                "those folders. Remove the group, then run this again."
            )
        raise RuntimeError(f"{email} already belongs to a {kind or 'non-user'} account")
    if not domain_id:
        raise RuntimeError(f"Stalwart has no domain {domain!r}")
    created = _method_result(
        jmap([["x:Account/set", {"create": {email: user_mailbox_payload(local, domain_id)}}, "c"]]),
        "c",
    )
    if created.get("notCreated"):
        raise RuntimeError(f"Could not create mailbox {email}: {created['notCreated']}")
    return "created"


def attach_mailbox(
    occ: Occ,
    mail: MailProvisioning,
    *,
    user: str,
    email: str,
    label: str,
    password: str,
) -> str:
    """Add the mailbox to one Nextcloud user, or leave it when it is already there."""
    occ.run("user:info", user)
    exported = exported_accounts(occ.run("mail:account:export", user).stdout)
    login = imap_login(email, mail.master_user_separator, mail.master_user)
    if email in exported:
        account_id = exported[email]
        verb = "already on"
    else:
        result = occ.run_secret(
            "mail:account:create",
            user,
            label,
            email,
            mail.imap.host,
            str(mail.imap.port),
            mail.imap.ssl,
            login,
            password,
            mail.smtp.host,
            str(mail.smtp.port),
            mail.smtp.ssl,
            login,
            password,
        )
        account_id = created_account_id(result.stdout)
        verb = "added to"
    occ.run("mail:account:sync", str(account_id), "--force")
    return f"{email} {verb} {user} (Mail account {account_id})"


def stalwart_root(project_root: Path | None = None) -> Path:
    root = (project_root or Path(__file__).resolve().parent.parent).parent / "stalwart-easy-deploy"
    if not (root / "scripts" / "mailboxes.py").is_file():
        raise RuntimeError(f"Stalwart kit not found at {root}")
    return root


def jmap_via_stalwart(root: Path, method_calls: list) -> dict:
    """Run one JMAP call in the Stalwart kit so this package stays the importer."""
    code = (
        "import json, sys\n"
        "from scripts.apply import SECRETS_PATH, _jmap, load_config, load_yaml\n"
        "if not SECRETS_PATH.is_file():\n"
        "    sys.stderr.write(f'missing {SECRETS_PATH}\\n')\n"
        "    raise SystemExit(1)\n"
        "secrets = load_yaml(SECRETS_PATH)\n"
        "json.dump(_jmap(load_config(), secrets, json.load(sys.stdin)), sys.stdout)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        input=json.dumps(method_calls),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "JMAP call failed").strip()
        raise RuntimeError(detail)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Stalwart returned no JMAP result: {result.stdout[:200]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Stalwart returned an unexpected JMAP result")
    return data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a shared inbox to people's Nextcloud Mail.",
    )
    parser.add_argument("address", help="Mailbox address, for example info@example.com")
    parser.add_argument("users", nargs="+", metavar="USER", help="Nextcloud user ids who should see it")
    parser.add_argument("--name", help="Name shown in Mail. Defaults to the part before @.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        from scripts.apply import load_config
        from scripts.integration import mail_provisioning

        config = load_config()
        mail = mail_provisioning(config)
        if mail is None:
            raise RuntimeError("Mail is not managed. Set mail.managed and apply the Stalwart kit.")
        local, email = parse_address(args.address, mail.email_domain)
        password = resolve_secret(mail.master_password)
        outcome = ensure_untracked_mailbox(
            lambda calls: jmap_via_stalwart(stalwart_root(), calls),
            email=email,
            domain=mail.email_domain,
            reserved={mail.master_user},
        )
        print(f"Mailbox {email} {outcome}.")
        occ = Occ()
        label = mailbox_label(local, args.name)
        for user in args.users:
            print(attach_mailbox(occ, mail, user=user, email=email, label=label, password=password))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
