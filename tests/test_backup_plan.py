from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "easydeploy-lib" / "python"))

import backup_plan as lib_backup_plan  # noqa: E402

from scripts import update  # noqa: E402


def _kit(tmp_path: Path, data_dir: str) -> Path:
    shutil.copytree(PROJECT_ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "deploy.yaml").write_text(yaml.safe_dump({"nextcloud": {"data_dir": data_dir}}))
    return tmp_path


def test_plan_passes_lib_validation_and_follows_data_dir(tmp_path):
    plan = lib_backup_plan.load_plan(_kit(tmp_path, "/srv/nc"))
    paths = {entry["as"]: entry["path"] for entry in plan["persistent_paths"]}
    assert paths["data/html"] == "/srv/nc/html"
    assert paths["data/files"] == "/srv/nc/data"
    assert "/srv/nc/postgres" not in paths.values()
    (db,) = plan["databases"]
    assert (db["container"], db["db_user"], db["admin_user"]) == ("nextcloud_postgres", "nextcloud", "postgres")
    assert db["role_password_secret"] == "NEXTCLOUD_DB_PASSWORD"
    assert db["admin_password_secret"] == "POSTGRES_ADMIN_PASSWORD"
    assert plan["hooks"]["postgres_start"] == "scripts/start-postgres.sh"
    assert (PROJECT_ROOT / plan["hooks"]["postgres_start"]).is_file()


def test_update_spec_tracks_every_container(tmp_path):
    spec = update.update_spec(tmp_path)
    assert spec.compose_projects == ("nextcloud-easy-deploy",)
    assert {"nextcloud", "nextcloud_postgres", "nextcloud_collabora", "nextcloud_talk",
            "nextcloud_caddy"} <= set(spec.extra_containers)
