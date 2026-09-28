# Phase 4 — Explainable Usage Forecasting

> Deterministic, reset-aware rate extrapolation over **observed** usage and **official** quota
> state. Every forecast output is typed `estimate` and never presented as official or guaranteed.
> Official quota usage and raw OpenCode message cost remain strictly separate bases.

## 1. Scope

In: 5 h session / weekly / subscription-period burn rates (multiple windows), reset-aware
time-to-limit, projection to window end, `/api/forecast`, the Forecast dashboard tab, secure
bridge method.

Out: per-agent/model/provider forecasts, cross-account forecasts, ML/statistical models,
confidence scores, notifications/tray alerts (Phase 6), charts (5B), any change to meter ratio /
credits / quota limits / official sync / account split / Raw Cost semantics.

## 2. Observed vs Official vs Estimated

| Kind | Meaning |
|---|---|
| OBSERVED | Real usage that already happened (local OpenCode usage rows). |
| OFFICIAL | Quota state synced from opencode.ai (used/remaining/limit via `quota_snapshot` pct + `fetched_at`, reset via the official reset text). |
| ESTIMATED | Future values extrapolated from an observed rate. Always `type: "estimate"`. |

Official state is the **first fact source** for used/remaining/limit. The forecast never
re-derives official `used` from raw cost; raw (rated) usage only ever feeds a *rate*.

## 3. Forecast basis

`"basis": "official_quota_usage_rate"` — rates are computed on **rated** usage
(`cost × rate_for(model, src)`) over rows counted exactly like `build_windows`
(`cost > 0`, `src not in SUBSET_SRCS`, `account != "other"`), so the rate is expressed in the
same quota units as the official used/remaining values. This is documented and distinct from
`opencode_message_raw` (Raw Cost in the rest of the dashboard); the two are never combined.

## 4. 5 h session algorithms

Windows: `last_30m` (30 min), `last_60m` (60 min), `window_avg` (5 h rolling).
For each: `compute_rate(records, now - window, now, min_coverage, min_points)` →
`usage_per_hour = usage / coverage_hours` (coverage = time from the first in-window record to
now, capped to the window). Each rate gets a reset-aware `compute_time_to_limit`.

## 5. Weekly algorithms

Windows: `last_24h`, `last_3d`, `last_7d` → `usage_per_day = usage_per_hour × 24`.
Each estimate = time-to-limit **plus** projection to the weekly reset
(`projected_usage_at_reset`, `projected_remaining_at_reset`, `would_exceed_limit`).

## 6. Subscription-period algorithms

Windows: `daily_average` (from the subscription anchor `period_start` to now),
`recent_3d_average`, `recent_7d_average`. Each estimate = projection to the period end
(`projected_usage_at_end`, `projected_remaining_at_end`, `would_exceed_limit`).
Period projections deliberately do not compute a separate time-to-limit.

## 7. Sampling thresholds

Named constants in `forecasting.py:SAMPLING` (no scattered magic numbers):

| Window | min coverage | min points |
|---|---|---|
| last_30m | 10 min | 2 |
| last_60m | 20 min | 2 |
| window_avg (5 h) | 30 min | 2 |
| last_24h | 6 h | 2 |
| last_3d | 24 h | 3 |
| last_7d | 48 h | 3 |
| daily_average | 24 h | 2 |
| recent_3d_average | 24 h | 3 |
| recent_7d_average | 48 h | 3 |

Below the threshold → `insufficient_data` with `usage_per_hour = null` (never a faked rate).

## 8. Status semantics

Rates: `ok` (finite rate), `no_usage` (`usage == 0`, rate `0.0`), `insufficient_data`
(null rate). Estimates: `unavailable` (official missing), `already_at_limit` (remaining ≤ 0,
time-to-limit 0), `no_usage` (rate 0), `insufficient_data` (rate null), `reset_unknown`
(reset time missing/invalid), `reset_before_limit`, `limit_before_reset`.
Projections: `ok` / `no_usage` / `insufficient_data` (+ `would_exceed_limit`). Never `NaN`/`Infinity`.

## 9. Reset-before-limit logic

Given `remaining`, rate, and `reset_at`: `ttl = remaining / rate`, `limit_at = now + ttl`.
If `limit_at >= reset_at` → **`reset_before_limit`** (`time_to_limit_hours = null`, but
`raw_time_to_limit_hours` keeps the raw math). Only if `limit_at < reset_at` is the status
`limit_before_reset` with `time_to_limit_hours = ttl`. Missing/invalid reset → `reset_unknown`.

## 10. API contract

`GET /api/forecast` (authenticated; no `range` parameter):

