{#- Demo-only override of the Databricks seed loader.

    The stock databricks__load_csv_rows binds every cell as an ADBC parameter
    (batch_size x columns bound params per statement). Through this build's
    Databricks ADBC driver that path costs ~3s per 250-row batch plus ~50s of
    per-statement overhead, making a 10k-row seed take ~40 minutes. Emitting
    literal VALUES instead skips the parameter-binding path entirely: the same
    load finishes in seconds.

    Strings are the only cells that need escaping; dbt already renders NULL
    for missing values, so single quotes are doubled and everything else is
    inserted verbatim.
-#}
{% macro databricks__load_csv_rows(model, agate_table) %}

  {% set batch_size = get_batch_size() %}
  {% set column_override = model['config'].get('column_types', {}) %}
  {% set must_cast = model['config'].get('file_format', 'delta') == 'parquet' %}

  {% set statements = [] %}

  {% for chunk in agate_table.rows | batch(batch_size) %}
      {% set sql %}
          insert {% if loop.index0 == 0 -%} overwrite {% else -%} into {% endif -%} {{ this.render() }} values
          {% for row in chunk -%}
              ({%- for col_name in agate_table.column_names -%}
                  {%- set value = row[loop.index0] -%}
                  {%- if value is none -%}
                    NULL
                  {%- else -%}
                    '{{ value | replace("'", "''") }}'
                  {%- endif -%}
                  {%- if not loop.last%},{%- endif %}
              {%- endfor -%})
              {%- if not loop.last%},{%- endif %}
          {%- endfor %}
      {% endset %}

      {% do adapter.add_query(sql, abridge_sql_log=True, close_cursor=True) %}

      {% if loop.index0 == 0 %}
          {% do statements.append(sql) %}
      {% endif %}
  {% endfor %}

  {{ return(statements[0]) }}
{% endmacro %}
