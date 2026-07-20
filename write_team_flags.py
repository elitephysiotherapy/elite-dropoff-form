"""Write engine-reconciliation flags into the 'team' tab of the June drop-off sheet:
  - new column R note column
  - flag the 14 reattribution rows (already on sheet, different treating physio)
  - append the 19 engine-caught patients not on the sheet, under a separator.
"""
import json, re
import gspread, phase1_fetch as p1

SHEET_ID = "1xFarmlYsTZIZ5zu8cRLMa-WypjZNqwEsIaX1_1lkw4M"
DATA = "/private/tmp/claude-501/-Users-martinloughran/c0f5ec42-16a3-483d-9958-6dd7a236e296/scratchpad/june_recon.json"
COL_R = 18  # column R
HEADER = "Attribute to another physio / engine note (Claude)"
SHORT2FULL = {
    "Aoife": "Aoife O'Kane", "Daire": "Daire McKenna", "Erin": "Erin McNicholl",
    "Molaí": "Molaí Smith", "Shannagh": "Shannagh Conwell", "Sinead": "Sinead McGill",
    "Marty": "Martin Loughran", "Julie": "Julie McVey",
}
def nkey(s): return re.sub(r"[^a-z]", "", (s or "").lower())

d = json.load(open(DATA))
extras, reattrib = d["extras"], d["reattrib"]

gc = gspread.authorize(p1._sheets_credentials())
ws = gc.open_by_key(SHEET_ID).worksheet("team")
vals = ws.get_all_values()

updates = []  # list of (a1_range, [[...]])

# 1. header
updates.append((gspread.utils.rowcol_to_a1(1, COL_R), [[HEADER]]))

# 2. reattribution flags on existing rows (match by name in col C, index 2)
reattrib_by_key = {nkey(r["name"]): r for r in reattrib}
flagged = set()
for i, row in enumerate(vals, start=1):
    if i == 1:
        continue
    name = row[2] if len(row) > 2 else ""
    k = nkey(name)
    if k in reattrib_by_key:
        updates.append((gspread.utils.rowcol_to_a1(i, COL_R), [[reattrib_by_key[k]["note"]]]))
        flagged.add(k)
missing = [r["name"] for r in reattrib if nkey(r["name"]) not in flagged]

# 3. append separator + 19 extras starting at row 121
START = 121
sep = [""] * 18
sep[2] = "──  19 below added by Claude — genuine June drop-offs NOT on your original list  ──"
block = [sep]
for e in extras:
    r = [""] * 18
    r[0] = e["appt_date"]          # A Appointment Date
    r[1] = e["cancel_date"]        # B Cancellation Date
    r[2] = e["name"]               # C Patient Name
    r[3] = SHORT2FULL.get(e["engine_physio"], e["engine_physio"])  # D Physio
    r[4] = e["clinic"]             # E Clinic
    r[5] = e["appt_type"]          # F Appointment Type
    r[6] = e["dropoff_type"]       # G Drop-off Type
    r[17] = e["note"]              # R note
    block.append(r)
end_row = START + len(block) - 1
rng = f"A{START}:R{end_row}"
updates.append((rng, block))

# apply
ws.batch_update([{"range": rng_, "values": v_} for rng_, v_ in updates],
                value_input_option="USER_ENTERED")

print(f"Done. Header set on col R. Flagged {len(flagged)} reattribution rows "
      f"(of {len(reattrib)}). Appended {len(extras)} rows at {START}-{end_row}.")
if missing:
    print("  [warn] reattribution names not found on sheet:", missing)
