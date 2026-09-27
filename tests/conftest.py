"""Shared fixtures for the opencode-widget Phase-1 test baseline.

Isolation contract (enforced for every test, autouse):
  * NEVER read or write real user data. All data paths are redirected to
    per-test temp directories:
      gw.CONFIG_PATH / gw.OPENCODE_DB / gw.CODEX_LOGS / gw.OPENCODE_AUTH
      ur.REMOTE_DB / sd.DB_PATH
  * No network: tests must not call real HTTP endpoints. The formula store can
    be pointed at local fixture files; network-failure paths are exercised by
    monkeypatching urlopen inside the test.
  * No subprocess: gw.scan_free_models is stubbed to [] by default.
  * No browser access: gw.read_auth_cookie_from_webdata is stubbed to "".
  * Rule-table module globals mutated by apply_params_to_gw() are snapshotted
    and restored after each test.

Test authors: do NOT rely on the real ~/.local/share/opencode or ~/.codex
databases. Build disposable SQLite fixtures with helpers.py.
"""
import copy
import importlib.util
import json
import os
import sys
import threading

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Rule-table globals written by data_server.apply_params_to_gw()
GW_RULE_GLOBALS = (
    "LIMITS",
    "CREDIT_PER_APPLIED",
    "SESSION_MS",
    "WEEK_MS",
    "PROVIDER_SRC",
    "PROVIDER_PREFIXES",
    "MODEL_ALIASES",
    "FREE_WHITELIST",
    "FREE_SUFFIXES",
    "FREE_EXCLUDE",
    "PRICES",
    "REQ_LIMITS",
    "TOKENS_PER_REQ",
    "DISPLAY_NAMES",
    "MODEL_QUOTAS",
    "MODEL_RATES",
    "RATE_DEFAULT",
    "RATE_INTERCEPT",
)

# Filesystem paths pointing at user data
GW_PATH_GLOBALS = ("CONFIG_PATH", "OPENCODE_DB", "CODEX_LOGS", "OPENCODE_AUTH")


def _load_gw():
    spec = importlib.util.spec_from_file_location(
        "gw_under_test", os.path.join(PROJECT_DIR, "go-usage-widget.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _snapshot(mod, names):
    return {n: copy.deepcopy(getattr(mod, n)) for n in names if hasattr(mod, n)}


def _restore(mod, snap):
    for name, value in snap.items():
        setattr(mod, name, value)


@pytest.fixture(scope="session")
def gw():
    """The production calculation module (session-wide instance)."""
    return _load_gw()


@pytest.fixture(scope="session")
def data_server():
    """The HTTP API module. Importing it is side-effect free (main() starts servers)."""
    import data_server as ds

    return ds


@pytest.fixture(scope="session")
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture()
def load_fixture():
    """load_fixture("formula_valid.json") -> parsed JSON."""

    def _load(name):
        with open(os.path.join(FIXTURES_DIR, name), "r", encoding="utf-8") as fh:
            return json.load(fh)

    return _load


@pytest.fixture(autouse=True)
def isolated_state(gw, monkeypatch, tmp_path):
    """Redirect every user-data path; restore mutated module globals afterwards."""
    # --- gw instance used directly by unit tests ---
    monkeypatch.setattr(gw, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(gw, "OPENCODE_DB", str(tmp_path / "opencode.db"))
    monkeypatch.setattr(gw, "CODEX_LOGS", str(tmp_path / "logs_2.sqlite"))
    monkeypatch.setattr(gw, "OPENCODE_AUTH", str(tmp_path / "auth.json"))
    monkeypatch.setattr(gw, "scan_free_models", lambda cfg=None, force=False: [])
    monkeypatch.setattr(gw, "read_auth_cookie_from_webdata", lambda: "")
    gw_rules = _snapshot(gw, GW_RULE_GLOBALS)

    # --- data_server's own gw instance ---
    import data_server as ds

    ds_gw = ds.gw
    monkeypatch.setattr(ds_gw, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(ds_gw, "OPENCODE_DB", str(tmp_path / "opencode.db"))
    monkeypatch.setattr(ds_gw, "CODEX_LOGS", str(tmp_path / "logs_2.sqlite"))
    monkeypatch.setattr(ds_gw, "OPENCODE_AUTH", str(tmp_path / "auth.json"))
    monkeypatch.setattr(ds_gw, "scan_free_models", lambda cfg=None, force=False: [])
    monkeypatch.setattr(ds_gw, "read_auth_cookie_from_webdata", lambda: "")
    ds_rules = _snapshot(ds_gw, GW_RULE_GLOBALS)

    # --- remote / server DB paths ---
    import server_data as sd
    import usage_remote as ur

    ur_db = getattr(ur, "REMOTE_DB", None)
    sd_db = getattr(sd, "DB_PATH", None)
    monkeypatch.setattr(ur, "REMOTE_DB", str(tmp_path / "usage_remote.db"))
    monkeypatch.setattr(sd, "DB_PATH", str(tmp_path / "server_usage.db"))

    # --- data_server module caches ---
    sub_start = copy.deepcopy(ds._SUB_START)
    ds._SUB_START.update({"ms": None, "ts": 0, "last_day": "", "source": "none", "fetched_at": 0})
    cache_state = ds.CACHE["state"]
    cache_ts = ds.CACHE["ts"]
    ds.CACHE["state"] = None
    ds.CACHE["ts"] = 0

    # --- formula_registry active table ---
    import formula_registry as fr

    fr_active = copy.deepcopy(fr._ACTIVE)
    fr_meta = copy.deepcopy(fr._FORMULA_META)

    yield

    _restore(gw, gw_rules)
    _restore(ds_gw, ds_rules)
    if ur_db is not None:
        ur.REMOTE_DB = ur_db
    if sd_db is not None:
        sd.DB_PATH = sd_db
    ds.CACHE["state"] = cache_state
    ds.CACHE["ts"] = cache_ts
    for key, value in sub_start.items():
        ds._SUB_START[key] = value
    fr._ACTIVE = fr_active
    fr._FORMULA_META = fr_meta


@pytest.fixture()
def api_server(data_server):
    """Run data_server.Handler on an ephemeral 127.0.0.1 port (no production ports)."""
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), data_server.Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
