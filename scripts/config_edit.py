#!/usr/bin/env python3
"""Read and write deploy.yaml for the Nextcloud wizard."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEPLOY_PATH = PROJECT_ROOT / "deploy.yaml"


def load_or_init(path: Path = DEFAULT_DEPLOY_PATH) -> dict:
    if not path.exists():
        example = PROJECT_ROOT / "deploy.yaml.example"
        if example.is_file():
            with example.open() as handle:
                return yaml.safe_load(handle) or {}
        return {}
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("deploy.yaml root must be a mapping")
    return data


def _read_section(env_var: str, name: str) -> dict:
    raw = str(os.environ.get(env_var) or "").strip()
    if not raw or not Path(raw).is_file():
        return {}
    data = yaml.safe_load(Path(raw).read_text()) or {}
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def suggested_base_domain() -> str:
    """Organisation domain from sibling kits the engine wizard configured first."""
    email_domain = str(_read_section("EASYDEPLOY_STALWART_DEPLOY", "stalwart").get("domain") or "").strip()
    if email_domain:
        return email_domain
    idm = str(_read_section("EASYDEPLOY_KANIDM_DEPLOY", "kanidm").get("domain") or "").strip()
    if idm.count(".") >= 2:
        return idm.split(".", 1)[1]
    return idm


def save(path: Path, data: dict) -> None:
    with path.open("w") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False)


def update_from_wizard(
    *,
    domain: str,
    data_dir: str,
    office_enabled: bool,
    office_domain: str,
    talk_enabled: bool,
    proxy_mode: str,
    path: Path = DEFAULT_DEPLOY_PATH,
) -> None:
    config = load_or_init(path)

    nextcloud = config.setdefault("nextcloud", {})
    nextcloud["domain"] = domain
    nextcloud["data_dir"] = data_dir.rstrip("/")
    nextcloud.setdefault("image", "docker.io/library/nextcloud")
    nextcloud.setdefault("tag", "34.0.5-apache")
    nextcloud.setdefault("admin_user", "admin")

    office = config.setdefault("office", {})
    office["enabled"] = office_enabled
    office["domain"] = office_domain

    talk = config.setdefault("talk", {})
    talk["enabled"] = talk_enabled
    talk.setdefault("turn_port", 3478)

    config["proxy"] = {
        "type": "caddy",
        "mode": proxy_mode,
        "integrate": {"network": "easydeploy-net"},
    }
    save(path, config)
