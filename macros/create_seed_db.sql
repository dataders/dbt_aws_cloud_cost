{% macro create_seed_db() %}
  {% do run_query("CREATE DATABASE IF NOT EXISTS " ~ env_var('SNOWFLAKE_SEED_DATABASE', 'SEEDS')) %}
  {% do run_query("CREATE SCHEMA IF NOT EXISTS " ~ env_var('SNOWFLAKE_SEED_DATABASE', 'SEEDS') ~ ".aws_cloud_cost") %}
{% endmacro %}
