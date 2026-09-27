# Test fixtures

All fixtures here are synthetic. They must never contain real API keys,
auth cookies, workspace IDs, or exported user data.

- `formula_valid.json` — a small, complete cloud-formula-shaped document
  (params + constants + formulas + views) used to exercise `views.FormulaStore`,
  `data_server.apply_params_to_gw()` and `formula_registry.apply_formulas()`.
  Values are deliberately different from production defaults (version 999,
  ratio 2.0, etc.) so tests can detect accidental production-value leakage.
- `formula_invalid.json` — missing `views` key; must be rejected by
  `FormulaStore._validate` and trigger the default-formula fallback.
- `config.example.json` — placeholder config shape. Placeholder strings are
  intentionally not key-shaped/cookie-shaped values.

Disposable SQLite databases (opencode / codex shapes) are built at test time
via `tests/helpers.py` (`build_legacy_opencode_db`, `build_current_opencode_db`)
into pytest `tmp_path` directories, not committed as binaries.
