"""Synthetic tests for the team drop-off sheet — the rules that decide whose
copy of a feedback cell wins, and what the physios end up seeing.

No network: these exercise the pure helpers, not the Sheets round trip.
"""
import team_sheet as ts

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got={got!r} want={want!r}")
    return ok


def master(aid="1", action="", notes="", **kw):
    row = {c: "" for c in __import__("phase1_fetch").SHEET_COLUMNS}
    row.update({
        "aid": aid, "tab": "W/C 14 Sep 2026", "row": 2, "week": "14 Sep",
        "what": "Cancelled", "patient": "Joe Bloggs", "physio": "Molaí Smith",
        "appointment_date": "2026-09-16 13:00", "appointment_type": "2. Review",
        "session_number": "4", "body_area": "Knee",
        "reactivation_notes": "reception rang 17/9",
        "cancellation_reason": "Feeling Better",
        "physio_action": action, "physio_reactivation_notes": notes,
    })
    row.update(kw)
    return row


def team(action="", notes=""):
    return {"action": action, "notes": notes}


print("\n1. Whose copy of a feedback cell wins:")

# physio typed something new -> it goes to the master
rows = [master(action="", notes="")]
changes = ts.reconcile(rows, {"1": team("Rebooked", "back in Tuesday")})
check("physio's entry is pushed to the master",
      sorted(c[1] for c in changes),
      ["physio_action", "physio_reactivation_notes"])
check("master row updated in place so the rebuild agrees",
      (rows[0]["physio_action"], rows[0]["physio_reactivation_notes"]),
      ("Rebooked", "back in Tuesday"))

# physio changed their mind -> the newer wording wins
rows = [master(action="Called – voicemail", notes="no answer")]
changes = ts.reconcile(rows, {"1": team("Called – spoke to them", "spoke, rebooking")})
check("a physio's edit overwrites the master's older value",
      changes, [("1", "physio_action", "Called – spoke to them"),
                ("1", "physio_reactivation_notes", "spoke, rebooking")])

# blank on the team sheet must NOT wipe the master (Martin types there directly)
rows = [master(action="Rebooked", notes="typed into the master by Sinead")]
changes = ts.reconcile(rows, {"1": team("", "")})
check("a blank team cell pushes nothing", changes, [])
check("a blank team cell does not wipe the master",
      rows[0]["physio_reactivation_notes"], "typed into the master by Sinead")

# identical on both sides -> no write, no wasted quota
rows = [master(action="Text sent", notes="txt 18/9")]
check("matching values write nothing",
      ts.reconcile(rows, {"1": team("Text sent", "txt 18/9")}), [])

# a row the physios can't see yet is left alone
rows = [master(aid="99")]
check("a master row with no team row is untouched",
      ts.reconcile(rows, {"1": team("Rebooked", "x")}), [])


print("\n2. What the physios see:")

row = ts._team_row(master(action="Rebooked", notes="back Tuesday"))
check("column count matches the header", len(row), len(ts.HEADER))
check("date is shown without the time", row[1], "2026-09-16")
check("patient name", row[2], "Joe Bloggs")
check("reception's notes are carried across so nobody double-calls",
      row[ts.N_INFO - 1], "reception rang 17/9")
check("the patient's stated reason travels with the row, so a red row is "
      "never unexplained", row[7], "Feeling Better")
check("feedback sits in the two editable columns",
      (row[ts.I_ACTION], row[ts.I_NOTES]), ("Rebooked", "back Tuesday"))
check("appointment_id is last, for the sync to match on",
      row[ts.I_KEY], "1")

check("jargon is translated for the physios",
      [ts.DROPOFF_LABELS[k] for k in ("iadnr", "did_not_attend", "cancelled")],
      ["First appt — no rebook", "No-show", "Cancelled"])
check("every drop-off type the sheet uses has a label",
      sorted(ts.DROPOFF_LABELS),
      ["cancelled", "did_not_attend", "iacna", "iadna", "iadnr"])

