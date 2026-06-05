"""Minimal Flask app — SMS URL shortener redirect service.

Single responsibility: receive GET /r/<token>, 302 redirect to the stored
long URL. Completely isolated from elite-dropoff-form's Slack/Tally routes —
if this service is unreachable it can't take anything else down.

Endpoints:
  GET /health     → {"ok": true}
  GET /r/<token>  → 302 to the long URL, or 404 if unknown

Env vars (set on the Render service):
  MARKETING_SPREADSHEET_ID   (the NPS & Marketing Google Sheet ID)
  SERVICE_ACCOUNT_JSON       (the Google service-account creds as raw JSON)
"""

import os

from flask import Flask, redirect, make_response

import url_shortener  # local module

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return {"ok": True}


@app.route("/", methods=["GET"])
def root():
    return {"service": "elite-sms-shortener", "ok": True}


@app.route("/r/<token>", methods=["GET"])
def short_redirect(token):
    long_url = url_shortener.lookup(token)
    if not long_url:
        return make_response("Link expired or invalid.", 404)
    return redirect(long_url, code=302)


if __name__ == "__main__":
    # Local dev runner
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
