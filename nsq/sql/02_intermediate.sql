-- INTERMEDIATE: one row per flagged batch, linked to its company and site.

-- The same batch can appear twice (an original list and its revision, or a
-- central and a state lab testing the same batch in one month). Keep one:
-- revised lists first, then the earliest page and row.
CREATE OR REPLACE TABLE int_flags AS
WITH ranked AS (
    SELECT
        r.*,
        ROW_NUMBER() OVER (
            PARTITION BY r.report_month,
                         COALESCE(NULLIF(r.batch_key, ''), r.source_file || '#' || CAST(r.row_idx AS VARCHAR)),
                         r.mfr_key, r.drug_generic
            ORDER BY r.is_revised DESC, r.source_file, r.page, r.row_idx
        ) AS dup_rank
    FROM stg_rows r
)
SELECT
    md5(concat_ws('|', ranked.source_file, CAST(ranked.page AS VARCHAR), CAST(ranked.row_idx AS VARCHAR),
                  ranked.batch_key)) AS flag_id,
    ranked.report_month,
    ranked.section,
    ranked.is_spurious,
    ranked.drug_name,
    ranked.drug_generic,
    ranked.drug_brand,
    ranked.drug_form,
    ranked.drug_strength,
    ranked.batch_no,
    ranked.batch_key,
    ranked.mfg_month,
    ranked.exp_month,
    ranked.reason_raw,
    ranked.reason_primary,
    ranked.reason_categories,
    ranked.manufacturer_raw,
    ranked.mfr_name,
    ranked.mfr_key,
    ranked.mfr_pin,
    ranked.mfr_state,
    ranked.mfr_state_source,
    ranked.mfr_district,
    ranked.reporting_state,
    ranked.reporting_lab,
    ranked.qr_brand,
    ranked.source_file,
    ranked.page,
    ranked.row_idx,
    ranked.doc_type,
    ranked.parse_method,
    ranked.warnings,
    m.company_id,
    m.company_name,
    m.site_id,
    i.url AS source_url
FROM ranked
LEFT JOIN stg_manufacturer_map m
       ON m.mfr_key = ranked.mfr_key
      AND m.mfr_pin = COALESCE(ranked.mfr_pin, '')
      AND m.mfr_district = COALESCE(ranked.mfr_district, '')
LEFT JOIN stg_index i
       ON i.num_id = ranked.num_id
WHERE ranked.dup_rank = 1;
