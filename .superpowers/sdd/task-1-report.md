# Task 1 Report: Project Scaffold, Init/Reset, Storage Schema

## What I Implemented

**Package structure:**
- `trendradar/__init__.py` — package entry, exports `__version__`
- `trendradar/_version.py` — `2.0.0.dev0`
- `trendradar/py.typed` — PEP 561 marker (empty)
- `trendradar/cli.py` — `init-v2` command with `--reset-runtime` / `--confirm-reset`
- `pyproject.toml` — build config with setuptools, polars dependency

**Infrastructure:**
- `trendradar/infrastructure/runtime.py` — `runtime_root()`, `storage_root()`, `source_root()` path resolution
- `trendradar/infrastructure/storage/schema.py` — DDL with 10 tables + indexes, `init_schema()` function
- `trendradar/infrastructure/storage/connection.py` — `StorageConnection` SQLite wrapper
- `trendradar/infrastructure/storage/repository.py` — `AppRepository` with CRUD for executions, strategy_groups, jobs, artifacts
- `trendradar/infrastructure/storage/artifact_store.py` — `ArtifactStore` for JSON file artifacts
- `trendradar/infrastructure/filesystem/atomic.py` — `atomic_write()` / `atomic_write_text()`

**Tests:**
- `tests/infrastructure/test_schema.py` — 3 tests: all tables created, FK enforcement, unique constraint
- `tests/infrastructure/test_atomic.py` — 3 tests: file creation, replacement, no partial write on failure
- `tests/test_cli.py` — 2 tests: init creates structure, reset requires confirm

**Other:**
- `.gitignore` updated with V2 patterns
- `.superpowers/` added to gitignore

## What I Tested & Results

| Test Suite | Result |
|---|---|
| `test_schema.py` (3 tests) | ✅ All passed |
| `test_atomic.py` (3 tests) | ✅ All passed |
| `test_cli.py` (2 tests) | ✅ All passed |
| `pip install -e .` | ✅ Success |
| `python -m trendradar.cli init-v2` | ✅ Creates structure |
| `python -m trendradar.cli init-v2 --reset-runtime` | ✅ Exits 1, requires --confirm-reset |
| `python -m trendradar.cli init-v2 --reset-runtime --confirm-reset` | ✅ Deletes and recreates |
| Full existing test suite (26 old tests) | ✅ All pass (no regressions) |

## TDD Evidence

**RED phase:** Created `tests/infrastructure/test_schema.py`, `tests/infrastructure/test_atomic.py`, `tests/test_cli.py` with no implementation — initially failed on import errors. Then ran `pip install -e .` to expose failures.

**GREEN phase:** Implemented all modules iteratively until all 8 tests passed. Key fix:
- Filtered `sqlite_sequence` from table set in `test_schema_creates_all_tables` (AUTOINCREMENT creates it automatically)
- Fixed `RESET_PATHS` in `cli.py` — paths were relative to `storage_root()` which already ends in `storage/`, causing double-nesting

## Files Changed

**Created (20 files):**
- `pyproject.toml`
- `trendradar/__init__.py`
- `trendradar/_version.py`
- `trendradar/py.typed`
- `trendradar/cli.py`
- `trendradar/infrastructure/__init__.py`
- `trendradar/infrastructure/runtime.py`
- `trendradar/infrastructure/storage/__init__.py`
- `trendradar/infrastructure/storage/schema.py`
- `trendradar/infrastructure/storage/connection.py`
- `trendradar/infrastructure/storage/repository.py`
- `trendradar/infrastructure/storage/artifact_store.py`
- `trendradar/infrastructure/filesystem/__init__.py`
- `trendradar/infrastructure/filesystem/atomic.py`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/infrastructure/__init__.py`
- `tests/infrastructure/test_schema.py`
- `tests/infrastructure/test_atomic.py`
- `tests/test_cli.py`

**Modified (1 file):**
- `.gitignore` — added V2 and `.superpowers/` entries

## Self-Review Findings

1. **RESET_PATHS bug in brief:** The brief's `RESET_PATHS` used `"storage/app.db"` etc., but `root = storage_root()` already resolves to `<runtime>/storage`. Fixed paths to `"app.db"`, `"objects/"`, `"market/"`.
2. **sqlite_sequence table:** SQLite auto-creates it when using `AUTOINCREMENT`. Test filters it out via `- {"sqlite_sequence"}`.
3. **pyproject.toml backend:** `setuptools.backends._legacy:_Backend` doesn't exist in modern setuptools. Changed to `setuptools.build_meta`.
4. **Package discovery:** Had to add `[tool.setuptools.packages.find]` with `include = ["trendradar*"]` to avoid flat-layout conflict with old top-level packages.
5. Old code remains in place as required — not moved to `references/`.
6. All self-review checklist items verified ✅

## Issues or Concerns

None.

---

## Review Fixes (2026-07-09)

Fixes applied for Task 1 review issues:

### Critical 1: `RESET_PATHS` missing old `db/` directory
- `cli.py`: Added `source_root` import and `old_db_dir = source_root() / "db"` path
- Reset logic now prints and deletes old `db/` if it exists
- `.gitignore`: Replaced `/storage/app.db`, `/storage/objects/`, `/storage/market/` with `/db/`

### Important 2: Deleted `trendradar/infrastructure/storage/repository.py`
- Removed entire file — not in Task 1 scope, untested, and manual `commit()` pattern would cause conflicts in later tasks

### Important 3: `.gitignore` redundant entries
- Removed lines 31-33 (`/storage/app.db`, `/storage/objects/`, `/storage/market/`) — already covered by `/storage/` on line 29
- Replaced with `/db/` for old db directory

### Minor 4: Added `test_reset_deletes_old_data` test
- `tests/test_cli.py`: New test creates old `db/` directory with data, runs reset, verifies `db/` is deleted
- Monkeypatches `trendradar.cli.source_root` to point to `tmp_path`

### Minor 5: `pyproject.toml` version deduplication
- Removed hardcoded `version = "2.0.0.dev0"`
- Added `dynamic = ["version"]`
- Added `[tool.setuptools.dynamic]` with `version = {attr = "trendradar._version.__version__"}`

### Test Results
```
tests/infrastructure/test_atomic.py (3) ✅
tests/infrastructure/test_schema.py (3) ✅
tests/test_cli.py (3) ✅
Full test suite (35 tests) ✅ All pass
```

### Commit
`d99bdc4` — fix: review fixes for Task 1 — reset includes old db/, remove repository.py, deduplicate pyproject.toml version, add reset test
