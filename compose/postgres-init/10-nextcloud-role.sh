#!/bin/sh
# First initialisation only. Nextcloud must get a role without CREATEROLE:
# given a superuser, its installer creates its own oc_* role instead, and
# backups and restores would no longer know the credentials.
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres \
	-v pw="${NEXTCLOUD_DB_PASSWORD}" <<'SQL'
CREATE ROLE nextcloud LOGIN PASSWORD :'pw';
CREATE DATABASE nextcloud OWNER nextcloud ENCODING 'UTF8' TEMPLATE template0;
SQL
