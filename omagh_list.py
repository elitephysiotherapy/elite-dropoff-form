"""Pull the Omagh-clinic catchment out of Cliniko for the launch campaign.

Catchment rule (Martin, 2026-08-17, v3):
  Postcode prefix: BT75 BT76 BT77 BT78 BT79 BT82 BT94
  OR the address names one of TOWNS (catches records with no postcode).

Street names are stripped before town matching, so "24 Gortin Road, Dungannon"
does not read as Gortin. Dromore and Greencastle exist in Co Down as well as
Co Tyrone, so they only count when the postcode is blank, in-catchment, or the
address says Tyrone/Omagh.

Excludes deleted and merged records. Read-only against Cliniko.
"""

import csv
import os
import re
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv(override=True)
SESSION = requests.Session()
SESSION.auth = (os.environ["CLINIKO_API_KEY"], "")
SESSION.headers.update({
    "User-Agent": os.environ["CLINIKO_USER_AGENT"],
    "Accept": "application/json",
})
BASE = f"https://api.{os.environ['CLINIKO_SHARD']}.cliniko.com/v1"

POSTCODES = ("BT75", "BT76", "BT77", "BT78", "BT79", "BT82", "BT94")

# Canonical name -> regex alternatives seen in free-typed addresses.
TOWNS = {
    "Ballygawley": r"ballygawley",
    "Carrickmore": r"carrickmore|carrick more|termonmaguirc",
    "Strabane": r"strabane",
    "Loughmacrory": r"loughmacrory|lough macrory",
    "Mountfield": r"mountfield",
    "Killyclogher": r"killyclogher|killy clogher",
    "Dromore": r"dromore",
    "Augher": r"augher",
    "Eskra": r"eskra",
    "Fintona": r"fintona",
    "Newtownstewart": r"newtownstewart|newtown stewart",
    "Gortin": r"gortin",
    "Plumbridge": r"plumbridge",
    "Glenelly": r"glen+elly",
    "Greencastle": r"greencastle|green castle",
    "Trillick": r"trillick",
    # Not on Martin's list, but a no-postcode Omagh address should not be lost.
    "Omagh": r"omagh",
}

# Ambiguous with Co Down namesakes - need corroboration.
AMBIGUOUS = ("Dromore", "Greencastle")
WRONG_COUNTY = ("BT25", "BT34")

STREET_TYPES = (r"road|rd|lane|ln|street|st|park|avenue|ave|drive|dr|way|"
                r"court|crescent|cresent|close|gardens|terrace|manor|heights|"
                r"view|walk|hill|brae|meadows|grove|villas?|mews|row|place")
# One optional word may sit between the name and the type ("Gortin Water Lane"),
# but only across plain spaces - a comma means a new address line, so
# "69 Glenhoy Road, Ballygawley, Errigal" keeps its Ballygawley.
STREET_RE = re.compile(
    r"\b(?:" + "|".join(TOWNS.values()) + r"|dungannon|pomeroy|donaghmore|"
    r"cookstown|enniskillen|derry|londonderry)[ ]+(?:\w+[ ]+)?(?:"
    + STREET_TYPES + r")\b", re.I)

# A town name with an out-of-catchment postcode is usually a different place
# (Glenelly Villas is in Magherafelt). Allow adjacent Tyrone districts, which
# is where Ballygawley sits, plus single-letter typos of catchment codes.
NEAR_OK = ("BT70", "BT71", "BT72")
TYPO_RE = re.compile(r"^B.(?:7[5-9]|82|94)")


def pc_supports_town(pc, pc_hit):
    if not pc or pc_hit:
        return True
    return pc.startswith(NEAR_OK) or bool(TYPO_RE.match(pc))

STAMP = f"{datetime.now():%Y-%m-%d}"
OUT_CSV = os.path.expanduser(f"~/Downloads/omagh_catchment_{STAMP}.csv")


