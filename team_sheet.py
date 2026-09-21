#!/usr/bin/env python3
"""Team-facing drop-off sheet — physio feedback, synced both ways.

Why a separate spreadsheet: Google Sheets permissions are per-FILE. There is no
per-tab view permission, so sharing the master workbook with the physios would
show them every tab in it, including the per-physio performance stats —
protecting or hiding a tab only blocks edits, not viewing (File > Download and
File > Make a copy both hand over the lot). So the physios get this file, which
holds nothing but the drop-off names and their own feedback columns.

The bot is the only thing that can see both files, which is what makes the
round trip work:

    master  W/C tabs ──(names, dates, reception's notes)──>  team sheet
    master  Q + R    <──(Physio Action, Physio Notes)──────  team sheet

Physios never touch the master; Sinead and Martin never have to copy anything
across.

Usage:
    python team_sheet.py --build     # one-off: lay out the tab, lock it down
    python team_sheet.py --sync      # push feedback back, pull new drop-offs
    python team_sheet.py --sync --dry-run
    python team_sheet.py --views             # per-physio saved views + links
    python team_sheet.py --sync --relayout   # rebuild rows from the master
                                             # (the master holds every entry)
"""
import fcntl
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime

import gspread

import config
import phase1_fetch as p1

TAB = config.TEAM_SHEET_TAB
WEEKS = config.TEAM_SHEET_WEEKS

# What the physios see. "key" is the master column it comes from; None means
# derived. Only PHYSIO_COLS are editable — everything else is protected.
INFO_COLS = [
    ("Week",           None),
    ("Date",           "appointment_date"),
    ("Patient",        "patient"),
    ("Physio",         "physio"),
    ("Appointment",    "appointment_type"),
    ("Session #",      "session_number"),
    ("What happened",  None),
    # What the patient said when they cancelled. Their words, not a clinical
    # finding — "Feeling Better" is very often a polite way of quitting, so it
    # is context for the call, never a reason to skip one.
    ("Reason given",   "cancellation_reason"),
    ("Body Area",      "body_area"),
    ("Reception notes", "reactivation_notes"),
]
PHYSIO_COLS = [
    ("Physio Action",  "physio_action"),
    ("Physio Notes",   "physio_reactivation_notes"),
]
KEY_COL = ("appointment_id", "appointment_id")
# Hidden, and only there to drive the row colours — the physios shouldn't have
# to read "contact_attempted" to know reception has already tried.
STATUS_COL = ("status", "reactivation_status")
HEADER = [h for h, _ in INFO_COLS + PHYSIO_COLS + [KEY_COL, STATUS_COL]]

N_INFO = len(INFO_COLS)                       # A..I
I_ACTION = N_INFO                             # J (0-based)
I_NOTES = N_INFO + 1                          # K
I_KEY = N_INFO + 2                            # L
I_STATUS = N_INFO + 3                         # M
N_COLS = N_INFO + 4

PHYSIO_ACTIONS = [
    "Called – spoke to them",
    "Called – voicemail",
    "Text sent",
    "Rebooked",
    "Not contacting (reason in notes)",
    "Patient not rebooking",
]

# Sheet jargon the physios shouldn't have to decode.
DROPOFF_LABELS = {
    "cancelled":      "Cancelled",
    "did_not_attend": "No-show",
    "iadnr":          "First appt — no rebook",
    "iacna":          "First appt — cancelled",
    "iadna":          "First appt — no-show",
}
# Patients already back in the diary need no chasing, so they drop off the list.
SKIP_STATUS = {"reactivated"}


def _client():
    return gspread.authorize(p1._sheets_credentials())


def _tab_date(title):
    try:
        return datetime.strptime(title[4:].strip(), "%d %b %Y")
    except ValueError:
        return datetime.min


