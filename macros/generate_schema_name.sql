{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set catalog_schema = env_var('AWS_CLOUD_COST_TARGET_SCHEMA', env_var('CATALOG_SCHEMA', '')) -%}
    {%- if custom_schema_name == 'aws_cloud_cost' -%}
        {{ (catalog_schema or custom_schema_name) | trim }}
    {%- elif catalog_schema and custom_schema_name == catalog_schema -%}
        {{ catalog_schema | trim }}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ target.schema }}_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
