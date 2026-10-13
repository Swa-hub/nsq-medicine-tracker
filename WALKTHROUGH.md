# How the project works, in plain English

Read this before an interview. Each section says what a stage does, why it's built that way, and where it lives in the code.

## The problem in one breath

Every month, India's drug regulator lists medicine batches that failed lab tests. Each list is a separate PDF (now a web page), the format changed over the years, and one factory's name gets written a dozen ways. So nobody can easily answer "which factories keep failing?" or "who is likely to fail next?". This project answers both.

## 1. Collect (`scrape_index.py`, `download.py`, `portal.py`)

- The Alerts page is one long table of every notice CDSCO has posted: drug lists, but also device alerts, circulars and committee lists. The scraper reads that table and **classifies each title** ("STATE NSQ ALERT FOR THE MONTH OF June 2025" means a state-lab list for 2025-06).
- One trick: a plain "Drug Alert for the Month of December 2024" published next to a "State Drug Alert" for the same month is really the central-lab list, so it gets re-labelled.
- The downloader waits a few seconds between files, skips files it already has, checks each file really is a PDF, and writes a **manifest** with each file's size and SHA-256 hash. The hash proves which version of a file a number came from.
- From August 2025 the lists moved to a web app. You save its data responses once a month, and `portal.py` maps whatever the field names are onto the same columns as the PDFs.

**Why:** an index plus a manifest means every run is repeatable and only fetches what's new.

## 2. Parse (`parse_pdf.py`)

- `pdfplumber` finds the tables on each page. The parser recognises the header row by its words ("Batch No.", "Manufactured By") and maps each column onto one standard schema, so both formats produce the same columns.
- **Old format (to 2023):** batch, dates and manufacturer sit in one cell. `split_combined_cell` uses the labels inside the cell ("B. No.:", "Mfg dt:", "Exp dt:", "Mfd by:") to cut it apart.
- **Continuation rows:** when a long address runs onto a new row, that row has no serial number and no batch, so it is glued back onto the row above.
- **Sections:** a list can hold central-lab rows, then a "STATE LABORATORIES" heading, then state rows. Each table takes the last heading printed above it on the page.
- **Self-check:** old lists print "Total number of samples declared as Not of Standard Quality 45". The parser compares its row count with that number and marks each file `ok` or `mismatch`.
- Every row keeps its **source file, page and row number**.

**Why:** most student projects trust the parser. This one proves it, file by file.

## 3. Clean (`clean.py`, `geo.py`)

- **Manufacturer:** drop "M/s.", split the name from the address, then build a spelling-proof key: "Arvex Life Sciences Private Limited" and "ARVEX LIFESCIENCES PVT. LTD." both become `arvex lifesciences`.
- **Location:** PIN code from the address. The state comes from words in the address first ("Himachal Pradesh", "(H.P.)"), then from the district, and only then from the PIN code. PIN 247xxx covers both Roorkee (Uttarakhand) and Saharanpur (UP), so the PIN alone can be wrong.
- **Drug:** "Pantoprazole Gastro-Resistant Tablets IP 40 mg (Pantoshang-40)" becomes generic `pantoprazole`, form `tablet`, strength `40 mg`, brand `Pantoshang-40`.
- **Failure reason:** free text becomes categories (assay, dissolution, sterility...).
- **Revisions:** when CDSCO re-issues a month ("Revised"), the newer list replaces the older one, and duplicates are removed in SQL.

## 4. Link factories (`entities.py`), the hardest part

- This is **entity resolution**: deciding which spellings are the same company.
- Comparing every pair of names is slow and noisy, so names are first grouped into **blocks** (same first letters). Pairs inside a block are scored with `rapidfuzz` (0 to 100).
- The thresholds depend on the address:
  - 96+ links two names anywhere
  - 85+ is enough when both sit at the same PIN code (typos at one factory)
  - 93+ when one side has no PIN
- Linked pairs are joined into groups with **union-find**: if A matches B and B matches C, all three become one company.
- A **site** is one factory: company plus PIN code. Repeat offenders are counted per site, because a quality failure belongs to a factory, not a head office.
- **Measured, not assumed:** on sample data there's an answer key, scored with B-cubed precision and recall. On real data you hand-check 100 pairs (`match_review_sample.csv`).

## 5. Warehouse (`warehouse.py`, `sql/`)