def read_master(gc):
    """In-scope drop-offs from the master's most recent W/C tabs.

    Returns (rows, index) where rows are dicts in sheet order, newest week
    first, and index maps appointment_id -> (tab title, 1-based row) so a
    push knows which cell to write."""
    sh = gc.open_by_key(p1.SPREADSHEET_ID)
    tabs = sorted([w for w in sh.worksheets() if w.title.startswith("W/C ")],
                  key=lambda w: _tab_date(w.title), reverse=True)[:WEEKS]
    if not tabs:
        raise RuntimeError("no W/C tabs on the master sheet")
    resp = sh.values_batch_get([f"'{w.title}'!A:T" for w in tabs])
    rows, index, skipped = [], {}, []
    for ws, vr in zip(tabs, resp.get("valueRanges", [])):
        vals = vr.get("values", [])
        if not vals:
            continue
        header = [h.strip() for h in vals[0]]
        want = [p1.HEADER_LABELS[c] for c in p1.SHEET_COLUMNS]
        if header[:len(want)] != want:
            raise RuntimeError(f"{ws.title}: unexpected header — refusing to sync")
        col = {c: p1.SHEET_COLUMNS.index(c) for c in p1.SHEET_COLUMNS}
        get = lambda r, c: (r[col[c]].strip() if len(r) > col[c] else "")
        for n, r in enumerate(vals[1:], start=2):
            aid = get(r, "appointment_id")
            if not p1._ID_RE.fullmatch(aid) or aid in index:
                # Not a real id (an old-layout row from the stray writer, whose
                # pulled_at sits where the id belongs — 30 of them shared one
                # timestamp on 21 Sep and collapsed into one "patient"), or a
                # second row for an id already taken. Either would pair a
                # physio's note with the wrong master row.
                if aid:
                    skipped.append(f"{ws.title} row {n}")
                continue
            if get(r, "reactivation_status") in SKIP_STATUS:
                continue
            rows.append({
                "aid": aid,
                "tab": ws.title,
                "row": n,
                "week": ws.title.replace("W/C ", "").replace(" 2026", ""),
                "what": DROPOFF_LABELS.get(get(r, "dropoff_type"),
                                           get(r, "dropoff_type")),
                **{c: get(r, c) for c in p1.SHEET_COLUMNS},
            })
            index[aid] = (ws.title, n)
    if skipped:
        print(f"  skipped {len(skipped)} master row(s) without a usable "
              f"appointment id: {', '.join(skipped[:5])}"
              f"{' …' if len(skipped) > 5 else ''}")
    return rows, index, sh


def _team_row(m):
    """One master row rendered as the physios see it."""
    out = []
    for label, key in INFO_COLS:
        if label == "Week":
            out.append(m["week"])
        elif label == "What happened":
            out.append(m["what"])
        elif label == "Date":
            out.append(m["appointment_date"][:10])
        else:
            out.append(m[key])
    out += [m["physio_action"], m["physio_reactivation_notes"], m["aid"],
            m["reactivation_status"]]
    return out


def is_out_of_order(on_sheet, desired_ids):
    """True if the rows on the sheet aren't in master order — i.e. someone
    sorted it. Only rows present in both are compared, so rows that are about
    to be added or removed never trip it."""
    wanted, present = set(desired_ids), set(on_sheet)
    return ([a for a in on_sheet if a in wanted] !=
            [a for a in desired_ids if a in present])


def plan_rows(current_ids, desired_ids):
    """Work out the row deletes and inserts that turn the sheet's current
    order into the wanted one, without moving any row that stays.

    Returns (deletes, inserts, final):
      deletes — 0-based data-row indices to remove, highest first, so each
                delete leaves the indices of the ones still to come intact;
      inserts — (appointment_id, index) in the order they must be applied,
                each index counted after the deletes and earlier inserts;
      final   — the resulting order of appointment_ids.

    New rows go where the master order puts them, which is the top for a new
    week's drop-offs. Existing rows are never reordered: moving a row is the
    thing that slides a patient out from under someone's cursor."""
    wanted = set(desired_ids)
    deletes = [i for i in range(len(current_ids) - 1, -1, -1)
               if not current_ids[i] or current_ids[i] not in wanted]
    gone = set(deletes)
    order = [a for i, a in enumerate(current_ids) if i not in gone]
    present = set(order)
    inserts = []
    for n, aid in enumerate(desired_ids):
        if aid in present:
            continue
        # just above the next row, in master order, that's already placed
        nxt = next((a for a in desired_ids[n + 1:] if a in present), None)
        pos = order.index(nxt) if nxt else len(order)
        order.insert(pos, aid)
        present.add(aid)
        inserts.append((aid, pos))
    return deletes, inserts, order


