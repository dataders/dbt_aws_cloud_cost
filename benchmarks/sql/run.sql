.timer on
SET autoinstall_known_extensions=false;
SET autoload_known_extensions=false;
LOAD iceberg;
LOAD httpfs;
SET enable_progress_bar=false;
SET preserve_insertion_order=false;
SET threads={threads};
SET memory_limit='{memory_limit}';

CALL enable_logging('HTTP');
{profile_sql}

.print >>> PHASE: duckdb_context
SELECT version() AS duckdb_version;
PRAGMA platform;
PRAGMA database_size;
SELECT current_setting('threads') AS threads;
SELECT current_setting('memory_limit') AS memory_limit;

.print >>> PHASE: secrets
{secret_sql}

.print >>> PHASE: attach
{attach_sql}

{workload_sql}

.print >>> PHASE: http_log
.mode csv
.output {http_log_path}
SELECT * FROM duckdb_logs WHERE type = 'HTTP' ORDER BY timestamp;
.output
.mode duckbox
