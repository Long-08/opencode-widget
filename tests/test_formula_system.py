"""Tests for views.FormulaStore, data_server.apply_params_to_gw and formula_registry.

Covers cloud formula fetch/cache/fallback, param overlay onto gw rule tables,
and the local-only formula registry (cloud provides metadata, never code).
"""
import copy
import json
import os
import shutil

import pytest

import formula_registry as fr
import views


# ---------------------------------------------------------------------------
# FormulaStore: fetch / cache / fallback
# ---------------------------------------------------------------------------
def test_formula_store_cloud_roundtrip_and_cache(tmp_path, fixtures_dir):
    # Copy the shared fixture: the test rewrites it to prove cache/force semantics
    # without touching tests/fixtures (shared with other lanes).
    path = tmp_path / "formula.json"
    shutil.copyfile(os.path.join(fixtures_dir, "formula_valid.json"), path)

    store = views.FormulaStore(url=str(path))
    f = store.get()
    assert f["version"] == 6
    assert store.meta()["source"] == "cloud"
    assert store.meta()["error"] is None

    # second get() without force stays cached
    assert store.get()["version"] == 6

    # rewriting the file does not affect the cached read ...
    changed = json.loads(path.read_text(encoding="utf-8"))
    # 7 is the top of the supported version window (MIN..MAX); Phase 7 rejects
    # out-of-window versions, so use an in-window value to prove force refresh.
    changed["version"] = 7
    path.write_text(json.dumps(changed), encoding="utf-8")
    assert store.get()["version"] == 6

    # ... until an explicit force refresh picks up the new version
    assert store.get(force=True)["version"] == 7
    assert store.meta()["error"] is None


def test_formula_store_invalid_fixture_falls_back(fixtures_dir):
    # formula_invalid.json has no "views" key -> validation fails -> DEFAULT_FORMULA
    store = views.FormulaStore(url=os.path.join(fixtures_dir, "formula_invalid.json"))
    f = store.get()
    assert f is views.DEFAULT_FORMULA
    assert store.meta()["source"] == "default"
    assert store.meta()["error"] is not None


