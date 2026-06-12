{#-
  The demo's source is the committed seed (seeds/aws_cost_report.csv), loaded
  into the built-in DuckDB catalog by `dbt seed`. Final-model output routes
  through catalogs v2 (+catalog_name in dbt_project.yml).
-#}
select *
from {{ ref('aws_cost_report') }}
