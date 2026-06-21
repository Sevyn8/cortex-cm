# Step 6.9.1 pre-flight checks

Date: 2026-05-12
HEAD: 9462e11 modules: retire ROOS from Python vocabulary and seed data

## Question 1 — pytest baseline

Verbatim pytest output (`uv run pytest --tb=no -q | tail -5`):

```
  /home/zorin/ithina-retail/admin-backend/.venv/lib/python3.12/site-packages/pythonjsonlogger/jsonlogger.py:11: DeprecationWarning: pythonjsonlogger.jsonlogger has been moved to pythonjsonlogger.json
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
263 passed, 1 warning in 42.76s
```

Collected count (`uv run pytest --collect-only -q | tail -3`):

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
263 tests collected in 1.00s
```

Pass / Fail / XFail / Skipped: 263 passed, 0 failed, 0 errored, 0 xfailed, 0 skipped. The single warning is a pre-existing python-json-logger deprecation (module rename from `pythonjsonlogger.jsonlogger` to `pythonjsonlogger.json`), unrelated to test correctness.

Status: clean baseline. Matches the post-Step-6.8.3 figure recorded in CLAUDE.md ("Current state" — Step 6.8.3 entry, "263 passed").

## Question 2 — ltree library

Packages matched in pyproject.toml/uv.lock:

```
(no matches)
```

- `grep -niE "ltree" pyproject.toml` — no matches.
- `grep -niE "^name = \"[^\"]*ltree" uv.lock` — no matches (no package whose name contains "ltree").
- `grep -niE "^name = \"sqlalchemy-utils" uv.lock` — no matches (sqlalchemy-utils is not a dependency; it would otherwise have been a candidate for an `Ltree` column type, though not for in-Python path comparison).

Usage in src/ tests/ scripts/ (`grep -rE "from .*ltree|import.*ltree" src/ tests/ scripts/ --include="*.py"`):

```
(no matches)
```

No Python module in the source tree imports any ltree library. The codebase's existing ltree handling is DB-side only: `models/org_node.py` declares `path: Mapped[str]` with `FetchedValue()` and treats the value as opaque text on the Python side; descendant/ancestor comparisons happen in raw SQL where PostgreSQL resolves `nlevel(path)`, `path <@ ...`, etc. against the native ltree type (per CLAUDE.md's Step 5.3 entry).

Status: not installed. No Python ltree library is available in the runtime environment, and no in-Python comparison API exists today. Any 6.9.1 design that needs to compare ltree paths in Python (vs in SQL) would have to either (a) introduce a new dependency such as `python-ltree` or `sqlalchemy-utils[ltree]`, or (b) reuse the existing in-SQL comparison pattern (raw SQL or SQLAlchemy text expressions).

## Open questions for design conversation

- None surfaced from these two pre-flight checks. The choice between "introduce a Python ltree dependency" and "keep cascade comparison in SQL" belongs in the Step 6.9.1 design conversation itself, not in this pre-flight.
