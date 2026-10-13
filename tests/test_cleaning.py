import json

import pandas as pd
import pytest

from nsq import geo
from nsq.clean import clean_drug, clean_manufacturer, clean_reporter, name_key, reason_categories, split_manufacturer
from nsq.entities import bcubed, resolve_companies
from nsq.portal import _find_records, map_record
from nsq.warehouse import tag_qr_brand


@pytest.mark.parametrize("a,b", [
    ("Arvex Life Sciences Private Limited", "ARVEX LIFESCIENCES PVT. LTD."),
    ("Arvex Lifesciences (P) Ltd.", "Arvex Lifesceinces Pvt Ltd"),
    ("Kestrel Pharmaceuticals Ltd", "Kestrel Pharma Limited"),
    ("Bluecrest Laboratories", "Bluecrest Labs"),
])
def test_name_key_variants_match(a, b):
    assert name_key(a) == name_key(b)


def test_split_manufacturer():
    name, addr = split_manufacturer("M/s. Ozone Pharmaceuticals, Village: Katha, Baddi, Distt. Solan (H.P.) 173 205")
    assert name == "Ozone Pharmaceuticals"
    assert addr.startswith("Village")


def test_clean_manufacturer_location():
    m = clean_manufacturer("M/s. Ozone Pharmaceuticals, Village: Katha, Baddi, Distt. Solan (H.P.) 173 205")
    assert m["mfr_pin"] == "173205"
    assert m["mfr_state"] == "Himachal Pradesh" and m["mfr_district"] == "Solan"


def test_district_fixes_ambiguous_pin():
    # 247xxx is shared by Roorkee (Uttarakhand) and Saharanpur (Uttar Pradesh)
    m = clean_manufacturer("Abc Remedies Pvt Ltd, Roorkee-247667")
    assert m["mfr_state"] == "Uttarakhand" and m["mfr_state_source"] == "district"
    assert geo.state_from_pin("247667") == "Uttar Pradesh"


def test_clean_drug():
    d = clean_drug("Pantoprazole Gastro-Resistant Tablets IP 40 mg (Pantoshang-40)")
    assert d["drug_generic"] == "pantoprazole" and d["drug_form"] == "tablet"
    assert d["drug_brand"] == "Pantoshang-40" and d["drug_strength"] == "40 mg"
    d = clean_drug("Amoxycillin and Potassium Clavulanate Tablets IP 625 mg")
    assert d["drug_generic"] == "amoxycillin + potassium clavulanate"
    assert clean_drug("Ondansetron Injection IP 2 mg/ml")["drug_form"] == "injection"


def test_reason_categories():
    assert reason_categories("Assay and Dissolution") == ["assay", "dissolution"]
    assert reason_categories("Spurious - disowned by the firm")[0] == "spurious"
    assert reason_categories("") == ["not stated"]


def test_clean_reporter_old_and_new():
    assert clean_reporter("Karnataka", "", "state") == {"reporting_state": "Karnataka", "reporting_lab": None}
    old = clean_reporter("RDTL, Guwahati", "Drug Control Department, Assam", "state")
    assert old == {"reporting_state": "Assam", "reporting_lab": "RDTL, Guwahati"}
    assert clean_reporter("CDL, Kolkata", "", "central")["reporting_lab"] == "CDL, Kolkata"


def test_entity_resolution():
    keys = pd.DataFrame({
        "mfr_key": ["arvex lifesciences", "arvx lifesciences", "arvex pharma", "brelix remedies"],
        "pins": [{"173205"}, {"173205"}, {"400069"}, {"173205"}],
    })
    root = resolve_companies(keys)
    assert root["arvex lifesciences"] == root["arvx lifesciences"]  # typo at the same factory
    assert root["arvex lifesciences"] != root["arvex pharma"]      # different name, different place
    assert root["brelix remedies"] != root["arvex lifesciences"]


def test_bcubed_perfect_and_merged():
    t = pd.Series(["a", "a", "b"])
    assert bcubed(pd.Series([1, 1, 2]), t)["bcubed_f1"] == 1.0
    merged = bcubed(pd.Series([1, 1, 1]), t)
    assert merged["bcubed_recall"] == 1.0 and merged["bcubed_precision"] < 1.0


def test_portal_field_mapping_and_pending():
    payload = {"result": {"rows": [{"SlNo": 1, "Product Name": "Paracetamol Tablets IP 500 mg",
                                    "Batch Number": "X1", "Mfg Date": "05/2025", "Expiry Date": "04/2027",
                                    "Manufactured By": "M/s A, Baddi-173205", "NSQ Result": "Assay",
                                    "Reporting Source": "State", "State Name": "Kerala"}]},
               "pendingStates": ["Goa", "Bihar"]}
    recs, pending = _find_records(json.loads(json.dumps(payload)))
    assert pending == ["Goa", "Bihar"] and len(recs) == 1
    m = map_record(recs[0])
    assert m["batch_no"] == "X1" and m["drug_name"].startswith("Paracetamol")
    assert m["manufacturer_raw"].startswith("M/s A") and m["reason_raw"] == "Assay"


def test_qr_brand_tagging():
    brands = ["Feverol 650", "Calvita 500"]
    assert tag_qr_brand("Paracetamol Tablets IP 650 mg (Feverol-650)", "Feverol-650", brands) == "Feverol 650"
    assert tag_qr_brand("Paracetamol Tablets IP 650 mg", "", brands) is None