FIELD_KEYS = (("action", "physio_action"),
              ("notes", "physio_reactivation_notes"))


def reconcile(master_rows, team):
    """Settle each feedback cell between the two sheets, master_rows in place.

    The physio's sheet is the source of truth for these two columns: a value
    typed there overwrites the master. A blank team cell adopts the master's
    value instead of wiping it, so feedback typed straight into the master (by
    Martin or Sinead) survives, and so a row that arrives before its next sync
    isn't blanked. Returns the (appointment_id, master column, value) triples
    that need writing to the master."""
    changes = []
    for m in master_rows:
        t = team.get(m["aid"])
        if not t:
            continue
        for field, key in FIELD_KEYS:
            if t[field] and t[field] != m[key]:
                changes.append((m["aid"], key, t[field]))
                m[key] = t[field]
            elif not t[field]:
                pass          # master value stands and flows back to the team sheet
    return changes


def sync(dry_run=False, verbose=True, relayout=False):
    """Push physio feedback to the master, then refresh the team sheet.

    Feedback flows team -> master. The reverse only fills a team cell that is
    blank, so a physio's wording is never overwritten by a stale master copy.
    """
    gc = _client()
    master_rows, index, master_sh = read_master(gc)
    team_sh = gc.open_by_key(config.TEAM_SPREADSHEET_ID)
    try:
        ws = team_sh.worksheet(TAB)
    except gspread.WorksheetNotFound:
        raise RuntimeError(f"team sheet has no '{TAB}' tab — run --build first")

    team_vals = ws.get_all_values()
    relaid_out = relayout or not team_vals or team_vals[0][:N_COLS] != HEADER
    if relaid_out:
        # The columns moved (a --build added one) or someone edited the header.
        # Reading feedback out of the old positions would put it in the wrong
        # master cells, so skip the push and rebuild from the master, which
        # already holds every physio entry pushed since the last layout. Only
        # feedback typed in the few minutes since the last sync is at risk.
        print("rebuilding the team sheet from the master (layout changed, or "
              "--relayout) — not pushing this round")
    team = {}
    for r in (team_vals[1:] if not relaid_out else []):
        aid = r[I_KEY].strip() if len(r) > I_KEY else ""
        if aid:
            team[aid] = {"action": (r[I_ACTION].strip() if len(r) > I_ACTION else ""),
                         "notes": (r[I_NOTES].strip() if len(r) > I_NOTES else "")}

    # ---- 1. push: physio feedback -> master Q/R
    changes = reconcile(master_rows, team)
    cols = {c: p1.SHEET_COLUMNS.index(c) + 1 for c in p1.SHEET_COLUMNS}
    pushes = []
    for aid, key, value in changes:
        tab, row = index[aid]
        a1 = gspread.utils.rowcol_to_a1(row, cols[key])
        pushes.append({"range": f"'{tab}'!{a1}", "values": [[value]]})
    if pushes and not dry_run:
        master_sh.values_batch_update({"valueInputOption": "RAW", "data": pushes})
    if verbose:
        print(f"push  team -> master : {len(pushes)} cell(s)")
        by_aid = {m["aid"]: m for m in master_rows}
        for aid, key, value in changes:
            print(f"    {by_aid[aid]['patient'][:28]:30s} {key:26s} -> {value[:40]}")

    # ---- 2. pull: refresh the team sheet from the master
    desired = {m["aid"]: _team_row(m) for m in master_rows}
    desired_ids = [m["aid"] for m in master_rows]

    # Rows already on the sheet should sit in master order (newest first). If
    # they don't, someone sorted the sheet — Martin did by accident on 21 Sep,
    # sorting the Week column as text so "07 Sep" jumped above "14 Sep". Sorts
    # move whole rows, so each row still carries its own id and the push above
    # was sound; now put the order back. plan_rows never reorders, by design,
    # so without this the sheet would stay jumbled for good.
    on_sheet = [(r[I_KEY].strip() if len(r) > I_KEY else "") for r in team_vals[1:]]
    resorted = not relaid_out and is_out_of_order(on_sheet, desired_ids)
    if resorted and verbose:
        print("rows are out of order (the sheet was sorted) — restoring newest-first")

    if relaid_out or resorted:
        # Columns have moved, so nothing on the sheet can be trusted by
        # position — lay it all out again. The one time a wholesale rewrite
        # is right; it only follows a --build that changed the layout.
        if not dry_run:
            end = max(len(desired_ids) + 1, len(team_vals))
            body = [desired[a] for a in desired_ids]
            body += [[""] * N_COLS] * (end - len(body) - 1)
            ws.update(values=body, range_name=f"A2:{chr(64 + N_COLS)}{end}",
                      value_input_option="RAW")
        if verbose:
            print(f"pull  master -> team : relaid out, {len(desired_ids)} rows")
        return len(pushes), len(desired_ids)

    # Rows are added and removed as real row inserts and deletes, never by
    # rewriting text into existing rows. Sheets treats a structural change the
    # way it treats a colleague inserting a row: a physio mid-edit on Joe
    # Bloggs stays on Joe Bloggs as he moves down. Overwriting values instead
    # would slide a different patient under her cursor, and her note would be
    # filed — here and in the master — against the wrong person.
    current_ids = [(r[I_KEY].strip() if len(r) > I_KEY else "")
                   for r in team_vals[1:]]
    while current_ids and not current_ids[-1]:
        current_ids.pop()                         # trailing empty rows
    deletes, inserts, final = plan_rows(current_ids, desired_ids)

    reqs = [{"deleteDimension": {"range": {
                "sheetId": ws.id, "dimension": "ROWS",
                "startIndex": i + 1, "endIndex": i + 2}}}
            for i in deletes]                     # bottom-up, so indices hold
    reqs += [{"insertDimension": {"range": {
                "sheetId": ws.id, "dimension": "ROWS",
                "startIndex": i + 1, "endIndex": i + 2},
              "inheritFromBefore": False}}        # take the data row's format,
             for _, i in inserts]                 # never the header's
    if reqs and not dry_run:
        team_sh.batch_update({"requests": reqs})

    # Values, addressed by where each row sits AFTER the inserts/deletes.
    cur_by_id = {}
    for r in team_vals[1:]:
        aid = r[I_KEY].strip() if len(r) > I_KEY else ""
        if aid:
            cur_by_id[aid] = (r + [""] * N_COLS)[:N_COLS]
    new_ids = {a for a, _ in inserts}
    updates = []
    for pos, aid in enumerate(final):
        row, sheet_row = desired[aid], pos + 2
        if aid in new_ids:
            updates.append({"range": f"'{TAB}'!A{sheet_row}:{chr(64 + N_COLS)}{sheet_row}",
                            "values": [row]})
            continue
        cur = cur_by_id[aid]
        # info columns and the hidden status (row colour) track the master
        cells = list(range(N_INFO)) + [I_STATUS]
        # a feedback cell is written only to fill a blank from the master —
        # never over anything a physio has typed
        cells += [j for j in (I_ACTION, I_NOTES) if not cur[j].strip() and row[j]]
        for j in cells:
            if str(cur[j]).strip() != str(row[j]).strip():
                a1 = gspread.utils.rowcol_to_a1(sheet_row, j + 1)
                updates.append({"range": f"'{TAB}'!{a1}", "values": [[row[j]]]})
    if updates and not dry_run:
        team_sh.values_batch_update({"valueInputOption": "RAW", "data": updates})

    if verbose:
        print(f"pull  master -> team : {len(final)} rows "
              f"(+{len(inserts)} new, -{len(deletes)} closed, "
              f"{len(updates) - len(inserts)} cell(s) refreshed)")
    if dry_run and verbose:
        print("\nDRY RUN — nothing written")
    return len(pushes), len(final)