- DuckDB is a single-file analytics database, so there's no server to install.
- The SQL is layered the way dbt projects are:
  - **staging:** typed copies of the inputs
  - **intermediate:** one row per flagged batch, de-duplicated and linked to its site
  - **marts:** the tables people use (`fct_flags`, `dim_site`, `mart_repeat_offenders`, `mart_district`, `mart_reasons`, `mart_state_reporting`, `mart_qr_brands`)
- Spurious rows are excluded from factory counts, because a fake carries an innocent company's name.

## 6. Check (`quality.py`)

About a dozen checks, each PASS, WARN or FAIL:

- rows vs stated totals
- missing batch numbers
- unlinked rows
- manufacture date after expiry
- months with no data
- how many states were guessed from the PIN alone
- matching accuracy (on sample data)

The monthly job runs with `--strict`, so a broken month stops the pipeline instead of reaching the dashboard.

## 7. Predict (`model.py`)

- **Question:** of the factories flagged before, which will be flagged again next month?
- **One row per factory per month.** Features come only from earlier months: flags in the last 1, 3, 6 and 12 months, months since the last flag, share of injections, sterility failures, flags at other factories in the same district, and national volume.
- **Honest test:** train on older months and test on the last 12. Never shuffle time.
- **Baseline:** rank by flags in the last 6 months. If the model can't beat this simple rule, it isn't worth having.
- **Metric:** precision@20. Each month, put 20 factories on a watch list and count how many were actually flagged.
- **Explaining it:** permutation importance shows which features the model leans on.

## 8. Show (`app/dashboard.py`, `export.py`)

The Streamlit dashboard has tabs for the overview, repeat offenders (with each factory's history and links to source PDFs), districts, failure reasons, testing gaps, QR-coded brands, the watch list, a batch lookup and data quality. Every mart is also exported as CSV and Parquet for Power BI.

---

# Interview questions you should be ready for

**Why not just use CDSCO's own search page?**
It shows one alert at a time. It can't tell you that one factory failed in 14 different months under 5 spellings, which states stopped reporting, or who is likely next.

**How do you know the parser is right?**
Old lists state their totals, and each file is checked against them. Every row also keeps its file, page and row, so any number can be traced to a line in a PDF.

**What was the hardest part?**
Entity resolution. Too loose and you merge two different companies with similar names; too strict and one factory looks like five. So I used the PIN code as extra evidence and measured precision and recall instead of eyeballing it.

**Why precision@20 and not accuracy?**
Most factory-months have no flag, so a model that always says "no" gets high accuracy and is useless. A regulator or bank has limited time, so the question is how many of the top 20 were right.

**Did the model beat the baseline?**
Give your real numbers from `model_metrics.json`. If it barely beats it, say so: recent history is a strong signal, and an honest small gain is better than an inflated one.

**What are the biggest limitations?**
- A failure applies to one batch.
- Samples aren't random, so the model predicts where regulators find failures, not true quality.
- Factories never flagged aren't in the data at all.

**Where did you use AI, and how did you stop it making things up?**
Only for pages with no readable table. The LLM copies values from one page, and any row whose batch number and drug name don't appear in that page's text is thrown away. Every row it touched is logged and marked.

**How would this run in production?**
The monthly Prefect flow (or Task Scheduler) runs collect and build with `--strict` checks, then exports for Power BI. New months are cheap because downloads are cached.

**Why does this matter to a bank?**
Banks lend to and insure pharma manufacturers, and ESG and credit teams need early signals of regulatory risk. The same skills apply to bank data too: cleaning messy public records, matching entities across sources, and checking data quality.

## Resume bullets (fill in your real numbers after the real run)

- Built an end-to-end pipeline that collects and parses [N] monthly CDSCO drug-quality alerts (2019 to 2026, two PDF formats and a JSON portal) into a DuckDB warehouse with staging, intermediate and mart layers, validating [X]% of files against their stated totals.
- Resolved [N] manufacturer spellings into [N] factory sites using blocked fuzzy matching and union-find, with [X]% matching accuracy on a 100-pair hand-labelled sample.
- Trained a time-split gradient-boosting model to rank factories for re-flagging. It reached precision@20 of [X] vs [Y] for a recent-history baseline over 12 test months, and feeds a Streamlit and Power BI dashboard.
