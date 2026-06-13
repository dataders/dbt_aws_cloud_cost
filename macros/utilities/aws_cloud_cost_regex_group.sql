{% macro aws_cloud_cost_regex_group(expression, pattern, group_index) -%}
    {%- if target.type == 'snowflake' -%}
        regexp_substr({{ expression }}, '{{ pattern }}', 1, 1, 'e', {{ group_index }})
    {%- else -%}
        regexp_extract({{ expression }}, '{{ pattern }}', {{ group_index }})
    {%- endif -%}
{%- endmacro %}