# status -> row background, matching apply_dropoff_tab_formatting on the master.
STATUS_COLOURS = [
    ("contact_attempted", {"red": 1.00, "green": 0.87, "blue": 0.70}),  # orange
    ("leave",             {"red": 0.96, "green": 0.78, "blue": 0.78}),  # red
]


def _colour_rules(sid):
    """Row colours by reception status, plus a fade once the physio replies."""
    full = {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 600,
            "startColumnIndex": 0, "endColumnIndex": N_COLS}
    reqs = []
    for i, (value, colour) in enumerate(STATUS_COLOURS):
        reqs.append({"addConditionalFormatRule": {"index": i, "rule": {
            "ranges": [full],
            "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA", "values": [
                    {"userEnteredValue": f'=${chr(65 + I_STATUS)}2="{value}"'}]},
                "format": {"backgroundColor": colour}}}}})
    # Answered rows grey out, so what's left is what still needs doing.
    reqs.append({"addConditionalFormatRule": {"index": len(reqs), "rule": {
        "ranges": [full],
        "booleanRule": {
            "condition": {"type": "CUSTOM_FORMULA", "values": [
                {"userEnteredValue": f'=${chr(65 + I_ACTION)}2<>""'}]},
            "format": {"textFormat": {"foregroundColor": {
                "red": 0.55, "green": 0.55, "blue": 0.55}}}}}}})
    return reqs


