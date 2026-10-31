# nextcloud-easy-deploy

Production-oriented Nextcloud for a VPS: Nextcloud with PostgreSQL and Redis,
Collabora Online for documents, and the Talk high-performance backend for
calls. Caddy provides HTTPS certificates.

On its own it is a complete Nextcloud server. Together with
[easydeploy-engine](https://github.com/opencomp-eu/easydeploy-engine) it is the
groupware half of the **Nextcloud deployment path** (Kanidm, Stalwart,
Nextcloud). In that setup the engine wires everything for you:

- **Sign-in:** people sign in with their Kanidm account (`user_oidc`).
  Members of the Kanidm group `nextcloud-admins` are Nextcloud
  administrators. The person who ran the wizard is in that group.
- **Mail:** every user whose email address is on the Stalwart domain gets a
  ready-to-use mailbox in Nextcloud Mail. Mail signs in to Stalwart as
  `mailbox%admin` with the recovery admin password, so users never type a mail
  password. Kanidm sign-in has no Nextcloud password; apply stores that master
  password, caches each folder, and a small app does both again at sign-in.
  Mail would otherwise skip the sync and leave the message list on HTTP 400.
- **Calendar and contacts:** Nextcloud owns them (CalDAV/CardDAV at
  `https://<domain>/remote.php/dav/`).
- **Audit:** Nextcloud's `admin_audit` log goes into the engine's audit trail.

## Quick start

```bash
git clone --recurse-submodules https://github.com/opencomp-eu/nextcloud-easy-deploy.git
cd nextcloud-easy-deploy
bash wizard.sh
```

The wizard asks for the Nextcloud domain (for example `cloud.example.com`), the
office domain (`office.example.com`), the data directory, and whether to run
Collabora and the Talk backend. Then it deploys. Point DNS for both domains at
the server first.

On a multi-service VPS, run `bash wizard.sh` in easydeploy-engine instead and
choose the Nextcloud path. The engine runs this wizard with `--from-engine`.

Without the wizard:

```bash
cp deploy.yaml.example deploy.yaml   # edit domains and data_dir
bash apply.sh --ensure-dependencies
```

## What apply does

`apply.sh` is idempotent. Re-run it after editing `deploy.yaml`.

1. It generates secrets once, into `.nextcloud-easy-deploy/secrets.yaml` (mode 0600).
2. It renders the Caddy site blocks: a standalone `caddy/Caddyfile`, or the
   integrate fragment `.nextcloud-easy-deploy/integration/caddy.caddy` that
   easydeploy-engine includes.
3. It starts the containers. The Nextcloud image installs Nextcloud on first
   start and upgrades it when `nextcloud.tag` changes.
4. It configures Nextcloud through `occ`:
   - trusted domain, proxy settings, and cron background jobs
   - the apps from `nextcloud.apps`
   - Kanidm sign-in
   - Collabora
   - the Talk signaling, TURN and STUN servers
   - Mail provisioning
   - the audit log

   Commands that carry a secret pass their arguments to PHP on stdin
   (`php/occ-stdin.php`), so secrets never show in a process list.

| Container | Role |
|---|---|
| `nextcloud` | Nextcloud (Apache) |
| `nextcloud_cron` | Background jobs every 5 minutes |
| `nextcloud_postgres` | PostgreSQL 16. Nextcloud uses its own `nextcloud` role, which has no superuser rights |
| `nextcloud_redis` | File locking and cache |
| `nextcloud_collabora` | Collabora Online at `office.domain` (if `office.enabled`) |
| `nextcloud_talk` | Signaling, Janus, and TURN (if `talk.enabled`) |
| `nextcloud_caddy` | HTTPS (standalone mode only) |

Talk signaling is served at `https://<nextcloud.domain>/standalone-signaling/`.
TURN listens on the host at `talk.turn_port` (default 3478). **Open that port
for both TCP and UDP in the firewall**, otherwise calls fail for people behind NAT.

## Signing in

- **With Kanidm (engine):** the login page sends people straight to Kanidm.
- **Local administrator:** user `nextcloud.admin_user` (default `admin`). The
  password is `NEXTCLOUD_ADMIN_PASSWORD` in `.nextcloud-easy-deploy/secrets.yaml`.
  When Kanidm sign-in is on, use `https://<domain>/login?direct=1`. Keep this
  account for break-glass access.

To keep local accounts only, set `auth.oidc.managed: false`. To configure Mail
accounts by hand, set `mail.managed: false`.

## Operations

```bash
bash start.sh | bash stop.sh           # containers, with the files apply rendered
bash update.sh                         # git pull; apply only when something changed
bash scripts/compose.sh logs -f nextcloud
bash scripts/maintenance.sh on|off     # Nextcloud maintenance mode
docker exec -u www-data nextcloud php occ <command>
```

## Backups

Backups use the shared easydeploy-lib Borg pipeline. Configure them in the
`backup:` block of `deploy.yaml`. A backup holds:

- `deploy.yaml` and `.nextcloud-easy-deploy/` (including the secrets)
- `<data_dir>/html` (code, `config.php`, and apps)
- `<data_dir>/data` (user files)
- a `pg_dump` of the database

```bash
bash backup.sh                 # hot backup (the database dump is consistent)
bash backup.sh --cold          # maintenance mode while staging: files and database match exactly
bash backup.sh --export /root/nextcloud.tar.gz --encrypt
bash restore.sh --latest       # or --archive NAME, or --file PATH
bash bootstrap-from-backup.sh /root/nextcloud.tar.gz   # fresh VPS
```

Restore puts files back first. Then it starts only PostgreSQL
(`scripts/start-postgres.sh`), loads the dump, and re-applies.

## Development

```bash
uv sync --dev
uv run pytest
```

The tests include `docker compose config` checks of the merged compose model.
They are skipped when Docker is not installed.
