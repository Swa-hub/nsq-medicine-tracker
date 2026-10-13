import random

import pandas as pd
import pytest

from nsq import sample
from nsq.parse_pdf import map_header, parse_pdf, split_combined_cell, stated_totals
from nsq.scrape_index import classify_title, decode_num_id, parse_alerts_table
from nsq.textutil import find_month_year, parse_month_year


@pytest.mark.parametrize("raw,expected", [
    ("05/2024", "2024-05"), ("5/24", "2024-05"), ("05-2024", "2024-05"), ("2024-05", "2024-05"),
    ("May-24", "2024-05"), ("May 2024", "2024-05"), ("Apr-24", "2024-04"), ("01.2025", "2025-01"),
    ("12/05/2024", "2024-05"), ("Sept-23", "2023-09"), ("", None), ("N/A", None), ("13/2024", None),
])
def test_parse_month_year(raw, expected):
    assert parse_month_year(raw) == expected


def test_find_month_year():
    assert find_month_year("NSQ ALERT FOR THE MONTH OF MAY-2025") == (2025, 5)
    assert find_month_year("Drug Alert list for month of Feb 2021") == (2021, 2)


@pytest.mark.parametrize("title,doc_type,year,month,revised", [
    ("CDSCO NSQ ALERT FOR THE MONTH OF June 2025", "central_nsq", 2025, 6, False),
    ("STATE NSQ ALERT FOR THE MONTH OF June 2025", "state_nsq", 2025, 6, False),
    ("List of spurious Drugs for the month of June-2025", "spurious", 2025, 6, False),
    ("Revise list drug alert May-2025", "combined", 2025, 5, True),
    ("NSQ May 2024 CDSCO Labs", "central_nsq", 2024, 5, False),
    ("Drug Alert list for month of April 2023", "combined", 2023, 4, False),
    ("List of Drugs, Medical Devices and Cosmetics declared as Not of Standard Quality/Spurious/"
     "Adulterated/Misbranded, For the Month of August, 2018", "combined", 2018, 8, False),
    ("Samples declared NSQ 2017-2023", "compiled", 2023, None, False),
    ("Medical Device Alert dated 08 May 2023", "other", 2023, 5, False),
    ("NSQ of Typhoid Polysaccharide Vaccine TYPBAR", "product_alert", None, None, False),
])
def test_classify_title(title, doc_type, year, month, revised):
    c = classify_title(title)
    assert (c["doc_type"], c["report_year"], c["report_month"], c["is_revised"]) == (doc_type, year, month, revised)


def test_decode_num_id():
    assert decode_num_id("https://cdsco.gov.in/x.jsp?num_id=MTI5Mjc=") == 12927
    assert decode_num_id("https://cdsco.gov.in/x.jsp") is None


def test_alerts_table_and_central_refinement():
    html = """<table><tr><th>S.no</th><th>Title</th><th>Release Date</th><th>Download Pdf</th><th>Pdf Size</th></tr>
    <tr><td>1</td><td>State Drug Alert for the Month of December 2024</td><td>2025-Jan-24</td>
        <td><a href="/opencms/x.jsp?num_id=MTIzOTQ=">pdf</a></td><td>613 KB</td></tr>
    <tr><td>2</td><td>Drug Alert for the Month of December 2024</td><td>2025-Jan-24</td>
        <td><a href="/opencms/x.jsp?num_id=MTIzOTM=">pdf</a></td><td>490 KB</td></tr></table>"""
    df = parse_alerts_table(html)
    assert list(df["doc_type"]) == ["state_nsq", "central_nsq"]  # plain list next to a state list = central
    assert df["release_date"].iat[0] == "2025-01-24"
    assert df["url"].iat[0].startswith("https://cdsco.gov.in/opencms/")


def test_split_combined_cell():
    out = split_combined_cell("B. No.: 28021831\nMfg dt: 04/2021\nExp dt: 02/2023\n"
                              "Mfd by: M/s. USV Pvt Ltd, Baddi,\nDistt. Solan (H.P.)")
    assert out["batch_no"] == "28021831"
    assert out["mfg_raw"] == "04/2021" and out["exp_raw"] == "02/2023"
    assert out["manufacturer_raw"].startswith("M/s. USV Pvt Ltd, Baddi")
    assert "Solan" in out["manufacturer_raw"]


def test_data_row_is_never_a_header():
    row = ["33", "Amoxycillin Tablets", "B. No.: FK1 Mfd by: M/s X", "Spurious, disowned this batch",
           "Drug Control Department, Kerala", "CDL, Kasauli"]
    assert map_header(row) is None
    hdr = ["S.No", "Product/Drug Name", "Batch No.", "Manufacturing Date", "Expiry Date",
           "Manufactured By", "NSQ Result", "Reported by State Laboratory"]
    assert set(map_header(hdr).values()) == {"sno", "drug_name", "batch_no", "mfg_raw", "exp_raw",
                                             "manufacturer_raw", "reason_raw", "reported_by"}


def test_stated_totals():
    t = stated_totals("Total number of samples tested 1330\nTotal number of samples declared as "
                      "Not of Standard Quality 45\nTotal number of samples declared as Spurious -")
    assert t["stated_tested"] == 1330 and t["stated_nsq"] == 45 and t["stated_spurious"] == 0


def _tiny_world():
    rng = random.Random(3)
    world = sample._make_world(rng, n_companies=6)
    return sample._simulate(world, [(2023, 5)], rng, __import__("numpy").random.default_rng(3))


def test_parse_old_format_pdf(tmp_path):
    rows = _tiny_world()
    path = tmp_path / "old.pdf"
    sample._write_old(path, 2023, 5, rows, random.Random(1))
    res = parse_pdf(path, {"doc_type": "combined", "report_year": 2023, "report_month": 5,
                           "local_file": "old.pdf"})
    assert res.summary["count_check"] == "ok"
    assert res.summary["rows_parsed"] == len(rows)
    got = {r["batch_no"] for r in res.rows}
    assert got == set(rows["batch_no"])
    assert all(r["mfg_month"] and r["exp_month"] for r in res.rows)


def test_parse_new_format_pdf(tmp_path):
    rows = _tiny_world()
    rows = rows[~rows["spurious"] & ~rows["central"]]
    path = tmp_path / "state.pdf"
    sample._write_new(path, "state", 2023, 5, rows)
    res = parse_pdf(path, {"doc_type": "state_nsq", "report_year": 2023, "report_month": 5,
                           "local_file": "state.pdf"})
    df = pd.DataFrame(res.rows)
    assert len(df) == len(rows)
    assert (df["section"] == "state").all()
    assert set(df["batch_no"]) == set(rows["batch_no"])
    assert df["reported_by"].isin(sample.STATES_REPORTING).all()
