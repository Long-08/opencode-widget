"""Phase 7: remote-formula trust-boundary hardening tests.

Covers ``views.validate_formula`` (strict schema/version/type/range/size checks)
and the hardened ``views.FormulaStore`` (bounded fetch + finite timeout,
last-known-good retention, validated + atomically persisted cache with
"unsigned" provenance).

The remote formula is INERT DATA: it is never executed. No signature
verification is implemented in this phase, so ``integrity_status`` must always
be the literal ``"unsigned"`` — never "signed"/"secured".
"""
import json
import math
import os

import pytest

import views


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _base_formula(**overrides):
    """A minimal formula document that passes validation."""
    formula = {
        "version": 4,
        "params": {
            "limits": {"session": 12.0, "weekly": 30.0, "monthly": 60.0},
            "windows": {"session_ms": 18000000, "week_ms": 604800000},
            "meter": {"ratio": 1.5, "rates": {}, "rate_default": 1.0,
                      "rate_intercept": 0.0},
            "sources": {"paid": ["go"], "subset_of": {"gateway": "go"}},
            "providers": {"src": {"opencode-go": "go"}, "prefixes": ["go"],
                          "aliases": {}},
            "free_models": {"whitelist": [], "suffixes": ["-free"], "exclude": []},
            "prices": {"m": {"in": 1.0, "out": 2.0, "cr": None, "cw": None}},
            "model_quotas": {"m": 60},
            "req_limits": {"m": [1, 2, 3]},
            "tokens_per_req": {"m": 1000},
            "display_names": {"m": "M"},
            "refresh": {"formula_s": 900},
        },
        "constants": {"weeks_per_month": 4.345, "periods_per_day": 6.0},
        "formulas": {},
        "views": [{"id": "all_today", "label": "x", "scope": {"all": True},
                   "range": "today", "group": None, "agg": [], "post": []}],
    }
    formula.update(overrides)
    return formula


def _write(path, formula):
    path.write_text(json.dumps(formula), encoding="utf-8")
    return str(path)


def _fake_response(payload):
    class _Resp:
        def __init__(self, data):
            self._data = data

        def read(self, n=None):
            return self._data if n is None else self._data[:n]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(payload)


# ---------------------------------------------------------------------------
# validate_formula: accept
# ---------------------------------------------------------------------------
def test_validate_default_formula_is_valid():
    res = views.validate_formula(views.DEFAULT_FORMULA)
    assert res["ok"] is True
    assert res["error"] is None
    assert res["schema_version"] == views.SUPPORTED_FORMULA_SCHEMA
    assert res["formula_version"] == views.DEFAULT_FORMULA["version"]


def test_validate_legacy_without_schema_version_is_schema_1():
    formula = _base_formula(version=3)
    assert "schema_version" not in formula
    res = views.validate_formula(formula)
    assert res["ok"] is True
    assert res["schema_version"] == 1
    assert res["formula_version"] == 3


def test_validate_explicit_supported_schema_and_version_window():
    for version in (views.MIN_SUPPORTED_FORMULA_VERSION,
                    views.MAX_SUPPORTED_FORMULA_VERSION):
        res = views.validate_formula(_base_formula(version=version, schema_version=1))
        assert res["ok"] is True, version
        assert res["formula_version"] == version


def test_validate_allows_documented_extensions_bag():
    formula = _base_formula(extensions={"vendor": {"anything": [1, 2, 3]}})
    res = views.validate_formula(formula)
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# validate_formula: reject
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [[], "not-a-dict", 123, None, 4.5])
def test_validate_rejects_non_dict(bad):
    res = views.validate_formula(bad)
    assert res["ok"] is False
    assert res["error"] == "not_a_dict"


def test_validate_rejects_unsupported_schema():
    formula = _base_formula(schema_version=views.SUPPORTED_FORMULA_SCHEMA + 1)
    res = views.validate_formula(formula)
    assert res["ok"] is False
    assert res["error"] == "unsupported_schema"
    assert res["schema_version"] == views.SUPPORTED_FORMULA_SCHEMA + 1


def test_validate_rejects_too_new_version():
    formula = _base_formula(version=views.MAX_SUPPORTED_FORMULA_VERSION + 1)
    res = views.validate_formula(formula)
    assert res["ok"] is False
    assert res["error"] == "too_new"
    assert res["formula_version"] == views.MAX_SUPPORTED_FORMULA_VERSION + 1


