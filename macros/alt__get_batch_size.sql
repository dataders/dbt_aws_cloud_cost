{#-
  dbt-adapters' default batch size is 10000 -- for this seed's 10000 rows and
  ~40 columns, that's a single INSERT statement carrying ~400k bound values.
  Sent over the Alt engine's HTTP-based dbt-compute API rather than a normal
  DB wire protocol, that single giant statement is the likely cause of the
  13-minute-then-broken-pipe failure seen loading this seed against the bare
  Alt target. Split into much smaller batches instead.
-#}
{% macro alt__get_batch_size() %}
  {{ return(200) }}
{% endmacro %}