def test_formula_store_network_failure_falls_back(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(views.urllib.request, "urlopen", _boom)
    store = views.FormulaStore(url="https://example.invalid/formula")
    f = store.get()
    assert f is views.DEFAULT_FORMULA
    assert store.meta()["source"] == "default"
    assert store.meta()["error"] is not None


def test_formula_store_keeps_last_formula_on_refresh_failure(tmp_path, fixtures_dir):
    path = tmp_path / "formula.json"
    shutil.copyfile(os.path.join(fixtures_dir, "formula_valid.json"), path)
    store = views.FormulaStore(url=str(path))
    assert store.get()["version"] == 6

    # File disappears; a forced refresh must silently retain the last good formula
    # while surfacing a non-None error (silent-failure semantics).
    os.remove(path)
    f = store.get(force=True)
    assert f["version"] == 6
    meta = store.meta()
    assert meta["source"] == "cloud"
    assert meta["error"] is not None


@pytest.mark.parametrize("bad", [[], "not-a-dict", 123, None])
def test_validate_rejects_non_dict(bad):
    with pytest.raises(views.FormulaError):
        views.FormulaStore._validate(bad)


@pytest.mark.parametrize("bad", [{"params": {}}, {"views": []}])
def test_validate_rejects_missing_params_or_views(bad):
    with pytest.raises(views.FormulaError):
        views.FormulaStore._validate(bad)


def test_validate_rejects_non_dict_formulas():
    with pytest.raises(views.FormulaError):
        views.FormulaStore._validate({"params": {}, "views": [], "formulas": []})


# ---------------------------------------------------------------------------
# data_server.apply_params_to_gw
# ---------------------------------------------------------------------------
def test_apply_params_to_gw_applies_fixture(data_server, load_fixture):
    dgw = data_server.gw
    before_prefixes = set(dgw.PROVIDER_PREFIXES)
    formula = load_fixture("formula_valid.json")
    params = formula["params"]

    data_server.apply_params_to_gw(formula)

    assert dgw.LIMITS["session"] == 10.0
    assert dgw.LIMITS["weekly"] == 20.0
    assert dgw.LIMITS["monthly"] == 40.0
    assert dgw.CREDIT_PER_APPLIED == 5.0
    assert dgw.SESSION_MS == 1000
    assert dgw.WEEK_MS == 2000
    # prefixes are a union: new entry added, previous entries preserved
    assert "fixtureprov" in dgw.PROVIDER_PREFIXES
    assert before_prefixes <= dgw.PROVIDER_PREFIXES
    # aliases are replaced, not merged
    assert dgw.MODEL_ALIASES == params["providers"]["aliases"]
    assert dgw.PRICES == params["prices"]
    assert dgw.MODEL_QUOTAS == params["model_quotas"]
    assert dgw.REQ_LIMITS == params["req_limits"]
    assert dgw.TOKENS_PER_REQ == params["tokens_per_req"]
    assert dgw.DISPLAY_NAMES == params["display_names"]
    assert dgw.MODEL_RATES == {}
    assert dgw.RATE_DEFAULT == 2.0
    assert dgw.RATE_INTERCEPT == 0.25


def test_apply_params_to_gw_missing_keys_keep_defaults(data_server):
    dgw = data_server.gw
    names = (
        "LIMITS", "CREDIT_PER_APPLIED", "SESSION_MS", "WEEK_MS", "PROVIDER_PREFIXES",
        "MODEL_ALIASES", "PRICES", "MODEL_QUOTAS", "REQ_LIMITS", "TOKENS_PER_REQ",
        "DISPLAY_NAMES", "MODEL_RATES", "RATE_DEFAULT", "RATE_INTERCEPT",
    )
    snap = {n: copy.deepcopy(getattr(dgw, n)) for n in names}

    data_server.apply_params_to_gw({"params": {}})

    for n in names:
        assert getattr(dgw, n) == snap[n], n


def test_apply_params_to_gw_is_noop_for_non_dict(data_server):
    dgw = data_server.gw
    before = copy.deepcopy(dgw.LIMITS)
    before_rates = copy.deepcopy(dgw.MODEL_RATES)

    data_server.apply_params_to_gw(None)
    data_server.apply_params_to_gw("not-a-formula")
    data_server.apply_params_to_gw(123)
    data_server.apply_params_to_gw({"params": "not-a-dict"})
    data_server.apply_params_to_gw({"params": []})

    assert dgw.LIMITS == before
    assert dgw.MODEL_RATES == before_rates


def test_apply_params_to_gw_no_per_key_type_guard_characterization(data_server):
    # CHARACTERIZATION: apply_params_to_gw has no per-key type guard. A non-numeric
    # limits.session is passed straight to float() and raises ValueError. This
    # documents the current robustness gap; it is intentionally NOT fixed here.
    with pytest.raises(ValueError):
        data_server.apply_params_to_gw({"params": {"limits": {"session": "abc"}}})


# ---------------------------------------------------------------------------
# formula_registry
# ---------------------------------------------------------------------------
def test_formula_registry_local_defaults_and_cloud_metadata_merge():
    fr.apply_formulas(None)
    assert fr.compute("model_remain", model_quota=10, model_used=4) == 6
    assert fr.compute("unknown_id") is None
    # dedup_rule has func=None -> no computation
    assert fr.compute("dedup_rule") is None

    # Cloud supplies display/expr metadata only; the local func must NOT be replaced.
    fr.apply_formulas({"formulas": {"tok_agg": {"display": "X", "expr": "evil()"}}})
    assert fr.compute("tok_agg", tokens_in=1, tokens_out=2, tokens_cache=3) == 6
    assert fr.get_formula("tok_agg")["display"] == "X"

    # Removing the cloud metadata restores local defaults.
    fr.apply_formulas(None)
    assert fr.get_formula("tok_agg")["display"] == "token 总量"