VIEW_SUFFIX = "'s patients"     # marks a filter view as bot-managed


def physio_views_wanted(today=None):
    """{view title: [practitioner full names]} for everyone on the team today.

    Straight from config.TEAM, so a starter gets a view and a leaver loses
    theirs without anyone remembering to do it. Several Cliniko names can map
    to one physio (Marty treats as "Martin Loughran" and "Martin Loughran CS")."""
    today = today or datetime.now().date()
    return {f"{m['display']}{VIEW_SUFFIX}": list(m["full_names"])
            for m in config.TEAM if config.is_active_on(m, today)}


def ensure_physio_views(sh=None, ws=None, verbose=True):
    """Create/refresh one saved filter view per physio on the team sheet.

    A filter view is private to whoever opens it — unlike the shared filter on
    the header row, where one physio filtering to her own name would change
    what everyone else sees. Each view has its own link, so Sinead can send
    Molai a URL that opens on just Molai's patients.

    Only views whose title ends in VIEW_SUFFIX are touched; anything a physio
    makes for herself is left alone. A view that already matches is kept, so
    its link never changes. Returns {title: url}."""
    if sh is None:
        sh = _client().open_by_key(config.TEAM_SPREADSHEET_ID)
        ws = sh.worksheet(TAB)
    wanted = physio_views_wanted()
    meta = sh.fetch_sheet_metadata(params={
        "fields": "sheets(properties(sheetId),filterViews)"})
    existing = {}
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == ws.id:
            for fv in sheet.get("filterViews", []) or []:
                if fv.get("title", "").endswith(VIEW_SUFFIX):
                    existing[fv["title"]] = fv
    i_physio = next(i for i, (h, _) in enumerate(INFO_COLS) if h == "Physio")
    col = chr(65 + i_physio)

    def spec(names):
        # custom formula so one view can match several Cliniko names
        f = "=OR(" + ",".join(f'${col}2="{n}"' for n in names) + ")"
        return [{"columnIndex": i_physio, "filterCriteria": {
            "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f}]}}}]

    def current_formula(fv):
        for fs in fv.get("filterSpecs", []) or []:
            c = (fs.get("filterCriteria") or {}).get("condition") or {}
            return (c.get("values") or [{}])[0].get("userEnteredValue")

    reqs, created = [], []
    for title, fv in existing.items():
        if title not in wanted or current_formula(fv) != spec(wanted[title])[0][
                "filterCriteria"]["condition"]["values"][0]["userEnteredValue"]:
            reqs.append({"deleteFilterView": {"filterId": fv["filterViewId"]}})
    for title, names in wanted.items():
        fv = existing.get(title)
        if fv and current_formula(fv) == spec(names)[0]["filterCriteria"][
                "condition"]["values"][0]["userEnteredValue"]:
            continue
        reqs.append({"addFilterView": {"filter": {
            "title": title,
            # open-ended range, so rows the sync inserts are always inside it
            "range": {"sheetId": ws.id, "startRowIndex": 0,
                      "startColumnIndex": 0, "endColumnIndex": N_COLS},
            "filterSpecs": spec(names)}}})
        created.append(title)
    if reqs:
        sh.batch_update({"requests": reqs})
    # re-read for the ids (and so the links reflect what's really there)
    meta = sh.fetch_sheet_metadata(params={
        "fields": "sheets(properties(sheetId),filterViews(filterViewId,title))"})
    links = {}
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == ws.id:
            for fv in sheet.get("filterViews", []) or []:
                if fv["title"] in wanted:
                    links[fv["title"]] = (f"{config.TEAM_SPREADSHEET_URL}"
                                          f"#gid={ws.id}&fvid={fv['filterViewId']}")
    if verbose:
        print(f"physio views: {len(links)} live"
              + (f", created/updated {len(created)}" if created else ", all current"))
    return links


def build():
    """Lay out the team tab and lock everything except the two feedback columns."""
    gc = _client()
    sh = gc.open_by_key(config.TEAM_SPREADSHEET_ID)
    try:
        ws = sh.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB, rows=600, cols=N_COLS)
        print(f"created tab '{TAB}'")
    # Drop the default Sheet1 once ours exists, so nobody types into the wrong one.
    for other in sh.worksheets():
        if other.id != ws.id and other.title in ("Sheet1", "Sheet 1"):
            sh.del_worksheet(other)
            print("removed the empty default Sheet1")

    ws.update(values=[HEADER], range_name="A1", value_input_option="RAW")
    sid = ws.id

    # Clear what a previous --build left behind FIRST, in the same batch.
    # addConditionalFormatRule appends, so without this every rebuild would
    # stack another copy of each rule (the same way bot charts piled up on the
    # master). Deletes go high -> low so the indices stay valid as they go.
    meta = sh.fetch_sheet_metadata(params={
        "fields": "sheets(properties(sheetId),conditionalFormats,"
                  "protectedRanges(protectedRangeId))"})
    cleanup = []
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") != sid:
            continue
        for i in range(len(sheet.get("conditionalFormats", []) or []) - 1, -1, -1):
            cleanup.append({"deleteConditionalFormatRule":
                            {"sheetId": sid, "index": i}})
        for pr in sheet.get("protectedRanges", []) or []:
            cleanup.append({"deleteProtectedRange":
                            {"protectedRangeId": pr["protectedRangeId"]}})
    if cleanup:
        print(f"  clearing {len(cleanup)} rule(s) from the previous build")

    reqs = cleanup + [
        {"updateSheetProperties": {
            "properties": {"sheetId": sid,
                           "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        # header
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.17, "green": 0.24, "blue": 0.31},
                "textFormat": {"bold": True,
                               "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"}},
        # the two columns the physios fill — tinted so they're obvious
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": I_ACTION, "endColumnIndex": I_NOTES + 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.11, "green": 0.45, "blue": 0.33}}},
            "fields": "userEnteredFormat.backgroundColor"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 600,
                      "startColumnIndex": I_ACTION, "endColumnIndex": I_NOTES + 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.92, "green": 0.97, "blue": 0.94}}},
            "fields": "userEnteredFormat.backgroundColor"}},
        # notes columns wrap; ids hidden
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 600,
                      "startColumnIndex": N_INFO - 1, "endColumnIndex": I_NOTES + 1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP",
                                           "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}},
        # Un-hide every column first, THEN hide the two that should be. Hiding
        # only the current ones left a column hidden from the previous layout:
        # adding "Reason given" shifted Physio Notes into what had been the
        # hidden id column, so the physios' notes column vanished (21 Sep).
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": 0, "endIndex": 26},
            "properties": {"hiddenByUser": False}, "fields": "hiddenByUser"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": I_KEY, "endIndex": I_STATUS + 1},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
        # dropdown
        {"setDataValidation": {
            "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 600,
                      "startColumnIndex": I_ACTION, "endColumnIndex": I_ACTION + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": v}
                                              for v in PHYSIO_ACTIONS]},
                     "showCustomUi": True, "strict": False}}},
        # Row colour, same language as the master's W/C tabs so nobody has to
        # learn two schemes. Green (reactivated) is deliberately absent: those
        # rows are dropped from this sheet entirely, which is tidier than
        # colouring a patient nobody needs to chase.
        *_colour_rules(sid),
        # No shared filter on the header row: one person sorting or filtering it
        # changes the sheet for everyone (the 21 Sep sort did exactly that).
        # Each physio has a private saved view instead — ensure_physio_views().
        {"clearBasicFilter": {"sheetId": sid}},
    ]
    widths = [70, 85, 150, 130, 190, 70, 150, 120, 110, 320, 170, 320]
    # (L appointment_id and M status are hidden — no width needed)
    for i, w in enumerate(widths):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})

    # Lock everything but the feedback columns. Editors on the FILE can still
    # type in J/K; the protection is what stops a stray paste wrecking the
    # names, dates and the id column the sync matches on.
    reqs.append({"addProtectedRange": {"protectedRange": {
        "range": {"sheetId": sid},
        "description": "Bot-written — physios edit Physio Action / Physio Notes only",
        "warningOnly": False,
        "unprotectedRanges": [{"sheetId": sid, "startRowIndex": 1,
                               "endRowIndex": 600,
                               "startColumnIndex": I_ACTION,
                               "endColumnIndex": I_NOTES + 1}],
    }}})

    sh.batch_update({"requests": reqs})
    ensure_physio_views(sh, ws)
    print(f"built '{TAB}': {N_COLS} columns, {len(PHYSIO_ACTIONS)}-option dropdown, "
          f"columns A–{chr(64 + N_INFO)} + {chr(65 + I_KEY)}–{chr(65 + I_STATUS)} "
          f"protected, {chr(65 + I_ACTION)}–{chr(65 + I_NOTES)} open to physios")