def test_validate_rejects_too_old_version():
    formula = _base_formula(version=views.MIN_SUPPORTED_FORMULA_VERSION - 1)
    res = views.validate_formula(formula)
    assert res["ok"] is False
    assert res["error"] == "too_old"


@pytest.mark.parametrize("bad_version", ["4", 4.0, True, None])
def test_validate_rejects_non_int_version(bad_version):
    res = views.validate_formula(_base_formula(version=bad_version))
    assert res["ok"] is False
    assert res["error"] == "bad_version"


def test_validate_rejects_missing_params_or_views():
    no_views = _base_formula()
    del no_views["views"]
    assert views.validate_formula(no_views)["error"] == "missing_views"

    no_params = _base_formula()
    del no_params["params"]
    assert views.validate_formula(no_params)["error"] == "missing_params"


def test_validate_rejects_bad_formulas_type():
    res = views.validate_formula(_base_formula(formulas=[]))
    assert res["ok"] is False
    assert res["error"] == "bad_formulas"


def test_validate_rejects_unknown_top_level_field():
    res = views.validate_formula(_base_formula(surprise=1))
    assert res["ok"] is False
    assert res["error"] == "unknown_field"


def test_validate_rejects_bad_extensions_type():
    res = views.validate_formula(_base_formula(extensions=[]))
    assert res["ok"] is False
    assert res["error"] == "bad_extensions"


@pytest.mark.parametrize("bad_limits", [
    {"session": -1.0},
    {"session": float("nan")},
    {"session": float("inf")},
    {"session": "12"},
    {"session": True},
    "not-a-dict",
])
def test_validate_rejects_bad_limits(bad_limits):
    res = views.validate_formula(_base_formula(params={"limits": bad_limits}))
    assert res["ok"] is False
    assert res["error"] == "bad_limits"


@pytest.mark.parametrize("bad_windows", [
    {"session_ms": 0},
    {"session_ms": -1},
    {"session_ms": 1.5},
    {"session_ms": "18000000"},
    "not-a-dict",
])
def test_validate_rejects_bad_windows(bad_windows):
    res = views.validate_formula(_base_formula(params={"windows": bad_windows}))
    assert res["ok"] is False
    assert res["error"] == "bad_windows"


@pytest.mark.parametrize("bad_meter", [
    {"ratio": float("nan")},
    {"ratio": float("-inf")},
    {"rate_default": "1.0"},
    {"rate_intercept": None},
    {"rates": {"m": "x"}},
    {"rates": {"m": float("nan")}},
    "not-a-dict",
])
def test_validate_rejects_bad_meter(bad_meter):
    res = views.validate_formula(_base_formula(params={"meter": bad_meter}))
    assert res["ok"] is False
    assert res["error"] == "bad_meter"


@pytest.mark.parametrize("bad_sources", [
    {"paid": "go"},
    {"paid": ["go", 3]},
    {"subset_of": {"gateway": 1}},
    {"subset_of": ["gateway"]},
    "not-a-dict",
])
def test_validate_rejects_bad_sources(bad_sources):
    res = views.validate_formula(_base_formula(params={"sources": bad_sources}))
    assert res["ok"] is False
    assert res["error"] == "bad_sources"


@pytest.mark.parametrize("bad_providers", [
    {"src": {"opencode-go": 1}},
    {"src": ["opencode-go"]},
    {"prefixes": ["go", 7]},
    {"prefixes": "go"},
    {"aliases": {"a": 2}},
    "not-a-dict",
])
def test_validate_rejects_bad_providers(bad_providers):
    res = views.validate_formula(_base_formula(params={"providers": bad_providers}))
    assert res["ok"] is False
    assert res["error"] == "bad_providers"


@pytest.mark.parametrize("bad_free", [
    {"whitelist": "big-pickle"},
    {"suffixes": ["-free", 9]},
    "not-a-dict",
])
def test_validate_rejects_bad_free_models(bad_free):
    res = views.validate_formula(_base_formula(params={"free_models": bad_free}))
    assert res["ok"] is False
    assert res["error"] == "bad_free_models"


@pytest.mark.parametrize("bad_prices", [
    {"m": {"in": -1.0}},
    {"m": {"in": float("nan")}},
    {"m": {"in": float("inf")}},
    {"m": "not-a-dict"},
    "not-a-dict",
])
def test_validate_rejects_bad_prices(bad_prices):
    res = views.validate_formula(_base_formula(params={"prices": bad_prices}))
    assert res["ok"] is False
    assert res["error"] == "bad_prices"


