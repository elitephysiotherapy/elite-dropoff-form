"""One-time setup: add the 3 weekly charts to the 'Weekly Drop-off Analysis' tab.

The weekly analysis script writes the current week at fixed rows, so charts
anchored to those ranges redraw themselves on every daily run. This script is
idempotent — re-running it removes any charts it previously added and recreates
them, so it never leaves duplicates.

Reproduces the 3 charts Martin builds by hand each week:
  1  Drop-offs by Body Part   pie   (IACNA/IADNA excluded)   J6:K19
  2  Drop-off Stage           horizontal bar                 G5:H10
  3  Clinical v Non-Clinical  pie                            M6:N7

Usage:  ./venv/bin/python add_analysis_charts.py
"""

import phase1_fetch as master

TAB = "Weekly Drop-off Analysis"


def _src(sid, r0, r1, c0, c1):
    return {"sources": [{"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1,
                         "startColumnIndex": c0, "endColumnIndex": c1}]}


def _pie(sid, title, dom, ser, anchor_row):
    return {"addChart": {"chart": {
        "spec": {
            "title": title,
            "pieChart": {
                "legendPosition": "RIGHT_LEGEND",
                "domain": {"sourceRange": dom},
                "series": {"sourceRange": ser},
            },
        },
        "position": {"overlayPosition": {
            "anchorCell": {"sheetId": sid, "rowIndex": anchor_row,
                           "columnIndex": 15},
            "widthPixels": 480, "heightPixels": 300}},
    }}}


def main():
    sh = master.open_spreadsheet()
    ws = sh.worksheet(TAB)
    sid = ws.id

    # Drop existing charts on this tab so the script is safe to re-run.
    meta = sh.fetch_sheet_metadata(params={"fields": (
        "sheets(properties(sheetId,gridProperties.columnCount),"
        "charts(chartId))")})
    old, cols = [], 8
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == sid:
            old = [c["chartId"] for c in s.get("charts", [])]
            cols = s["properties"].get("gridProperties", {}).get(
                "columnCount", 8)
    requests = [{"deleteEmbeddedObject": {"objectId": cid}} for cid in old]

    # Charts anchor at column P (index 15) — widen the grid if it's too narrow.
    if cols < 26:
        requests.append({"appendDimension": {
            "sheetId": sid, "dimension": "COLUMNS", "length": 26 - cols}})

    # Chart 1 — pie: drop-offs by body part. Body-area block is J6:K19
    # (rows idx 5-18), IACNA/IADNA already excluded by the analysis script.
    requests.append(_pie(
        sid, "Drop-offs by Body Part",
        _src(sid, 5, 19, 9, 10), _src(sid, 5, 19, 10, 11), 4))

    # Chart 2 — horizontal bar: drop-off stage. Header row 5 + 5 stage
    # rows 6-10, columns G/H (idx 6/7).
    requests.append({"addChart": {"chart": {
        "spec": {
            "title": "Drop-off Stage",
            "basicChart": {
                "chartType": "BAR",
                "legendPosition": "NO_LEGEND",
                "headerCount": 1,
                "axis": [
                    {"position": "LEFT_AXIS", "title": "Stage"},
                    {"position": "BOTTOM_AXIS", "title": "Drop-offs"},
                ],
                "domains": [{"domain": {"sourceRange": _src(sid, 4, 10, 6, 7)}}],
                "series": [{"series": {"sourceRange": _src(sid, 4, 10, 7, 8)},
                            "targetAxis": "BOTTOM_AXIS"}],
            },
        },
        "position": {"overlayPosition": {
            "anchorCell": {"sheetId": sid, "rowIndex": 22, "columnIndex": 15},
            "widthPixels": 480, "heightPixels": 300}},
    }}})

    # Chart 3 — pie: clinical v non-clinical. Block is M6:N7 (rows idx 5-6).
    requests.append(_pie(
        sid, "Clinical v Non-Clinical",
        _src(sid, 5, 7, 12, 13), _src(sid, 5, 7, 13, 14), 40))

    sh.batch_update({"requests": requests})
    print(f"Removed {len(old)} old chart(s), added 3. Tab: '{TAB}'.")


if __name__ == "__main__":
    main()