# --------------------------------------------------------------------------
# Background loop, run inside the Render web service (always on for Slack and
# Twilio), so this needs no extra cron and no dashboard work.
# --------------------------------------------------------------------------
SYNC_EVERY = 1800     # seconds — every 30 min
_LOCK_PATH = "/tmp/team_sheet_sync.lock"


@contextmanager
def _only_one_runner():
    """Yield True in at most one process at a time.

    gunicorn runs two workers in the one container and each imports this
    module, so without this both would sync — doubling the API calls and
    letting two rewrites race each other. The lock is taken per tick rather
    than once at start-up, so if the worker holding it dies the other picks
    the work up on its next tick instead of the sync stopping until a deploy.
    """
    fh = open(_LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        fh.close()


def run_forever(interval=SYNC_EVERY):
    fails = 0
    views_checked = None          # date the physio views were last reconciled
    while True:
        try:
            with _only_one_runner() as mine:
                if not mine:
                    time.sleep(interval)
                    continue
                pushed, rows = sync(verbose=False)
                today = datetime.now().date()
                if views_checked != today:     # roster changes land within a day
                    ensure_physio_views(verbose=False)
                    views_checked = today
                if pushed:
                    print(f"team sheet: pushed {pushed} feedback cell(s), "
                          f"{rows} rows live")
            fails = 0
        except Exception as exc:                      # never kill the thread
            fails += 1
            print(f"team sheet sync failed ({fails}): {exc}")
            if fails in (2, 16):                      # ~1 h, then ~8 h
                try:
                    import slack_notifier
                    slack_notifier._send_dm(
                        config.CEO_SLACK_EMAIL,
                        f":warning: Team drop-off sheet sync has failed {fails} "
                        f"times in a row — physio feedback is *not* reaching the "
                        f"master sheet. Last error: `{exc}`",
                        target_label="team sheet sync failure")
                except Exception as alert_exc:
                    print(f"  (could not raise the alert: {alert_exc})")
        time.sleep(interval)


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    if "--sync" in sys.argv:
        sync(dry_run="--dry-run" in sys.argv, relayout="--relayout" in sys.argv)
    if "--views" in sys.argv:
        for title, url in ensure_physio_views().items():
            print(f"  {title:24s} {url}")
    if not any(a in sys.argv for a in ("--build", "--sync", "--views")):
        print(__doc__)
