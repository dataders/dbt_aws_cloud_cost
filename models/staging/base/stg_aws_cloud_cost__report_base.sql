{#-
  Local-CSV source for the demo. ShadowTraffic generates
  local_files/aws_cost_report.csv (see scripts/start.sh); we read it directly
  with DuckDB's read_csv so the pipeline has no external source catalog to
  attach. Final-model output still routes through catalogs v2 (+catalog).
-#}
{%- set csv_path = env_var('AWS_CLOUD_COST_CSV_PATH', 'local_files/aws_cost_report.csv') -%}

select *
from read_csv('{{ csv_path }}', header = true, all_varchar = true)
