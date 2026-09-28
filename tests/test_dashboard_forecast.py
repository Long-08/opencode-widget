"""Phase 4: run the Forecast dashboard Node suite plus static guards.

Mirrors tests/test_dashboard_core.py: skip cleanly when node is unavailable and
run `node --test tests/js/dashboard_forecast.test.js` from the project root. The
static half guards the index.html wiring (ordered <script>, nav button, panel
root) and the forecast module's mandated disclaimer / forbidden labels.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
INDEX_HTML = os.path.join(APP_DIR, "index.html")
FORECAST_JS = os.path.join(APP_DIR, "dashboard", "forecast.js")
FORECAST_TEST = os.path.join("tests", "js", "dashboard_forecast.test.js")

DISCLAIMER = (
    "Forecasts are estimates based on recent observed usage and official quota state. "
    "They are not guaranteed future outcomes."
)
FORBIDDEN_LABELS = ("official prediction",)
CERTAINTY_WORDS = ("certain", "guaranteed", "必然")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _index():
    return _read(INDEX_HTML)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_dashboard_forecast_node_suite():
    proc = subprocess.run(
        ["node", "--test", FORECAST_TEST],
        cwd=PROJECT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    assert proc.returncode == 0


def test_index_loads_forecast_between_sessions_and_app():
    src = _index()
    found = re.findall(r"<script[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>", src, re.I)
    assert "dashboard/forecast.js" in found, found
    assert found.index("dashboard/sessions.js") < found.index("dashboard/forecast.js")
    assert found.index("dashboard/forecast.js") < found.index("app.js")
    assert found[-1] == "app.js", found


def test_index_has_forecast_nav_button_and_panel():
    src = _index()
    assert 'data-tab="forecast"' in src
    assert re.search(r'<button[^>]*data-tab="forecast"[^>]*aria-label="Forecast"', src)
    assert 'Forecast</button>' in src
    assert 'data-panel="forecast"' in src
    assert 'id="obsForecastView"' in src


def test_forecast_module_contains_disclaimer():
    src = _read(FORECAST_JS)
    flat = re.sub(r"\s+", " ", src)
    assert "Forecasts are estimates" in flat
    assert "recent observed usage and official quota state" in flat
    assert "not guaranteed future outcomes" in flat


def test_forecast_module_has_no_forbidden_or_certainty_labels():
    src = _read(FORECAST_JS)
    for bad in FORBIDDEN_LABELS:
        assert bad not in src, "forbidden label present: %s" % bad
    # The disclaimer deliberately says "not guaranteed"; everything else must be
    # free of certainty wording.
    scan = src.replace("not guaranteed", "")
    lowered = scan.lower()
    for word in CERTAINTY_WORDS:
        assert word.lower() not in lowered, "certainty wording present: %s" % word


def test_forecast_module_does_not_call_the_api():
    src = _read(FORECAST_JS)
    # forecast data must load only through the core orchestration (no direct fetch here)
    assert "apiGetForecast" not in src


def test_core_hides_range_control_on_forecast_without_mutating_range():
    src = _read(os.path.join(APP_DIR, "dashboard", "core.js"))
    assert 'OCW.state.tab === "forecast"' in src
    assert "rangeBox.style.display" in src
    # The stored range is assigned in exactly one place (obsSelectRange); the
    # forecast tab must not mutate it, so Agents @ 7d -> Forecast -> Agents
    # still reads 7d.
    assert src.count("OCW.state.range =") == 1