def test_validate_accepts_prices_none_and_nonnegative_numbers():
    prices = {"m": {"in": 0.5, "out": 1.0, "cr": None, "cw": None, "hi_above": 100}}
    res = views.validate_formula(_base_formula(params={"prices": prices}))
    assert res["ok"] is True


@pytest.mark.parametrize("key,bad", [
    ("model_quotas", {"m": -1}),
    ("model_quotas", {"m": float("nan")}),
    ("model_quotas", "not-a-dict"),
    ("req_limits", {"m": [1, -2]}),
    ("req_limits", {"m": "nope"}),
    ("req_limits", "not-a-dict"),
    ("tokens_per_req", {"m": 0}),
    ("tokens_per_req", {"m": -5}),
    ("tokens_per_req", "not-a-dict"),
])
def test_validate_rejects_bad_quota_sections(key, bad):
    res = views.validate_formula(_base_formula(params={key: bad}))
    assert res["ok"] is False


def test_validate_rejects_bad_display_names():
    res = views.validate_formula(
        _base_formula(params={"display_names": {"m": 1}}))
    assert res["ok"] is False
    assert res["error"] == "bad_display_names"


@pytest.mark.parametrize("bad_refresh", [0, -1, float("nan"), "900",
                                         {"formula_s": 0}, {"formula_s": "x"}])
def test_validate_rejects_bad_refresh(bad_refresh):
    res = views.validate_formula(_base_formula(params={"refresh": bad_refresh}))
    assert res["ok"] is False
    assert res["error"] == "bad_refresh"


def test_validate_accepts_scalar_and_dict_refresh():
    assert views.validate_formula(
        _base_formula(params={"refresh": 900}))["ok"] is True
    assert views.validate_formula(
        _base_formula(params={"refresh": {"formula_s": 900}}))["ok"] is True


@pytest.mark.parametrize("bad_constants", [
    {"weeks_per_month": float("nan")},
    {"weeks_per_month": float("inf")},
    {"weeks_per_month": "4.345"},
    {"weeks_per_month": [4.345]},
    "not-a-dict",
])
def test_validate_rejects_bad_constants(bad_constants):
    res = views.validate_formula(_base_formula(constants=bad_constants))
    assert res["ok"] is False
    assert res["error"] == "bad_constants"


def test_validate_allows_constants_string_note():
    assert views.validate_formula(
        _base_formula(constants={"weeks_per_month": 4.0, "note": "meta"}))["ok"]


@pytest.mark.parametrize("bad_views", ["nope", {"id": "x"}, [1, 2]])
def test_validate_rejects_bad_views(bad_views):
    res = views.validate_formula(_base_formula(views=bad_views))
    assert res["ok"] is False
    assert res["error"] == "bad_views"


# ---------------------------------------------------------------------------
# validate_formula: size caps
# ---------------------------------------------------------------------------
def test_validate_rejects_oversized_string():
    formula = _base_formula(source_note="x" * (views.MAX_FORMULA_STRING_LEN + 1))
    res = views.validate_formula(formula)
    assert res["ok"] is False
    assert res["error"] == "oversized_string"


def test_validate_rejects_oversized_list():
    formula = _base_formula()
    formula["views"] = [{"id": "v"}] * (views.MAX_FORMULA_LIST_LEN + 1)
    res = views.validate_formula(formula)
    assert res["ok"] is False
    assert res["error"] == "oversized_list"


def test_validate_rejects_oversized_dict():
    formula = _base_formula(
        extensions={f"k{i}": i for i in range(views.MAX_FORMULA_DICT_KEYS + 1)})
    res = views.validate_formula(formula)
    assert res["ok"] is False
    assert res["error"] == "oversized_dict"


# ---------------------------------------------------------------------------
# FormulaStore: bounded fetch
# ---------------------------------------------------------------------------
def test_fetch_enforces_size_cap(tmp_path):
    # A syntactically valid but oversized document must be rejected before
    # parsing (read cap = MAX_FORMULA_BYTES).
    huge = _base_formula(source_note="x" * (views.MAX_FORMULA_BYTES + 16))
    path = tmp_path / "huge.json"
    path.write_text(json.dumps(huge), encoding="utf-8")
    store = views.FormulaStore(url=str(path))
    formula = store.get()
    assert formula is views.DEFAULT_FORMULA
    assert store.meta()["error"] is not None


