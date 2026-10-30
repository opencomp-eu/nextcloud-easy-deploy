# Nextcloud (local)

Docker Compose stack: Nextcloud + MariaDB + Collabora CODE + Talk HPB.

Designed for a single-host local/LAN deploy where the browser and containers share the same hostnames (`localhost`).

## Quick start (new server)

```bash
cd apps/nextcloud
cp .env.example .env          # or let setup.sh create it
# Edit NEXTCLOUD_URL / ports / TZ for this host, then:
./setup.sh
```

`setup.sh` will:

1. Create `.env` with random secrets if missing
2. `docker compose up -d`
3. Install/enable Deck, Calendar, Contacts, Mail, Talk, Collabora
4. Wire overwrite URLs, Collabora WOPI, Talk signaling + TURN from `.env`

Open `${NEXTCLOUD_URL}` (default `http://localhost:8082`) and log in with `NEXTCLOUD_ADMIN_*`.

## Configuration

All secrets and host-specific values live in **`.env`** (gitignored). See `.env.example`.

| Variable | Purpose |
|---|---|
| `NEXTCLOUD_URL` / `NEXTCLOUD_PORT` | Browser URL and published port |
| `MYSQL_*` | Database (set before first start; changing later needs a DB reset) |
| `NEXTCLOUD_ADMIN_*` | Admin user (first install only) |
| `COLLABORA_*` | Collabora admin + `server_name` |
| `TURN_SECRET` / `SIGNALING_SECRET` / `INTERNAL_SECRET` | Talk HPB; must match values applied by `setup.sh` |

After changing Talk secrets or URLs, re-run `./setup.sh` (or the Talk/Collabora sections) so Nextcloud stays in sync with the containers.

## Services / ports

| Service | Default |
|---|---|
| Nextcloud | `8082` |
| Collabora | `9980` (host network) |
| Talk signaling | `8181` |
| Talk TURN/STUN | `3478` tcp/udp |

## Day-to-day

```bash
docker compose up -d
docker compose logs -f app
docker compose down          # keeps volumes
docker compose down -v       # wipe data (destructive)
```

## Notes

- Collabora uses **host networking** so it can reach Nextcloud at the same URL as the browser.
- `talk-proxy` (socat) exposes Talk signaling on `localhost:8181` inside the Nextcloud container, matching the browser URL.
- Talk’s backend URL is patched from `https://$NC_DOMAIN` to `$NEXTCLOUD_URL` for local HTTP.
- Mail app TLS verification is on by default; only disable (`occ config:system:set app.mail.verify-tls-peer --type=boolean --value=false`) for self-signed IMAP certs.
