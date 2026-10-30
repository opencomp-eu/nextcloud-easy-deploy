#!/usr/bin/env python3
"""Dynamic backup plan for nextcloud-easy-deploy (shared backup contract v1).

easydeploy-lib's shared loader (python/backup_plan.py) discovers this module and
calls resolve_plan(project_root). Files follow nextcloud.data_dir; the database
is captured with pg_dump rather than by copying PostgreSQL's data directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

STATE_DIR = ".nextcloud-easy-deploy"
DEFAULT_DATA_DIR = "/var/lib/nextcloud"


def _load_deploy_config(project_root: Path) -> dict[str, Any]:
    deploy_yaml = project_root / "deploy.yaml"
    if not deploy_yaml.is_file():
        return {}
    data = yaml.safe_load(deploy_yaml.read_text()) or {}
    return data if isinstance(data, dict) else {}


def resolve_plan(project_root: Path) -> dict[str, Any]:
    config = _load_deploy_config(Path(project_root))
    data_dir = Path(str((config.get("nextcloud") or {}).get("data_dir") or DEFAULT_DATA_DIR))

    return {
        "service": "nextcloud",
        "archive_prefix": "nextcloud",
        "timer_name": "nextcloud-easy-deploy-backup",
        "state_dir": STATE_DIR,
        "secrets_file": f"{STATE_DIR}/secrets.yaml",
        "hooks": {
            "apply": "apply.sh",
            "start": "start.sh",
            "stop": "stop.sh",
            "postgres_start": "scripts/start-postgres.sh",
        },
        "persistent_paths": [
            {"path": "deploy.yaml", "as": "deploy.yaml"},
            {"path": STATE_DIR, "as": STATE_DIR},
            # Code, config.php (instance secret, passwordsalt) and installed apps.
            {"path": str(data_dir / "html"), "as": "data/html"},
            {"path": str(data_dir / "data"), "as": "data/files"},
        ],
        "docker_volumes": [],
        "databases": [
            {
                "name": "nextcloud",
                "container": "nextcloud_postgres",
                "db_name": "nextcloud",
                "db_user": "nextcloud",
                "admin_user": "postgres",
                "role_password_secret": "NEXTCLOUD_DB_PASSWORD",
                "admin_password_secret": "POSTGRES_ADMIN_PASSWORD",
            }
        ],
    }
