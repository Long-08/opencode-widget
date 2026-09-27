"""Phase-1 baseline: gw.norm_model / gw.is_free_model / gw.register_provider_prefix.

No production code is modified. Tests are self-contained and rely only on the
shared `gw` fixture + the autouse `isolated_state` isolation.
"""
import pytest


# --------------------------------------------------------------------------
# norm_model: provider-prefix stripping + free-marker unification
# --------------------------------------------------------------------------
def test_provider_prefix_stripped(gw):
    assert gw.norm_model("tencent/hy3:free") == "hy3-free"
    assert gw.norm_model("cohere/north-mini-code:free") == "north-mini-code-free"
    assert gw.norm_model("openrouter/some-model") == "some-model"


def test_free_marker_variants_unified(gw):
    # :free, /free and -free all collapse onto the canonical "-free" form.
    assert gw.norm_model("hy3:free") == "hy3-free"
    assert gw.norm_model("hy3/free") == "hy3-free"
    assert gw.norm_model("hy3-free") == "hy3-free"


def test_unknown_provider_prefix_not_stripped(gw):
    # Only known provider prefixes are peeled off.
    assert gw.norm_model("unknown-provider/model-x") == "unknown-provider/model-x"


def test_kilo_auto_is_model_name_not_prefix(gw):
    # "kilo-auto" is part of the model name, not a provider prefix, so it is kept.
    assert gw.norm_model("kilo-auto/free") == "kilo-auto-free"


def test_register_provider_prefix_dynamic(gw):
    # A newly observed providerID becomes strippable.
    gw.register_provider_prefix("myprov")
    assert gw.norm_model("myprov/model") == "model"


# --------------------------------------------------------------------------
# norm_model: whitelist + aliases
# --------------------------------------------------------------------------
def test_free_whitelist_big_pickle(gw):
    assert gw.norm_model("big-pickle") == "big-pickle"
    assert gw.is_free_model("big-pickle") is True


def test_plain_hy3_is_not_free(gw):
    assert gw.is_free_model("hy3") is False


def test_model_alias_resolution(gw):
    assert (
        gw.norm_model("nemotron-3-ultra-free")
        == "nemotron-3-ultra-550b-a55b-free"
    )


# --------------------------------------------------------------------------
# is_free_model
# --------------------------------------------------------------------------
def test_is_free_model_suffix_forms(gw):
    assert gw.is_free_model("some-model-free") is True
    assert gw.is_free_model("some-model:free") is True
    assert gw.is_free_model("some-model/free") is True


def test_is_free_model_empty_and_plain(gw):
    assert gw.is_free_model("") is False
    assert gw.is_free_model(None) is False
    assert gw.is_free_model("plain-model") is False
