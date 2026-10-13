"""Where is a manufacturer? State and district from its address.

Explicit state words in the address win ("Himachal Pradesh", "(H.P.)").
When there are none, the 6-digit PIN code's first digits give the postal
circle, which mostly matches the state. Industrial towns map to their district
(Baddi -> Solan) because CDSCO addresses often skip the district.
"""
from __future__ import annotations

import re

STATE_PATTERNS = [
    ("Himachal Pradesh", r"himachal|\(\s*h\.?\s*p\.?\s*\)|\bh\.p\.?(?=[\s,)\-]|$)"),
    ("Uttarakhand", r"uttarakhand|uttaranchal|\(\s*u\.?\s*k\.?\s*\)|\(uk\)"),
    ("Uttar Pradesh", r"uttar\s*pradesh|\(\s*u\.?\s*p\.?\s*\)|\bu\.p\.(?=[\s,)\-]|$)"),
    ("Madhya Pradesh", r"madhya\s*pradesh|\(\s*m\.?\s*p\.?\s*\)|\bm\.p\.(?=[\s,)\-]|$)"),
    ("Andhra Pradesh", r"andhra\s*pradesh|\(\s*a\.?\s*p\.?\s*\)"),
    ("Arunachal Pradesh", r"arunachal"),
    ("Haryana", r"haryana|\(\s*hr\s*\)|\(hry\)"),
    ("Punjab", r"punjab"),
    ("Gujarat", r"gujarat|gujrat|\(\s*gj\s*\)"),
    ("Maharashtra", r"maharashtra|\(\s*m\.?\s*s\.?\s*\)|\(mh\)"),
    ("Telangana", r"telangana|\(\s*t\.?\s*s\.?\s*\)|\(tg\)"),
    ("Karnataka", r"karnataka"),
    ("Tamil Nadu", r"tamil\s*nadu|\(\s*t\.?\s*n\.?\s*\)"),
    ("Kerala", r"kerala"),
    ("Goa", r"\bgoa\b"),
    ("Sikkim", r"sikkim"),
    ("West Bengal", r"west\s*bengal|\(\s*w\.?\s*b\.?\s*\)"),
    ("Bihar", r"\bbihar\b"),
    ("Jharkhand", r"jharkhand"),
    ("Odisha", r"odisha|orissa"),
    ("Assam", r"\bassam\b"),
    ("Rajasthan", r"rajasthan"),
    ("Jammu and Kashmir", r"jammu|kashmir|\(\s*j\s*&\s*k\s*\)"),
    ("Delhi", r"\bdelhi\b"),
    ("Chandigarh", r"chandigarh"),
    ("Chhattisgarh", r"chhattisgarh|chattisgarh"),
    ("Puducherry", r"puducherry|pondicherry"),
    ("Dadra and Nagar Haveli and Daman and Diu", r"daman|silvassa|dadra"),
]

# First three digits that need an exact answer, then first two digits.
PIN3 = {
    "160": "Chandigarh", "403": "Goa", "396": "Dadra and Nagar Haveli and Daman and Diu",
    "605": "Puducherry", "737": "Sikkim", "744": "Andaman and Nicobar Islands",
    "248": "Uttarakhand", "249": "Uttarakhand", "246": "Uttarakhand", "263": "Uttarakhand",
    "790": "Arunachal Pradesh", "791": "Arunachal Pradesh", "792": "Arunachal Pradesh",
    "793": "Meghalaya", "794": "Meghalaya", "795": "Manipur", "796": "Mizoram",
    "797": "Nagaland", "798": "Nagaland", "799": "Tripura",
}
PIN2 = {
    "11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab", "15": "Punjab",
    "16": "Punjab", "17": "Himachal Pradesh", "18": "Jammu and Kashmir", "19": "Jammu and Kashmir",
    "20": "Uttar Pradesh", "21": "Uttar Pradesh", "22": "Uttar Pradesh", "23": "Uttar Pradesh",
    "24": "Uttar Pradesh", "25": "Uttar Pradesh", "26": "Uttar Pradesh", "27": "Uttar Pradesh",
    "28": "Uttar Pradesh", "30": "Rajasthan", "31": "Rajasthan", "32": "Rajasthan",
    "33": "Rajasthan", "34": "Rajasthan", "36": "Gujarat", "37": "Gujarat", "38": "Gujarat",
    "39": "Gujarat", "40": "Maharashtra", "41": "Maharashtra", "42": "Maharashtra",
    "43": "Maharashtra", "44": "Maharashtra", "45": "Madhya Pradesh", "46": "Madhya Pradesh",
    "47": "Madhya Pradesh", "48": "Madhya Pradesh", "49": "Chhattisgarh", "50": "Telangana",
    "51": "Andhra Pradesh", "52": "Andhra Pradesh", "53": "Andhra Pradesh", "56": "Karnataka",
    "57": "Karnataka", "58": "Karnataka", "59": "Karnataka", "60": "Tamil Nadu",
    "61": "Tamil Nadu", "62": "Tamil Nadu", "63": "Tamil Nadu", "64": "Tamil Nadu",
    "67": "Kerala", "68": "Kerala", "69": "Kerala", "70": "West Bengal", "71": "West Bengal",
    "72": "West Bengal", "73": "West Bengal", "74": "West Bengal", "75": "Odisha", "76": "Odisha",
    "77": "Odisha", "78": "Assam", "80": "Bihar", "81": "Jharkhand", "82": "Jharkhand",
    "83": "Jharkhand", "84": "Bihar", "85": "Bihar",
}

