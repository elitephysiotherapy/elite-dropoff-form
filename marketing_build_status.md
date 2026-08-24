# Marketing / NPS System — Build Status

**Built overnight 2026-05-16 → 17.** The Python module that replaces Cliniq Apps
is **code-complete, compiles, and passed a full shadow dry-run against live
Cliniko data.** Nothing can reach a patient — safe mode is on and no API keys
are set. This report is your morning briefing.

---

## 1. What got built

A new `marketing/` package inside `~/cliniko-dropoffs/`, plus changes to two
existing files and a scheduler.

### `marketing/` — 16 files
| File | Role |
|---|---|
| `__init__.py` | package |
| `sheets.py` | opens the NPS & Marketing Google Sheet |
| `sent_log.py` | dedup ledger — never message a patient twice |
| `tally_url.py` | builds the per-patient survey URL |
| `resend_client.py` | email sending (Resend) |
| `twilio_client.py` | SMS sending (Twilio, one-way `ElitePhysio` sender) |
| `cliniko.py` | Cliniko data helpers — appointments, patients, locations |
| `templates.py` | all 33 message templates + HTML email renderer |
| `common.py` | the `Touch` model + shared date/context helpers |
| `results.py` | reads survey results back from the sheet |
| `send.py` | the single send gate (shadow / safe / live) |
| `nps.py` | IA + discharge survey flows |
| `reactivation.py` | cancellation rebooker, no-show rebooker, did-not-rebook |
| `lifecycle.py` | 30 / 90 / 180-day, 12-month, birthday flows |
| `detractor.py` | webhook-side: routes responses, sends branch follow-ups + alerts |
| `poller.py` | the every-10-minutes entry point |

### Changed / added elsewhere
- `config.py` — new `MARKETING` section (switches, senders, per-clinic details, placeholders)
- `server.py` — new `/tally/webhook` route to receive survey responses
- `marketing_poll.sh` — the launchd wrapper
- `com.elitephysio.marketing.poll.plist` — the every-10-min schedule
- `nps_sheet_setup.gs` — small dashboard formula fix

---

## 2. What was verified

- **Every file compiles** (`py_compile`, clean).
- **All 33 templates render** with a full context — no leftover `{placeholders}`, no errors.
- **Tally URL builder + UK phone normalisation** — tested; the Maghera review URL is correctly selected per clinic.
- **Webhook + response handler** — tested with synthetic promoter / passive / detractor payloads: each routes correctly, sends the right follow-ups, attempts the right sheet writes.
- **Full poller shadow dry-run against LIVE Cliniko data** — pulled 267 recent appointments + 27 cancellations, produced **241 correct touches** across every flow (42 IA-survey SMS, 39 IA emails, 39 nurtures, 23 discharge surveys ×2, 20 cancellation rebookers ×2, 8 no-show ×3, 4 did-not-rebook), **0 errors**, patients with no contact details skipped cleanly.

The console output is patient-ID only — no patient names, emails or message bodies are ever printed, so no patient data leaves your tools.

---

## 3. How it works (quick mental model)

```
every 10 min:  poller.py
   -> pulls recent Cliniko appointments + cancellations
   -> nps / reactivation / lifecycle each return the touches "due now"
   -> for each touch: dedup -> fetch patient -> consent check -> render -> send -> log

on survey submit:  Tally -> /tally/webhook (server.py on Render)
   -> records the response in the sheet
   -> Promoter -> Google review follow-ups
      Passive  -> thank-you + alert to Sinead
      Detractor-> apology + callback + alert to Sinead + Detractor Tracker row
```

Three safety modes (in `config.py`): **SHADOW** (nothing sent) → **SAFE_MODE**
(sent, but rerouted to your own email/phone) → **LIVE**. It's on SHADOW+SAFE now.

---

## 4. What's left — your part

### 4a. Accounts & forms (your Track A)
1. **Run `nps_sheet_setup.gs`** → copy the sheet ID into `config.MARKETING_SPREADSHEET_ID`.
2. **Build the Tally form** (`tally_nps_form.md`) → form code into `config.TALLY_FORM_ID`; signing secret into Render's `TALLY_SIGNING_SECRET`.
3. **Build the Cliniko pre-assessment form** (`pre_assessment_form.md`) → its link into `config.PRE_ASSESSMENT_FORM_LINK`.
4. **Resend account** → `RESEND_API_KEY` into `.env` *and* Render; verify the domain DNS.
5. **Twilio** → `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` into `.env` *and* Render.

### 4b. Config to confirm
- **Maghera phone** — Cliniko lists the Cookstown number against both sites. If Maghera has its own line, update `config.CLINICS["Maghera"]["phone"]`.

### 4c. Render
- Redeploy `server.py` (git push) so `/tally/webhook` goes live.
- Add `RESEND_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TALLY_SIGNING_SECRET` to Render's environment.

### 4d. Launchd
- Load `com.elitephysio.marketing.poll.plist` — **last step**, only when ready for SAFE_MODE testing.

---

## 5. Things to tune (flagged honestly)

1. **Discharge detection** is a heuristic — "attended a non-IA appointment, 1-day settle, no future booking, ≥3 attended appointments in the episode." There's no explicit "discharged" flag in Cliniko. Watch the shadow logs for which patients it treats as discharged and tune the constants at the top of `nps.py` (`DISCHARGE_*`). This is the #1 thing to calibrate.
2. **Tally webhook field matching** — hidden fields match by exact name; the score and branch questions match by keyword ("recommend", "went wrong", "9 or 10", "call you"). When you build the Tally form, keep those question labels, or tell me the exact labels and I'll pin them.
3. **"Surveys sent"** on the dashboard is an SMS-proxy count (one SMS per survey). The NPS score itself is exact; only the response-rate denominator is approximate.
4. **Birthday flow** is off (`MARKETING_BIRTHDAY_ENABLED = False`) — it scans the whole patient base. Turn on once you're comfortable with the volume.

---

## 6. Test it yourself

A shadow dry-run — safe, sends nothing, prints what it *would* do:
```
cd ~/cliniko-dropoffs
./venv/bin/python -m marketing.poller
```

---

## 7. Go-live sequence (unchanged from the plan)

1. Foundations done (§4) → **shadow** validates against real data
2. `MARKETING_SAFE_MODE` stays `True` → channel test (sends reach *you*)
3. **Cutover day — all at once (decided: hard cutover, no dual-send):**
   flip `MARKETING_LIVE = True`, load the launchd job, AND **pause every
   Cliniq Apps automation** in the same sitting. Only one system ever messages
   a patient — so no duplicated comms.
4. Keep the Cliniq Apps **subscription** active ~2 weeks as a fallback, with its
   automations **paused**. If the new system misbehaves: set
   `MARKETING_LIVE = False` and re-enable the Cliniq Apps automations — minutes to revert.
5. After ~2 weeks stable → cancel the Cliniq Apps subscription.

The whole module is built. The remaining work is account setup and wiring — no more code is needed to reach a working shadow test.
