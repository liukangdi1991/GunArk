DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL UNIQUE,
    execution_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    manifest_key TEXT,
    lineage_key TEXT
);

CREATE TABLE IF NOT EXISTS execution_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    item_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_name TEXT,
    params_json TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    artifact_type TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    mime_type TEXT,
    size_bytes INTEGER,
    checksum TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(execution_key, artifact_type)
);

CREATE TABLE IF NOT EXISTS execution_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    target_execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    link_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_execution_key, target_execution_key, link_type)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    request_json TEXT,
    result_json TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    sequence INTEGER NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    UNIQUE(job_id, sequence)
);

CREATE TABLE IF NOT EXISTS strategy_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS strategy_group_members (
    group_id TEXT NOT NULL REFERENCES strategy_groups(id),
    strategy_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_id, strategy_id)
);

CREATE TABLE IF NOT EXISTS strategy_settings (
    strategy_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    params_json TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    stock_count INTEGER,
    skipped_latest INTEGER DEFAULT 0,
    empty_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_executions_created_at ON executions(created_at);
CREATE INDEX IF NOT EXISTS idx_execution_items_execution_key ON execution_items(execution_key);
CREATE INDEX IF NOT EXISTS idx_artifacts_execution_key ON artifacts(execution_key);
CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_market_sync_runs_execution_key ON market_sync_runs(execution_key);
"""


def init_schema(conn):
    conn.executescript(DDL)
    conn.commit()