def test_fetch_uses_finite_timeout(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _fake_response(json.dumps(_base_formula(version=5)).encode("utf-8"))

    monkeypatch.setattr(views.urllib.request, "urlopen", _fake_urlopen)
    store = views.FormulaStore(url="https://formula.invalid/f.json")
    assert store.get()["version"] == 5
    assert captured["timeout"] == views.FORMULA_FETCH_TIMEOUT_S
    assert math.isfinite(captured["timeout"])


def test_fetch_bad_url_never_raises(tmp_path):
    store = views.FormulaStore(url=str(tmp_path / "missing.json"))
    assert store.get() is views.DEFAULT_FORMULA
    assert store.meta()["error"] is not None


# ---------------------------------------------------------------------------
# FormulaStore: last-known-good (never cleared)
# ---------------------------------------------------------------------------
def test_store_keeps_lkg_when_refresh_turns_invalid(tmp_path):
    path = tmp_path / "f.json"
    _write(path, _base_formula(version=6))
    store = views.FormulaStore(url=str(path))
    assert store.get()["version"] == 6

    _write(path, _base_formula(version=views.MAX_SUPPORTED_FORMULA_VERSION + 1))
    retained = store.get(force=True)
    assert retained["version"] == 6
    meta = store.meta()
    assert meta["source"] == "cloud"
    assert meta["error"] is not None
    assert meta["validation_status"] == "too_new"


def test_store_keeps_lkg_when_source_disappears(tmp_path):
    path = tmp_path / "f.json"
    _write(path, _base_formula(version=6))
    store = views.FormulaStore(url=str(path))
    assert store.get()["version"] == 6

    os.remove(str(path))
    retained = store.get(force=True)
    assert retained["version"] == 6
    assert store.meta()["error"] is not None


# ---------------------------------------------------------------------------
# FormulaStore: validated + atomic cache with provenance
# ---------------------------------------------------------------------------
def test_store_persists_and_reloads_validated_cache(tmp_path):
    cache = tmp_path / "cache.json"
    src = tmp_path / "f.json"
    _write(src, _base_formula(version=5))

    first = views.FormulaStore(url=str(src), cache_path=str(cache))
    assert first.get()["version"] == 5
    assert cache.exists()
    wrapper = json.loads(cache.read_text(encoding="utf-8"))
    assert wrapper["integrity_status"] == "unsigned"
    assert wrapper["schema_version"] == views.SUPPORTED_FORMULA_SCHEMA
    assert wrapper["formula_version"] == 5
    assert wrapper["formula"]["version"] == 5
    assert isinstance(wrapper["loaded_at"], int)

    # A fresh store whose URL is gone still adopts the validated cache.
    second = views.FormulaStore(
        url=str(tmp_path / "gone.json"), cache_path=str(cache))
    assert second.get()["version"] == 5
    assert second.meta()["source"] == "cache"
    assert second.meta()["integrity_status"] == "unsigned"


def test_store_cache_write_is_atomic_and_tmp_is_ignored(tmp_path):
    cache = tmp_path / "cache.json"
    src = tmp_path / "f.json"
    _write(src, _base_formula(version=5))

    # Leftover .tmp garbage must never be consulted.
    tmp_leftover = str(cache) + ".tmp"
    with open(tmp_leftover, "w", encoding="utf-8") as fh:
        fh.write("not-json{{{")

    store = views.FormulaStore(url=str(src), cache_path=str(cache))
    assert store.get()["version"] == 5
    # Successful persistence cleans up its tmp file (os.replace semantics).
    assert not os.path.exists(tmp_leftover)

    # A second store loads the cache despite a leftover .tmp beside it.
    with open(tmp_leftover, "w", encoding="utf-8") as fh:
        fh.write("stale")
    reloaded = views.FormulaStore(
        url=str(tmp_path / "gone.json"), cache_path=str(cache))
    assert reloaded.get()["version"] == 5
    assert reloaded.meta()["source"] == "cache"


def test_store_ignores_invalid_cached_payload(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({
        "integrity_status": "unsigned",
        "formula": _base_formula(version=views.MAX_SUPPORTED_FORMULA_VERSION + 1),
    }), encoding="utf-8")

    store = views.FormulaStore(
        url=str(tmp_path / "gone.json"), cache_path=str(cache))
    assert store.get() is views.DEFAULT_FORMULA
    assert store.meta()["source"] == "default"


def test_store_cache_write_failure_never_blocks_adoption(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    src = tmp_path / "f.json"
    _write(src, _base_formula(version=5))

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(views.os, "replace", _boom)
    store = views.FormulaStore(url=str(src), cache_path=str(cache))
    # Adoption still happens even though persistence failed (best-effort).
    assert store.get()["version"] == 5
    assert store.meta()["source"] == "cloud"


def test_cached_formula_cold_start_does_not_touch_network(tmp_path, monkeypatch):
    # Phase 7 startup fix: with a valid persisted LKG, the first (non-forced)
    # get() must serve it without a synchronous network refresh.
    cache = tmp_path / "cache.json"
    src = tmp_path / "f.json"
    _write(src, _base_formula(version=5))
    views.FormulaStore(url=str(src), cache_path=str(cache)).get()
    assert cache.exists()

    calls = []

    def _record(req, timeout=None):
        calls.append(timeout)
        return _fake_response(json.dumps(_base_formula(version=6)).encode("utf-8"))

    monkeypatch.setattr(views.urllib.request, "urlopen", _record)

    cold = views.FormulaStore(url="https://formula.invalid/f.json",
                              cache_path=str(cache))
    formula = cold.get()
    assert formula["version"] == 5
    assert cold.meta()["source"] == "cache"
    assert calls == []          # cold start did not block on the network

    # An explicit force still refreshes from the network.
    forced = cold.get(force=True)
    assert forced["version"] == 6
    assert cold.meta()["source"] == "cloud"
    assert calls == [views.FORMULA_FETCH_TIMEOUT_S]


# ---------------------------------------------------------------------------
# FormulaStore: opt-out + provenance
# ---------------------------------------------------------------------------
def test_disabled_store_never_fetches(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("network must not be called when disabled")

    monkeypatch.setattr(views.urllib.request, "urlopen", _boom)
    store = views.FormulaStore(url="https://formula.invalid/f.json",
                               enabled=False, cache_path=None)
    assert store.get() is views.DEFAULT_FORMULA
    meta = store.meta()
    assert meta["source"] == "disabled"
    assert meta["validation_status"] == "disabled"
    assert meta["integrity_status"] == "unsigned"


def test_meta_exposes_hardening_provenance(tmp_path):
    path = tmp_path / "f.json"
    _write(path, _base_formula(version=5))
    store = views.FormulaStore(url=str(path))
    assert store.get()["version"] == 5
    meta = store.meta()
    assert meta["schema_version"] == views.SUPPORTED_FORMULA_SCHEMA
    assert meta["formula_version"] == 5
    assert isinstance(meta["loaded_at"], int)
    assert meta["validation_status"] == "ok"
    assert meta["integrity_status"] == "unsigned"
    # legacy keys preserved
    assert meta["source"] == "cloud"
    assert meta["enabled"] is True
    assert meta["fallback"] is False
    assert meta["hash"]
    assert "last_updated" in meta


def test_network_failure_after_good_fetch_keeps_good(monkeypatch):
    good = json.dumps(_base_formula(version=5)).encode("utf-8")
    calls = {"n": 0}

    def _urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_response(good)
        raise OSError("network down")

    monkeypatch.setattr(views.urllib.request, "urlopen", _urlopen)
    store = views.FormulaStore(url="https://formula.invalid/f.json")
    assert store.get()["version"] == 5

    retained = store.get(force=True)
    assert retained["version"] == 5
    assert store.meta()["source"] == "cloud"
    assert store.meta()["error"] is not None


# ---------------------------------------------------------------------------
# data_server /api/formula surface
# ---------------------------------------------------------------------------
def test_api_formula_exposes_hardening_provenance(
    api_server, api_token, data_server, fixtures_dir, monkeypatch, tmp_path
):
    import helpers

    monkeypatch.setenv("OPENCODE_WIDGET_DATA_DIR", str(tmp_path / "data"))
    data_server.gw.save_config({
        "formula_url": os.path.join(fixtures_dir, "formula_valid.json"),
        "formula_enabled": True,
    })
    status, data = helpers.http_get_json(
        api_server + "/api/formula", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["integrity_status"] == "unsigned"
    assert data["validation_status"] == "ok"
    assert data["schema_version"] == views.SUPPORTED_FORMULA_SCHEMA
    assert data["formula_version"] == 6
    assert isinstance(data["loaded_at"], int)
    # existing keys still present (additive change only)
    assert data["source"] == "cloud"
    assert data["enabled"] is True
    assert data["fallback"] is False
