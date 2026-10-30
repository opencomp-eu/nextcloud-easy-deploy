from __future__ import annotations

import yaml

from scripts import config_edit


def test_wizard_writes_deploy_yaml(tmp_path):
    path = tmp_path / "deploy.yaml"
    config_edit.update_from_wizard(
        domain="cloud.acme.test", data_dir="/srv/nc/", office_enabled=True,
        office_domain="office.acme.test", talk_enabled=False, proxy_mode="integrate", path=path,
    )
    data = yaml.safe_load(path.read_text())
    assert data["nextcloud"]["domain"] == "cloud.acme.test"
    assert data["nextcloud"]["data_dir"] == "/srv/nc"
    assert data["office"] == {**data["office"], "enabled": True, "domain": "office.acme.test"}
    assert data["talk"]["enabled"] is False
    assert data["proxy"]["mode"] == "integrate"


def test_suggested_base_domain_prefers_stalwart_then_kanidm(tmp_path, monkeypatch):
    stalwart = tmp_path / "stalwart.yaml"
    stalwart.write_text(yaml.safe_dump({"stalwart": {"domain": "acme.test"}}))
    kanidm = tmp_path / "kanidm.yaml"
    kanidm.write_text(yaml.safe_dump({"kanidm": {"domain": "idm.other.test"}}))
    monkeypatch.setenv("EASYDEPLOY_KANIDM_DEPLOY", str(kanidm))
    monkeypatch.setenv("EASYDEPLOY_STALWART_DEPLOY", str(stalwart))
    assert config_edit.suggested_base_domain() == "acme.test"
    monkeypatch.delenv("EASYDEPLOY_STALWART_DEPLOY")
    assert config_edit.suggested_base_domain() == "other.test"
    monkeypatch.delenv("EASYDEPLOY_KANIDM_DEPLOY")
    assert config_edit.suggested_base_domain() == ""
