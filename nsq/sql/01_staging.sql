-- STAGING: typed copies of the Python outputs. One view per input, no logic.

CREATE OR REPLACE TABLE stg_rows AS
SELECT
    * EXCLUDE (report_month),
    report_month AS report_month_num,
    CAST(report_month_start AS DATE) AS report_month
FROM clean_rows
WHERE in_facts;

CREATE OR REPLACE TABLE stg_index AS
SELECT * FROM alert_index;

CREATE OR REPLACE TABLE stg_doc_summary AS
SELECT * FROM doc_summary;

CREATE OR REPLACE TABLE stg_manufacturer_map AS
SELECT * FROM manufacturer_map;

CREATE OR REPLACE TABLE stg_pending_states AS
SELECT
    CAST(make_date(CAST(report_year AS INTEGER), CAST(report_month AS INTEGER), 1) AS DATE) AS report_month,
    state
FROM pending_states;
