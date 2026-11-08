"""Tests for speed_to_lead's pure rules (no Sheets, Slack or Twilio).

Pins what Martin agreed 2026-11-08:
  - body area from keywords only; no clear match = no "about your ..." part,
  - conditions are never echoed back in the text,
  - one fixed call line, booking link per clinic, GSM-safe (no curly quotes),
  - texts held 21:00–08:00,
  - the 15-min call clock starts at desk opening (Mon–Thu 08:30–21:00,
    Fri 08:30–16:00, weekend closed -> Monday 08:30).

Run: ./venv/bin/python test_speed_to_lead.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speed_to_lead as stl  # noqa: E402

L = stl.LONDON
failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}   {detail}")


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=L)


# --- body areas -------------------------------------------------------------
check("knee", stl.body_areas("Sore knee after football") == ["knee"])
check("ACL -> knee", stl.body_areas("acl reconstruction rehab") == ["knee"])
check("sciatica -> back", stl.body_areas("Sciatica down left leg") == ["back"])
check("lower back", stl.body_areas("lower back pain for months") == ["back"])
check("two areas, in written order",
      stl.body_areas("neck and lower back stiffness") == ["neck", "back"])
check("max two areas",
      len(stl.body_areas("back, neck, shoulder and knee")) == 2)
check("nothing clear -> []", stl.body_areas("everything hurts") == [])
check("condition not matched", stl.body_areas("peripheral neuropathy") == [])
check("'backing' not back", stl.body_areas("looking for backing") == [])
check("blank", stl.body_areas("") == [])

# --- first name -------------------------------------------------------------
check("plain", stl.first_name("Sarah Quinn") == "Sarah")
check("caps fixed", stl.first_name("SARAH QUINN") == "Sarah")
check("lower fixed", stl.first_name("mary-kate o'neill") == "Mary-Kate")
check("curly apostrophe kept GSM", "’" not in stl.first_name("D’Arcy Smith"))
check("emoji stripped", stl.first_name("Ciara 😀") == "Ciara")
check("junk -> blank", stl.first_name("x") == "")
check("empty -> blank", stl.first_name("") == "")

# --- text -------------------------------------------------------------------
LINK = "https://elite-sms-shortener.onrender.com/r/HJvXlw"
t = stl.compose_text(name="Sarah Quinn", clinic="Maghera",
                     issue="Sore knee", link=LINK)
check("agreed wording", t.startswith(
    "Hi Sarah, thanks for getting in touch with Elite Physio Maghera about your knee. "
    f"You can pick a time here: {LINK}. Or reply with a good time and we'll give you a call."),
    t)
check("STOP line", t.endswith("Reply STOP to opt out."))
t2 = stl.compose_text(name="Sarah", clinic="Omagh", issue="type two diabetic neuro",
                      link=LINK)
check("no match -> no 'about your'", "about your" not in t2, t2)
check("condition never echoed", "diabetic" not in t2 and "neuro" not in t2)
check("no name -> Hi there",
      stl.compose_text(name="", clinic="Omagh", issue="", link=LINK).startswith("Hi there,"))
check("GSM-7 only (no curly quotes/dashes)", all(ord(c) < 128 for c in t), t)
check("fits 2 SMS parts (<=306)", len(t) <= 306, str(len(t)))

# --- quiet hours ------------------------------------------------------------
check("daytime sends now", stl.text_send_time(dt("2026-11-10 14:00")) == dt("2026-11-10 14:00"))
check("20:59 sends now", stl.text_send_time(dt("2026-11-10 20:59")) == dt("2026-11-10 20:59"))
check("21:30 -> 08:00 next day", stl.text_send_time(dt("2026-11-10 21:30")) == dt("2026-11-11 08:00"))
check("02:00 -> 08:00 same day", stl.text_send_time(dt("2026-11-11 02:00")) == dt("2026-11-11 08:00"))
check("overdue lead seen at 07:40 waits for 08:00",
      stl.text_send_time(max(dt("2026-11-07 19:00"), dt("2026-11-08 07:40"))) == dt("2026-11-08 08:00"))
check("Sat 11:00 sends now", stl.text_send_time(dt("2026-11-14 11:00")) == dt("2026-11-14 11:00"))

# --- desk clock (Mon 2026-11-09) ---------------------------------------------
check("Mon 10:00 open -> now", stl.clock_start(dt("2026-11-09 10:00")) == dt("2026-11-09 10:00"))
check("Mon 07:00 -> 08:30", stl.clock_start(dt("2026-11-09 07:00")) == dt("2026-11-09 08:30"))
check("Thu 21:15 -> Fri 08:30", stl.clock_start(dt("2026-11-12 21:15")) == dt("2026-11-13 08:30"))
check("Fri 15:59 open", stl.clock_start(dt("2026-11-13 15:59")) == dt("2026-11-13 15:59"))
check("Fri 16:30 -> Mon 08:30", stl.clock_start(dt("2026-11-13 16:30")) == dt("2026-11-16 08:30"))
check("Sat -> Mon 08:30", stl.clock_start(dt("2026-11-14 12:00")) == dt("2026-11-16 08:30"))
check("Sun 23:00 -> Mon 08:30", stl.clock_start(dt("2026-11-15 23:00")) == dt("2026-11-16 08:30"))

# --- misc -------------------------------------------------------------------
check("phone keys match", stl.norm_phone("p:+447700900123") == stl.norm_phone("07700 900123"))
check("mins fmt", stl.fmt_mins(dt("2026-11-09 10:47") - dt("2026-11-09 09:00")) == "1h 47m")
check("slack escape", stl.esc("<b> & co") == "&lt;b&gt; &amp; co")

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