# Pharma towns -> district (addresses often name the town but not the district)
TOWN_DISTRICT = {
    "baddi": "Solan", "nalagarh": "Solan", "barotiwala": "Solan", "parwanoo": "Solan",
    "solan": "Solan", "paonta sahib": "Sirmaur", "kala amb": "Sirmaur", "sirmaur": "Sirmaur",
    "sirmour": "Sirmaur", "haridwar": "Haridwar", "roorkee": "Haridwar",
    "bhagwanpur": "Haridwar", "selaqui": "Dehradun", "dehradun": "Dehradun",
    "bahadurgarh": "Jhajjar", "panchkula": "Panchkula", "vatva": "Ahmedabad",
    "ahmedabad": "Ahmedabad", "vadodara": "Vadodara", "baroda": "Vadodara",
    "bhavnagar": "Bhavnagar", "pithampur": "Dhar", "indore": "Indore", "namthang": "Namchi",
    "rangpo": "Pakyong", "verna": "South Goa", "jeedimetla": "Medchal", "pimpri": "Pune",
    "pune": "Pune", "patna": "Patna", "kolkata": "Kolkata", "ghaziabad": "Ghaziabad",
    "daman": "Daman", "silvassa": "Dadra and Nagar Haveli",
}

# For the pharma districts above: which state they are in. Used to correct a
# PIN-based guess (e.g. 247xxx covers Roorkee in Uttarakhand AND Saharanpur in UP).
DISTRICT_STATE = {
    "Solan": "Himachal Pradesh", "Sirmaur": "Himachal Pradesh", "Haridwar": "Uttarakhand",
    "Dehradun": "Uttarakhand", "Jhajjar": "Haryana", "Panchkula": "Haryana",
    "Ahmedabad": "Gujarat", "Vadodara": "Gujarat", "Bhavnagar": "Gujarat",
    "Dhar": "Madhya Pradesh", "Indore": "Madhya Pradesh", "Namchi": "Sikkim", "Pakyong": "Sikkim",
    "South Goa": "Goa", "Medchal": "Telangana", "Pune": "Maharashtra", "Patna": "Bihar",
    "Kolkata": "West Bengal", "Ghaziabad": "Uttar Pradesh", "Daman": "Dadra and Nagar Haveli and Daman and Diu",
}

_PIN = re.compile(r"(?<!\d)([1-8]\d{2})\s?(\d{3})(?!\d)")


def find_pin(text: str) -> str | None:
    pins = [a + b for a, b in _PIN.findall(text or "")]
    return pins[-1] if pins else None


def state_from_pin(pin: str | None) -> str | None:
    if not pin:
        return None
    return PIN3.get(pin[:3]) or PIN2.get(pin[:2])


def find_state(text: str) -> tuple[str | None, str]:
    """Return (state, how) where how is 'text', 'pin' or 'none'."""
    t = (text or "").lower()
    hits = []
    for state, pat in STATE_PATTERNS:
        for m in re.finditer(pat, t):
            hits.append((m.start(), state))
    if hits:
        return max(hits)[1], "text"  # the last mention is the factory's state
    st = state_from_pin(find_pin(text))
    return (st, "pin") if st else (None, "none")


def find_district(text: str) -> str | None:
    t = (text or "").lower()
    m = re.search(r"\b(?:district|distt\.?|dist\.?)(?![a-z])\s*[-:.]?\s*([a-z][a-z ]{2,25}?)(?=\s*[,(\-]|\s*\d|\s*$)", t)
    if m:
        d = m.group(1).strip()
        return TOWN_DISTRICT.get(d, d.title())
    for town, district in TOWN_DISTRICT.items():
        if re.search(rf"\b{re.escape(town)}\b", t):
            return district
    return None