def fetch_all(path, params=None):
    url = f"{BASE}{path}"
    qp = list((params or {}).items()) + [("per_page", 100)]
    first = True
    while url:
        r = None
        for attempt in range(12):
            try:
                r = SESSION.get(url, params=qp if first else None, timeout=30)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                time.sleep(min(5 * (attempt + 1), 60))
                continue
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "5")) + 1)
                continue
            break
        first = False
        if r is None or r.status_code != 200:
            print(f"ERROR on {url}: {r.status_code if r else 'no response'}")
            sys.exit(1)
        data = r.json()
        coll_key = next(k for k, v in data.items() if isinstance(v, list))
        for item in data[coll_key]:
            yield item
        url = (data.get("links") or {}).get("next")


def classify(p):
    """Return (town, matched_on) if in catchment, else (None, None)."""
    pc = re.sub(r"\s+", "", (p.get("post_code") or "")).upper()
    blob = " ".join(str(p.get(f) or "") for f in
                    ("address_1", "address_2", "address_3", "city", "state"))
    clean = STREET_RE.sub(" ", blob).lower()
    pc_hit = next((c for c in POSTCODES if pc.startswith(c)), None)

    town = None
    for name, pattern in TOWNS.items():
        if not re.search(rf"\b(?:{pattern})\b", clean):
            continue
        if not pc_supports_town(pc, pc_hit):
            continue
        if name in AMBIGUOUS and pc.startswith(WRONG_COUNTY):
            continue
        town = name
        break

    if pc_hit and town:
        return town, f"{pc_hit} + town"
    if pc_hit:
        return (p.get("city") or "").strip(), pc_hit
    if town:
        return town, "town only (no matching postcode)"
    return None, None


def main():
    rows = []
    scanned = 0
    for p in fetch_all("/patients"):
        scanned += 1
        if scanned % 4000 == 0:
            print(f"  scanned {scanned}...")
            sys.stdout.flush()
        if p.get("deleted_at") or p.get("merged_at"):
            continue
        town, matched = classify(p)
        if not matched:
            continue
        phones = p.get("patient_phone_numbers") or []
        mobile = next((n.get("number") for n in phones
                       if n.get("phone_type") == "Mobile"), "")
        if not mobile:  # fall back to any number that looks like a mobile
            mobile = next((n.get("number") for n in phones
                           if re.sub(r"\D", "", n.get("number") or "")
                           .lstrip("0").startswith(("447", "7", "3538"))), "")
        # What a "Hi {first_name}," should actually say: their preferred name
        # if they gave one, and fix the handful typed in lower case.
        given = (p.get("preferred_first_name") or "").strip() \
            or (p.get("first_name") or "").strip()
        if given and given[0].islower():
            given = given[0].upper() + given[1:]
        rows.append({
            "First name": given,
            "Name": " ".join(x for x in (p.get("first_name"),
                                         p.get("last_name")) if x),
            "Email": (p.get("email") or "").strip(),
            "Mobile": mobile or "",
            "Town": town,
            "Postcode": p.get("post_code") or "",
            "Address": ", ".join(x for x in (p.get("address_1"),
                                             p.get("address_2"),
                                             p.get("address_3")) if x),
            "Matched on": matched,
            "Email marketing OK": p.get("accepted_email_marketing"),
            "SMS marketing OK": p.get("accepted_sms_marketing"),
            "Patient ID": p["id"],
        })

    rows.sort(key=lambda r: (r["Postcode"][:4].upper(), r["Name"]))
    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\nscanned {scanned} records -> {len(rows)} in catchment")
    print(f"  with an email:  {sum(1 for r in rows if r['Email'])}")
    print(f"  with a mobile:  {sum(1 for r in rows if r['Mobile'])}")
    print(f"  email opt-in:   {sum(1 for r in rows if r['Email marketing OK'] and r['Email'])}")
    print(f"  SMS opt-in:     {sum(1 for r in rows if r['SMS marketing OK'] and r['Mobile'])}")
    by = {}
    for r in rows:
        by.setdefault(r["Matched on"], 0)
        by[r["Matched on"]] += 1
    print("\nmatched on:")
    for k in sorted(by, key=lambda k: -by[k]):
        print(f"  {k:36s} {by[k]:5d}")
    print(f"\nwrote {OUT_CSV}")


if __name__ == "__main__":
    main()
