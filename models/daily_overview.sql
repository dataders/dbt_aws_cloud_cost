{{
    config(
        materialized='table',
        alt_compute='alt',
        catalog_name='horizon',
    )
}}

with source_report as (

    select *
    from {{ ref('stg_report') }}
),

{# Sometimes records are sent with just IDs and null names. The following 2 CTEs will map account names
to their IDs so we can fill these in when needed. #}
usage_account_mapping as (

    select
        usage_account_id,
        usage_account_name,
        source_relation,
        max(usage_start_date) as latest_start_date

    from source_report
    where usage_account_name is not null
    group by 1,2,3
),

usage_account_names as (

    select
        sub.usage_account_id,
        sub.usage_account_name,
        sub.source_relation
    from (
        {# In case the account name as been updated, let's ensure we're only grabbing the most recent one #}
        select
            usage_account_id,
            usage_account_name,
            source_relation,
            row_number() over (partition by usage_account_id order by latest_start_date desc) = 1 as is_latest_name
        from usage_account_mapping
    ) as sub where is_latest_name
),

{# Sometimes records are sent with just IDs and null names. The following 2 CTEs will map account names
to their IDs so we can fill these in when needed. #}
billing_account_mapping as (

    select
        bill_payer_account_id,
        bill_payer_account_name,
        source_relation,
        max(billing_period_start_date) as latest_start_date

    from source_report
    where bill_payer_account_name is not null
    group by 1,2,3
),

billing_account_names as (

    select
        sub.bill_payer_account_id,
        sub.bill_payer_account_name,
        sub.source_relation
    from (
        {# In case the account name as been updated, let's ensure we're only grabbing the most recent one #}
        select
            bill_payer_account_id,
            bill_payer_account_name,
            source_relation,
            row_number() over (partition by bill_payer_account_id order by latest_start_date desc) = 1 as is_latest_name
        from billing_account_mapping
    ) as sub where is_latest_name
),

fields as (

    {#- The Alt engine's write path derives each output column's stored name
       from how it's selected: a bare passthrough column keeps whatever case
       the upstream (native-Snowflake, auto-uppercased) table already stores
       it in, but an explicit alias is preserved exactly as written -- it does
       NOT get auto-uppercased the way it would on a plain Snowflake CTAS.
       Left alone, that produces a table with MIXED case (uppercase
       passthroughs, lowercase computed columns), which then breaks downstream
       native-Snowflake reads of the computed columns (unquoted references
       auto-uppercase and no longer match). Every computed/aliased column
       below is therefore explicitly aliased in UPPERCASE to match the
       passthrough columns' casing. -#}
    select
        source_report.source_relation,
        report,

        {# Period Details #}
        cast({{ dbt.date_trunc('day', 'usage_start_date') }} as date) as "USAGE_START_DATE",
        cast({{ dbt.date_trunc('day', 'usage_end_date') }} as date) as "USAGE_END_DATE",
        {#- Iceberg v2 caps timestamp precision at microseconds (the Alt
           engine's write path always creates v2 tables regardless of this
           model's iceberg_version='3' config -- that setting doesn't
           propagate through the Alt write path); billing_period_start/end_date
           are nanosecond-precision TIMESTAMP_NTZ(9) from the upstream
           timestamp_type cast, so truncate to date here the same way the
           usage dates already are. -#}
        cast(billing_period_start_date as date) as "BILLING_PERIOD_START_DATE",
        cast(billing_period_end_date as date) as "BILLING_PERIOD_END_DATE",

        {# Account Details #}
        source_report.usage_account_id,
        coalesce(source_report.usage_account_name, usage_account_names.usage_account_name) as "USAGE_ACCOUNT_NAME",
        source_report.bill_payer_account_id,
        coalesce(source_report.bill_payer_account_name, billing_account_names.bill_payer_account_name) as "BILL_PAYER_ACCOUNT_NAME",

        {# Billing Details #}
        invoice_id,
        invoicing_entity,
        billing_entity,
        bill_type,
        line_item_type,
        tax_type,

        {# Pricing Details #}
        purchase_option,
        pricing_term,
        product_fee_code,
        product_fee_description,

        {# Units #}
        pricing_unit,
        usage_type,
        currency_code,

        {# Line Item Service Details #}
        line_item_description,
        product_code,
        product_name,
        product_service_code, -- service is within product
        product_family,
        operation,

        {# Product Details - Compute #}
        instance_type,
        instance_family,

        {# Product Details - s3 #}
        location,
        location_type,
        region_code,
        availability_zone,

        {# Product Details - Data transfers/movement #}
        from_location,
        from_location_type,
        from_region_code,
        to_location,
        to_location_type,
        to_region_code,

        {# Usage Metrics #}
        cast(sum(coalesce(usage_amount, 0)) as {{ dbt.type_numeric() }}) as "USAGE_AMOUNT",
        cast(sum(coalesce(normalized_usage_amount, 0)) as {{ dbt.type_numeric() }}) as "NORMALIZED_USAGE_AMOUNT",
        cast(max(normalization_factor) as {{ dbt.type_numeric() }}) as "NORMALIZATION_FACTOR",

        {# Cost Metrics - General #}
        cast(sum(coalesce(blended_cost, 0)) as {{ dbt.type_numeric() }}) as "BLENDED_COST",
        cast(sum(coalesce(unblended_cost, 0)) as {{ dbt.type_numeric() }}) as "UNBLENDED_COST",
        cast(sum(coalesce(public_on_demand_cost, 0)) as {{ dbt.type_numeric() }}) as "PUBLIC_ON_DEMAND_COST",
        cast(avg(blended_rate) as {{ dbt.type_numeric() }}) as "AVG_BLENDED_RATE",
        cast(avg(unblended_rate) as {{ dbt.type_numeric() }}) as "AVG_UNBLENDED_RATE",
        cast(avg(public_on_demand_rate) as {{ dbt.type_numeric() }}) as "AVG_PUBLIC_ON_DEMAND_RATE",
        cast(count(*) as {{ dbt.type_numeric() }}) as "COUNT_LINE_ITEMS",

        {# Cost & Usage Metrics - Reservations
            Using MAX's + MIN's under the assumption that there is a 1:Many relationship between Reservations and Line Items
        #}
        cast(max(reservation_amortized_upfront_cost_for_usage) as {{ dbt.type_numeric() }}) as "RESERVATION_AMORTIZED_UPFRONT_COST_FOR_USAGE",
        cast(max(reservation_amortized_upfront_fee_for_billing_period) as {{ dbt.type_numeric() }}) as "RESERVATION_AMORTIZED_UPFRONT_FEE_FOR_BILLING_PERIOD",
        cast(max(reservation_effective_cost) as {{ dbt.type_numeric() }}) as "RESERVATION_EFFECTIVE_COST",
        cast(max(number_of_reservations) as {{ dbt.type_numeric() }}) as "NUMBER_OF_RESERVATIONS",
        cast(max(normalized_units_per_reservation) as {{ dbt.type_numeric() }}) as "NORMALIZED_UNITS_PER_RESERVATION",
        cast(max(units_per_reservation) as {{ dbt.type_numeric() }}) as "UNITS_PER_RESERVATION",
        cast(max(total_reserved_normalized_units) as {{ dbt.type_numeric() }}) as "TOTAL_RESERVED_NORMALIZED_UNITS",
        cast(max(total_reserved_units) as {{ dbt.type_numeric() }}) as "TOTAL_RESERVED_UNITS",

        cast(max(reservation_recurring_fee_for_usage) as {{ dbt.type_numeric() }}) as "RESERVATION_RECURRING_FEE_FOR_USAGE",
        cast(min(reservation_unused_amortized_upfront_fee_for_billing_period) as {{ dbt.type_numeric() }}) as "RESERVATION_UNUSED_AMORTIZED_UPFRONT_FEE_FOR_BILLING_PERIOD",
        cast(min(reservation_unused_normalized_unit_quantity) as {{ dbt.type_numeric() }}) as "RESERVATION_UNUSED_NORMALIZED_UNIT_QUANTITY",
        cast(min(reservation_unused_quantity) as {{ dbt.type_numeric() }}) as "RESERVATION_UNUSED_QUANTITY",
        cast(min(reservation_unused_recurring_fee) as {{ dbt.type_numeric() }}) as "RESERVATION_UNUSED_RECURRING_FEE",
        cast(max(reservation_upfront_value) as {{ dbt.type_numeric() }}) as "RESERVATION_UPFRONT_VALUE",

        {# Cost & Usage Metrics - Savings Plans #}
        cast(max(savings_plan_amortized_upfront_commitment_for_billing_period) as {{ dbt.type_numeric() }}) as "SAVINGS_PLAN_AMORTIZED_UPFRONT_COMMITMENT_FOR_BILLING_PERIOD",
        cast(max(savings_plan_recurring_commitment_for_billing_period) as {{ dbt.type_numeric() }}) as "SAVINGS_PLAN_RECURRING_COMMITMENT_FOR_BILLING_PERIOD",
        cast(max(savings_plan_effective_cost) as {{ dbt.type_numeric() }}) as "SAVINGS_PLAN_EFFECTIVE_COST",
        cast(max(savings_plan_rate) as {{ dbt.type_numeric() }}) as "SAVINGS_PLAN_RATE",
        cast(max(savings_plan_total_commitment_to_date) as {{ dbt.type_numeric() }}) as "SAVINGS_PLAN_TOTAL_COMMITMENT_TO_DATE",
        cast(max(savings_plan_used_commitment) as {{ dbt.type_numeric() }}) as "SAVINGS_PLAN_USED_COMMITMENT"

    from source_report
    left join billing_account_names
        on source_report.bill_payer_account_id = billing_account_names.bill_payer_account_id
        and source_report.source_relation = billing_account_names.source_relation
    left join usage_account_names
        on source_report.usage_account_id = usage_account_names.usage_account_id
        and source_report.source_relation = usage_account_names.source_relation

    {{ dbt_utils.group_by(n=41) }}
),

final as (
{%- set composite_key = [
        'source_relation',
        'report',
        'usage_start_date',
        'usage_end_date',
        'billing_period_start_date',
        'billing_period_end_date',
        'usage_account_id',
        'usage_account_name',
        'bill_payer_account_id',
        'bill_payer_account_name',
        'invoice_id',
        'invoicing_entity',
        'billing_entity',
        'bill_type',
        'line_item_type',
        'tax_type',
        'purchase_option',
        'pricing_term',
        'product_fee_code',
        'product_fee_description',
        'pricing_unit',
        'usage_type',
        'currency_code',
        'line_item_description',
        'product_code',
        'product_service_code',
        'product_name',
        'product_family',
        'operation',
        'instance_family',
        'instance_type',
        'location',
        'location_type',
        'region_code',
        'availability_zone',
        'from_location',
        'from_location_type',
        'from_region_code',
        'to_location',
        'to_location_type',
        'to_region_code'
    ]
-%}

    select
        *,
        {{ dbt_utils.generate_surrogate_key(composite_key) }} as "UNIQUE_KEY"
    from fields
)

select *
from final
