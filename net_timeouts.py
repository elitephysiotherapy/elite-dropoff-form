"""Hard network timeouts for every Google Sheets client in this repo.

gspread's HTTP client ships with timeout=None: a stalled connection to Google
waits forever. That is one way the bots froze for a week (8–14 Nov 2026), so
importing this module gives every gspread client created afterwards a
(connect, read) timeout. A timed-out call raises, which sheets_retry retries
and the bots' existing error alerts report — it never hangs silently.

Import it before any gspread client is created (the bot modules import it at
the top). Safe to import more than once.
"""
import gspread.http_client as _hc

SHEETS_TIMEOUT = (10, 60)          # seconds: connect, read

if not getattr(_hc.HTTPClient, "_elite_timeout_patched", False):
    _orig_init = _hc.HTTPClient.__init__

    def _init_with_timeout(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        if self.timeout is None:
            self.timeout = SHEETS_TIMEOUT

    _hc.HTTPClient.__init__ = _init_with_timeout
    _hc.HTTPClient._elite_timeout_patched = True

SLACK_TIMEOUT = 20                 # seconds, passed to every WebClient(...)
