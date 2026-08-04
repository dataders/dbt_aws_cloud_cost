{% macro set_rsa_public_key() %}
  {% do run_query("ALTER USER " ~ env_var('SNOWFLAKE_DEMO_USER') ~ " SET RSA_PUBLIC_KEY='" ~ env_var('SNOWFLAKE_DEMO_RSA_PUBLIC_KEY') ~ "'") %}
{% endmacro %}
