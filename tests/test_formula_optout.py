"""Phase 2A: formula opt-out / hash provenance tests.

FormulaStore(url, ttl, enabled): when disabled it must not fetch at all and
must hash the canonical DEFAULT_FORMULA. data_server rebuilds the store when
the configured url or the formula_enabled flag changes.
"""
import hashlib
import http.client
import json
import os
import urllib.parse

import helpers
import views

FORMULA_FIXTURE = "formula_valid.json"


def _default_hash():
    canonical = json.dumps(views.DEFAULT_FORMULA, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# FormulaStore
# ---------------------------------------------------------------------------
def test_disabled_store_never_calls_network(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("network must not be called when disabled")

    monkeypatch.setattr(views.urllib.request, "urlopen", _boom)
    store = views.FormulaStore(url="https://example.invalid/formula", enabled=False)
    f = store.get()

    assert f is views.DEFAULT_FORMULA
    meta = store.meta()
    assert meta["source"] == "disabled"
    assert meta["enabled"] is False
    assert meta["fallback"] is True
    assert meta["error"] is None
    assert meta["url"] == "https://example.invalid/formula"
    assert meta["version"] == views.DEFAULT_FORMULA["version"]
    assert meta["hash"] == _default_hash()
    assert isinstance(meta["fetched_at"], int)


def test_enabled_file_hash_and_meta(tmp_path, fixtures_dir):
    path = tmp_path / "formula.json"
    path.write_text(
        open(os.path.join(fixtures_dir, FORMULA_FIXTURE), "r", encoding="utf-8").read(),
        encoding="utf-8",
    )
    store = views.FormulaStore(url=str(path))
    f = store.get()
    assert f["version"] == 999

    # hash the exact string _fetch returns (file is ASCII/LF so == file bytes)
    raw = path.read_text(encoding="utf-8")
    meta = store.meta()
    assert meta["hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert meta["fallback"] is False
    assert meta["enabled"] is True
    assert meta["version"] == 999
    assert meta["last_updated"] == meta["fetched_at"]


def test_enabled_broken_url_falls_back(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(views.urllib.request, "urlopen", _boom)
    store = views.FormulaStore(url="https://example.invalid/formula")
    f = store.get()

    assert f is views.DEFAULT_FORMULA
    meta = store.meta()
    assert meta["source"] == "default"
    assert meta["fallback"] is True
    assert meta["enabled"] is True
    assert meta["error"] is not None
    assert meta["hash"] == _default_hash()


def test_toggle_config_rebuilds_store(data_server, fixtures_dir):
    url = os.path.join(fixtures_dir, FORMULA_FIXTURE)

    data_server.gw.save_config({"formula_url": url, "formula_enabled": True})
    data_server.get_formula()
    store_enabled = data_server._FORMULA_STORE
    assert store_enabled.enabled is True

    # disabling rebuilds the store and switches source to "disabled"
    data_server.gw.save_config({"formula_url": url, "formula_enabled": False})
    data_server.get_formula(force=True)
    store_disabled = data_server._FORMULA_STORE
    assert store_disabled is not store_enabled
    assert store_disabled.enabled is False
    assert store_disabled.meta()["source"] == "disabled"

    # re-enabling rebuilds it again
    data_server.gw.save_config({"formula_url": url, "formula_enabled": True})
    data_server.get_formula(force=True)
    assert data_server._FORMULA_STORE is not store_disabled
    assert data_server._FORMULA_STORE.enabled is True


# ---------------------------------------------------------------------------
# /api/formula exposes provenance fields
# ---------------------------------------------------------------------------
def test_api_formula_disabled_fields(api_server, api_token, data_server, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("network must not be called when disabled")

    # patch urlopen globally (views.urllib.request is urllib.request); use
    # http.client for the request so the test client itself is unaffected
    monkeypatch.setattr(views.urllib.request, "urlopen", _boom)
    data_server.gw.save_config(
        {"formula_url": "http://formula.invalid/f.json", "formula_enabled": False}
    )

    port = urllib.parse.urlparse(api_server).port
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/formula", headers={"Authorization": f"Bearer {api_token}"})
    resp = conn.getresponse()
    status = resp.status
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()

    assert status == 200
    assert data["source"] == "disabled"
    assert data["enabled"] is False
    assert data["fallback"] is True
    assert data["hash"]
    assert "last_updated" in data
    assert data["version"] == views.DEFAULT_FORMULA["version"]


def test_api_formula_enabled_fields(api_server, api_token, data_server, fixtures_dir):
    url = os.path.join(fixtures_dir, FORMULA_FIXTURE)
    data_server.gw.save_config({"formula_url": url, "formula_enabled": True})
    status, data = helpers.http_get_json(
        api_server + "/api/formula", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["enabled"] is True
    assert data["fallback"] is False
    raw = open(url, "r", encoding="utf-8").read()
    assert data["hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert data["version"] == 999
    assert data["last_updated"] == data["fetched_at"]
