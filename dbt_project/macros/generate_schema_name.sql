{# By default dbt prefixes custom schemas with the target schema
   (e.g. "main_gold"). Override so models land in exactly "silver" / "gold"
   as configured in dbt_project.yml, matching the bronze schema created by
   the ingestion script and what the dashboard/tests expect. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
