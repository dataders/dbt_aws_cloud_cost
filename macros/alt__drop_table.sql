{#-
  The Alt engine's Iceberg REST write path rejects `DROP TABLE ... CASCADE`
  ([NotImplementedException] "DROP TABLE <table_name> CASCADE is not
  supported for Iceberg tables currently"). dbt-adapters' default__drop_table
  always appends CASCADE, and fs's dbt-alt macro package is empty (no
  override of its own), so every table materialization's drop-before-create
  step fails on this branch without this override.
-#}
{% macro alt__drop_table(relation) -%}
    drop table if exists {{ relation.render() }}
{%- endmacro %}
