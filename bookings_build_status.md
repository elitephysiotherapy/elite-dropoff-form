# New Patient Bookings Tracker — Build Status

**Built 2026-05-18.** Code-complete and shadow-tested against live Cliniko.
Writes nothing until you create the sheet and run it with `--write`.

---

## What it does

Trawls Cliniko **6× a day** (06:00, 09:00, 12:00, 15:00, 18:00, 20:45) for newly
booked initial assessments and logs each one to a dedicated Google Sheet —
**weekly tabs (Sunday–Saturday)**, a **Dashboard** (weekly + calendar-month
counts), and a manual **Leads** tab. After each trawl it DMs the new bookings to
the reception Slack profile.

## What got built

| File | Role |
|---|---|
| `bookings_fetch.py` | The trawl — Cliniko → note parser → weekly sheet rows → Dashboard → Slack DM |
| `bookings_poll.sh` | launchd wrapper |
| `com.elitephysio.bookings.poll.plist` | the 6×/day schedule |
| `config.py` (new section) | sheet ID, reception Slack address, insurer list |

## What auto-fills per booking — zero manual work

Patient (full name, linked to Cliniko) · date booked · appointment
date · clinic (Cookstown/Maghera) · **New vs Past patient** · appointment type ·
**Online booking flag** · and — parsed from the reception note — referrer, body
area, insurer + auth code.

## Verified

- Compiles clean; note parser unit-tested (structured, partial, free-text, empty,
  insurer recognition all correct).
- Weekly tabs correctly Sunday-anchored (Sun 17 May → "W/C 17 May 2026").
- **Live shadow trawl:** pulled 48 recent appointments, correctly identified
  14 IA bookings — New 6 / Past 8, Online 2, Cookstown 10 / Maghera 4, split by
  appointment type — 0 errors.

## The reception note format (as agreed)

When booking an IA, reception adds an appointment note in this shape:

```
Ref: Lavey | Area: hamstring | Auth: AXA 12345
```

- Any part can be left out. Free text that isn't `Ref:/Area:/Auth:` is kept in the Notes column.
- `Auth:` — a recognised insurer (AXA, Aviva, WPA, Bupa, Vitality, VHI, Laya…) is split into its own Insurer column; the rest becomes the auth code.

## The two manual jobs for reception

1. **Booking source tag** — the trawl pre-fills "Online" where it can; reception picks **Phone** or **Walk-in** on the rest (Cliniko can't tell those apart).
2. **Non-booking leads** — type them straight into the **Leads** tab (no Cliniko appointment exists, so the trawl can't see them).

## What's left for you

1. **Create the sheet** — a blank Google Sheet named `Elite Physio — New Patient Bookings`. Share it (Editor) with the service account `dropoff-bot@elite-drop-off-automation.iam.gserviceaccount.com`. Paste its ID into `config.BOOKINGS_SPREADSHEET_ID`. (The trawl builds the Dashboard, Leads and weekly tabs itself on first `--write`.)
2. **Brief reception** on the note format and the two manual jobs above.
3. **Load the launchd job** when ready (`com.elitephysio.bookings.poll.plist`).

## Test it yourself any time (safe — writes nothing)

```
cd ~/cliniko-dropoffs
./venv/bin/python bookings_fetch.py            # full preview table
./venv/bin/python bookings_fetch.py --summary  # counts only
```
`--write` is what commits to the sheet. For your first `--write` test, set
`SLACK_SAFE_MODE = True` in `config.py` if you'd rather the reception DM reroute
to you while testing.

## Worth knowing

- **"Date booked"** = the appointment's creation time — accurate as long as reception creates the appointment during the call.
- A booking that's **cancelled after** it's been logged stays in the sheet (it was a real booking event); your drop-off automation tracks the cancellation separately.
- The Dashboard's **leads count** depends on reception entering a readable date in the Leads tab — the parser accepts `YYYY-MM-DD`, `DD/MM/YYYY` and a few others.