```json
{
  "type": "estimate",
  "generated_at": 0,
  "basis": "official_quota_usage_rate",
  "reader": {"schema": "current", "status": "ok", "row_count": 0, "skipped_rows": 0, "error": null},
  "session": {"official": {...}, "rates": {"last_30m": R, "last_60m": R, "window_avg": R},
              "estimates": {"last_30m": E, "last_60m": E, "window_avg": E}},
  "weekly":  {"official": {...}, "rates": {"last_24h": R, "last_3d": R, "last_7d": R},
              "estimates": {"last_24h": E, "last_3d": E, "last_7d": E}},
  "period":  {"official": {...}, "rates": {"daily_average": R, "recent_3d_average": R, "recent_7d_average": R},
              "estimates": {"daily_average": P, "recent_3d_average": P, "recent_7d_average": P}}
}
```

- `official` = `{"status": "ok"|"unavailable", "used", "remaining", "limit", "reset_at", "reset_source"}` where `reset_source` is `official_reset_text` | `local_window_estimate` | `unknown`.
- `R` = `{status, usage, duration_hours, usage_per_hour, usage_per_day, sample_points, first_ts, last_ts, window_ms}`.
- `E` = `{status, time_to_limit_hours, raw_time_to_limit_hours, estimated_limit_at, reset_at, projected_usage_at_reset, projected_remaining_at_reset, would_exceed_limit}`.
- `P` = `{status, projected_usage_at_end, projected_remaining_at_end, would_exceed_limit, period_end_at}`.
- Single DB read per request; no backend cache in v1.

## 11. UI behavior

- New **Forecast** tab (5th) in the large dashboard; three sections: **5h Session**, **Weekly**,
  **Current Period**, each showing official used/remaining/reset, all three rate windows
  (all results shown, never one "best"), and estimates with explicit `/h` and `/day` units,
  `—` for nulls, and formatted durations.
- Disclaimer at the top: *Forecasts are estimates based on recent observed usage and official
  quota state. They are not guaranteed future outcomes.* Status labels use hedged wording
  ("Estimated to …"); no "guaranteed"/"certain"/"official prediction".
- Lazy: no `/api/forecast` until the tab is first activated. Cache: single `forecast` key in the
  shared `dashCache` (range-independent). Refresh only refreshes the Forecast tab.
- The range selector is hidden/disabled while Forecast is active and **never mutates the stored
  range**: Agents at `7d` → Forecast → back to Agents stays `7d`.
- Small/mid layouts unaffected (Forecast exists only in the large dashboard).

## 12. Security

`/api/forecast` uses the Phase 2.1 runtime-token bridge: Host allowlist + Origin policy +
`Authorization: Bearer`; Electron exposes only `widgetAPI.apiGetForecast()`; the main-process
allowlist accepts the bare `/api/forecast` path only. The renderer never sees the token/base and
has no generic fetch bridge; all dynamic strings are escaped.

## 13. Tests

- Suite total: **308 passed / 0 failed / 0 skipped** (`python -m pytest -o addopts="" -q`, TEMP
  redirected) — 257 before + 51 new.
- `tests/test_forecasting.py` (36 pure): rate statuses/zero/single-point/insufficient-coverage/
  multi-record/window clipping/outside ignored; time-to-limit (already at limit, zero burn,
  reset before/after, exactly at reset, missing/invalid reset, unavailable); projection
  (positive/zero rate, end soon, above/below limit, unclamped remaining); `parse_reset_text`;
  `type`/`basis`; no `NaN`/`Infinity` via `json.dumps(..., allow_nan=False)`.
- `tests/test_forecast_api.py` (7): auth required; envelope + `session/weekly/period`;
  `type=estimate`; `basis`; status vocabulary; no secrets; empty DB semantics.
- JS: `tests/js/dashboard_forecast.test.js` (17) + updated `dashboard_core.test.js`;
  `tests/test_dashboard_forecast.py` wrapper + static guards; structure/static/boundary tests
  updated for the 5th tab and the `apiGetForecast` bridge method.
- Smoke (real DB, read-only): server 16/16 PASS (`type=estimate`, basis, 3 rates + 3 estimates
  per window, `official=unavailable` when no official sync exists, no `NaN`/`Infinity`);
  dashboard PASS (5 tabs, Forecast lazy, disclaimer, 3 sections, range hidden + preserved,
  `PAGE_EVENTS: []`, `leaks: []`).
- Timing (uncached, ~1.8k normalized rows): forecast endpoint ≈231 ms.

## 14. Known limitations

1. Forecasts require official quota state for used/remaining; without it the `official` block is
   `unavailable` and time-to-limit statuses become `unavailable`/`insufficient_data` (no fake
   numbers). On a machine with no official sync this is the expected display.
2. Reset times come from the official reset text when parseable, otherwise a documented local
   window estimate (`reset_source`); they are not a guaranteed official timestamp.
3. Rates over sparse data are reported as `insufficient_data` rather than extrapolated.
4. Subscription-period `daily_average` needs a known subscription anchor; otherwise it falls back
   to a 24 h window (still marked).
5. No confidence scores (deliberately), no notifications/alerts (Phase 6), no charts (5B).
6. Regeneration is on-demand (no background forecast cache); the endpoint recomputes per request.
