{#- Override of the Databricks seed loader -- identical to the stock
    databricks__load_csv_rows except get_binding_char() is resolved once
    instead of once per cell. Each call is an adapter.dispatch, and every
    adapter call currently re-serializes the whole model node
    (dbt-labs/fs#15865), so a 10k-row x 80-column seed made 800k of them and
    never finished. Values still go through fs's own literal formatting.
    Drop this file once dbt-labs/fs#15865 is fixed. -#}
{% macro databricks__load_csv_rows(model, agate_table) %}

  {% set batch_size = get_batch_size() %}
  {#- Resolve once: each call is an adapter.dispatch, and every adapter call
      currently pays a full model-node serialization (dbt-labs/fs#15865). -#}
  {% set binding_char = get_binding_char() %}
  {% set column_override = model['config'].get('column_types', {}) %}
  {% set must_cast = model['config'].get('file_format', 'delta') == 'parquet' %}

  {% set statements = [] %}

  {% for chunk in agate_table.rows | batch(batch_size) %}
      {% set bindings = [] %}

      {% for row in chunk %}
          {% do bindings.extend(row) %}
      {% endfor %}

      {% set sql %}
          insert {% if loop.index0 == 0 -%} overwrite {% else -%} into {% endif -%} {{ this.render() }} values
          {% for row in chunk -%}
              ({%- for col_name in agate_table.column_names -%}
                  {%- if must_cast -%}
                    {%- set inferred_type = adapter.convert_type(agate_table, loop.index0) -%}
                    {%- set type = column_override.get(col_name, inferred_type) -%}
                    cast({{ binding_char }} as {{type}})
                  {%- else -%}
                    {{ binding_char }}
                  {%- endif -%}
                  {%- if not loop.last%},{%- endif %}
              {%- endfor -%})
              {%- if not loop.last%},{%- endif %}
          {%- endfor %}
      {% endset %}

      {% do adapter.add_query(sql, bindings=bindings, abridge_sql_log=True, close_cursor=True) %}

      {% if loop.index0 == 0 %}
          {% do statements.append(sql) %}
      {% endif %}
  {% endfor %}

  {# Return SQL so we can render it out into the compiled files #}
  {{ return(statements[0]) }}
{% endmacro %}
