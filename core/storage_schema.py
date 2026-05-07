SCHEMA_SQL = """
create table if not exists executions (
    id integer primary key autoincrement,
    execution_key text not null unique,
    execution_type text not null,
    status text not null,
    created_at text not null,
    finished_at text not null,
    object_dir_key text not null
);

create table if not exists selection_results (
    id integer primary key autoincrement,
    execution_id integer not null unique,
    selection_date text not null,
    selection_from text,
    selection_to text,
    trade_days integer not null default 1,
    data_dir text,
    signal_file text,
    foreign key (execution_id) references executions(id) on delete cascade
);

create table if not exists backtest_results (
    id integer primary key autoincrement,
    execution_id integer not null unique,
    start_date text not null,
    end_date text not null,
    signal_dir text,
    foreign key (execution_id) references executions(id) on delete cascade
);

create table if not exists execution_items (
    id integer primary key autoincrement,
    execution_id integer not null,
    item_type text not null,
    item_key text not null,
    item_name text not null,
    item_class text,
    description text,
    foreign key (execution_id) references executions(id) on delete cascade,
    unique (execution_id, item_type, item_key)
);

create table if not exists execution_item_params (
    id integer primary key autoincrement,
    execution_item_id integer not null,
    param_key text not null,
    param_value text,
    param_type text not null,
    foreign key (execution_item_id) references execution_items(id) on delete cascade,
    unique (execution_item_id, param_key)
);

create table if not exists execution_item_metrics (
    id integer primary key autoincrement,
    execution_item_id integer not null,
    metric_key text not null,
    metric_value text,
    metric_type text not null,
    foreign key (execution_item_id) references execution_items(id) on delete cascade,
    unique (execution_item_id, metric_key)
);

create table if not exists artifacts (
    id integer primary key autoincrement,
    execution_id integer not null,
    artifact_scope text not null,
    artifact_type text not null,
    storage_key text not null,
    mime_type text,
    size_bytes integer,
    checksum text,
    created_at text not null,
    foreign key (execution_id) references executions(id) on delete cascade,
    unique (execution_id, artifact_scope, artifact_type)
);

create table if not exists backtest_selection_links (
    id integer primary key autoincrement,
    backtest_execution_id integer not null,
    selection_execution_id integer not null,
    created_at text not null,
    foreign key (backtest_execution_id) references executions(id) on delete cascade,
    foreign key (selection_execution_id) references executions(id) on delete cascade,
    unique (backtest_execution_id, selection_execution_id)
);

create table if not exists execution_log_links (
    id integer primary key autoincrement,
    job_execution_id text not null unique,
    resource_type text not null,
    resource_execution_key text,
    resource_url text,
    created_at text not null
);

create index if not exists idx_executions_created_at
    on executions(created_at);
create index if not exists idx_execution_items_execution_id
    on execution_items(execution_id);
create index if not exists idx_artifacts_execution_id
    on artifacts(execution_id);
create index if not exists idx_backtest_selection_links_backtest
    on backtest_selection_links(backtest_execution_id);
create index if not exists idx_backtest_selection_links_selection
    on backtest_selection_links(selection_execution_id);
create index if not exists idx_execution_log_links_resource
    on execution_log_links(resource_type, resource_execution_key);
"""
