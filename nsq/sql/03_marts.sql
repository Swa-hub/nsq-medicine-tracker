-- MARTS: the tables the dashboard, Power BI and the model read.

-- Every flag (fact table)
CREATE OR REPLACE TABLE fct_flags AS
SELECT * FROM int_flags;

-- Factories (sites). Spurious rows are left out: a fake carries an innocent
-- company's name, so it must not count against that company.
CREATE OR REPLACE TABLE dim_site AS
SELECT
    site_id,
    any_value(company_id)                         AS company_id,
    mode(company_name)                            AS company_name,
    mode(mfr_state)                               AS state,
    mode(mfr_district)                            AS district,
    mode(mfr_pin)                                 AS pin,
    min(report_month)                             AS first_flag_month,
    max(report_month)                             AS last_flag_month,
    count(*)                                      AS total_flags,
    count(DISTINCT report_month)                  AS months_flagged,
    count(DISTINCT drug_generic)                  AS distinct_drugs,
    count(DISTINCT mfr_key)                       AS spellings_seen
FROM fct_flags
WHERE NOT is_spurious AND site_id IS NOT NULL
GROUP BY site_id;

-- Flags per month and list section
CREATE OR REPLACE TABLE mart_monthly AS
SELECT report_month,
       CASE WHEN is_spurious THEN 'spurious' ELSE section END AS section,
       count(*) AS flags
FROM fct_flags
GROUP BY ALL
ORDER BY report_month, section;

-- Repeat offenders: sites flagged in 3 or more different months
CREATE OR REPLACE TABLE mart_repeat_offenders AS
SELECT
    s.*,
    date_diff('month', s.first_flag_month, s.last_flag_month) + 1 AS span_months,
    (SELECT count(*) FROM fct_flags f
      WHERE f.site_id = s.site_id AND NOT f.is_spurious
        AND f.report_month > (SELECT max(report_month) FROM fct_flags) - INTERVAL 12 MONTH) AS flags_last_12m
FROM dim_site s
WHERE s.months_flagged >= 3
ORDER BY s.months_flagged DESC, s.total_flags DESC;

-- Clusters: where failing factories are
CREATE OR REPLACE TABLE mart_district AS
SELECT
    COALESCE(mfr_state, 'Unknown')      AS state,
    COALESCE(mfr_district, 'Unknown')   AS district,
    count(*)                            AS flags,
    count(DISTINCT site_id)             AS sites,
    count(DISTINCT company_id)          AS companies
FROM fct_flags
WHERE NOT is_spurious
GROUP BY ALL
ORDER BY flags DESC;

-- Why medicines fail, by dosage form
CREATE OR REPLACE TABLE mart_reasons AS
SELECT drug_form, reason_primary, count(*) AS flags
FROM fct_flags
WHERE NOT is_spurious
GROUP BY ALL
ORDER BY flags DESC;

-- Which states' labs show up in each month's state list (testing coverage).
-- A missing month is "no report", not "no failures".
CREATE OR REPLACE TABLE mart_state_reporting AS
SELECT report_month, reporting_state AS state, count(*) AS flags_reported
FROM fct_flags
WHERE section = 'state' AND reporting_state IS NOT NULL
GROUP BY ALL
ORDER BY report_month, state;

CREATE OR REPLACE TABLE mart_pending_states AS
SELECT * FROM stg_pending_states;

-- Fakes and failures of QR-coded top brands (QR mandatory from Aug 2023)
CREATE OR REPLACE TABLE mart_qr_brands AS
SELECT
    report_month, qr_brand, is_spurious, drug_name, batch_no, manufacturer_raw,
    reason_raw, section, source_file, page, source_url
FROM fct_flags
WHERE qr_brand IS NOT NULL AND qr_brand <> ''
ORDER BY report_month DESC;

-- Per-file parsing check: parsed rows versus the totals the PDF states
CREATE OR REPLACE TABLE mart_parse_quality AS
SELECT * FROM stg_doc_summary;
