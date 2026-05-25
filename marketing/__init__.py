"""Elite Physiotherapy marketing / NPS automation package.

Replaces Cliniq Apps. Runs alongside the existing drop-off automation in the
~/cliniko-dropoffs/ project and reuses its Cliniko client (phase2.py), Google
service account and Render Flask server.

Modules:
  sheets.py         opens the "NPS & Marketing" Google Sheet
  sent_log.py       dedup ledger — never message the same patient twice
  tally_url.py      builds the Tally survey URL with hidden fields
  resend_client.py  email sending (Resend)
  twilio_client.py  SMS sending (Twilio, sender "ElitePhysio")

Design docs: Elite_Marketing_Replacement_Plan.md, patient_communication_system.md,
tally_nps_form.md.
"""