print("\n3. Layout guards:")
check("only two columns are editable", len(ts.PHYSIO_COLS), 2)
check("the editable columns are K and L",
      (chr(65 + ts.I_ACTION), chr(65 + ts.I_NOTES)), ("K", "L"))
check("the hidden key and status columns sit past them",
      (chr(65 + ts.I_KEY), chr(65 + ts.I_STATUS)), ("M", "N"))
check("dropdown options", ts.PHYSIO_ACTIONS[0], "Called – spoke to them")
check("patients already rebooked drop off the list", ts.SKIP_STATUS, {"reactivated"})

print("\n4. Rows move without sliding a patient under anyone's cursor:")


def apply(current, deletes, inserts):
    """Replay the plan the way Sheets would, to prove where rows end up."""
    rows = list(current)
    for i in deletes:
        del rows[i]
    for aid, i in inserts:
        rows.insert(i, aid)
    return rows


# a new week's drop-offs arrive -> on top, everyone else shifts down intact
cur = ["b1", "b2", "a1", "a2"]
want = ["c1", "c2", "b1", "b2", "a1", "a2"]
d, ins, final = ts.plan_rows(cur, want)
check("new drop-offs go on top", final[:2], ["c1", "c2"])
check("in master order", final, want)
check("nothing deleted when nothing left", d, [])
check("replaying the plan gives the same order", apply(cur, d, ins), final)

# someone reactivated mid-list -> only that row goes
cur = ["c1", "b1", "b2", "a1"]
d, ins, final = ts.plan_rows(cur, ["c1", "b1", "a1"])
check("a reactivated patient's row is deleted, nobody else's",
      final, ["c1", "b1", "a1"])
check("deleted by position, and only once", d, [2])

# weekly rollover: oldest week drops, new week lands, same run
cur = ["b1", "b2", "a1", "a2"]
want = ["c1", "b1", "b2"]
d, ins, final = ts.plan_rows(cur, want)
check("deletes run bottom-up so indices stay valid", d, [3, 2])
check("rollover: new on top, old gone", final, want)
check("replay agrees after deletes and inserts together", apply(cur, d, ins), want)

# a late row for an older week slots into its week, not the top
cur = ["c1", "b1", "a1"]
d, ins, final = ts.plan_rows(cur, ["c1", "b1", "b9", "a1"])
check("a late row lands inside its own week", final, ["c1", "b1", "b9", "a1"])

# existing rows are never reordered, even if the master order differs
cur = ["b2", "b1"]
d, ins, final = ts.plan_rows(cur, ["b1", "b2"])
check("rows already on the sheet are never shuffled", (d, ins, final),
      ([], [], ["b2", "b1"]))

# stray empty rows inside the data are tidied away
d, ins, final = ts.plan_rows(["b1", "", "a1"], ["b1", "a1"])
check("an empty row inside the data is removed", (d, final), ([1], ["b1", "a1"]))

check("sync runs every 30 minutes", ts.SYNC_EVERY, 1800)


print("\n5. One saved view per physio, straight from the roster:")
from datetime import date
views = ts.physio_views_wanted(today=date(2026, 9, 21))
check("everyone on the team gets a view",
      sorted(v.replace(ts.VIEW_SUFFIX, "") for v in views),
      sorted(["Marty", "Julie", "Sinead", "Erin", "Aoife", "Ciara", "Molaí",
              "Shannagh", "Conor", "Kelly"]))
check("a leaver has no view (Daire left 2 Jul)", "Daire" + ts.VIEW_SUFFIX in views, False)
check("a physio with two Cliniko names matches both",
      views["Marty" + ts.VIEW_SUFFIX], ["Martin Loughran", "Martin Loughran CS"])
check("a starter appears from their start date, not before",
      ("Kelly" + ts.VIEW_SUFFIX in ts.physio_views_wanted(today=date(2026, 8, 10)),
       "Kelly" + ts.VIEW_SUFFIX in ts.physio_views_wanted(today=date(2026, 8, 11))),
      (False, True))
check("the leaver kept her view while she was here",
      "Daire" + ts.VIEW_SUFFIX in ts.physio_views_wanted(today=date(2026, 6, 30)), True)

print(f"\n{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
