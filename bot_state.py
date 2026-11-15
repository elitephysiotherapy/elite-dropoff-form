"""Small JSON state files that carry a bot's memory from one run to the next.

Since 2026-11-15 each bot run is a fresh, short-lived process started by
bot_worker.py (so a hang can never outlive one run). Anything the old
always-on threads kept in memory between loops — consecutive-failure counts,
"already ran today" dates, the campaign's per-day send ceiling — lives here
instead. Files sit in BOT_STATE_DIR (default /tmp/elite-bots): they survive
between runs on the same worker and reset on a redeploy, exactly like the old
in-memory values did.
"""
import json
import os
import tempfile

STATE_DIR = os.environ.get("BOT_STATE_DIR", "/tmp/elite-bots")


def _path(name: str) -> str:
    return os.path.join(STATE_DIR, f"{name}.json")


def load(name: str) -> dict:
    try:
        with open(_path(name)) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(name: str, data: dict) -> None:
    """Atomic write (temp file + rename) so a run killed mid-write never
    leaves a half-written file behind."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, _path(name))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_result(name: str, ok: bool) -> int:
    """Track consecutive failures across runs. Returns the count after this
    run (0 on success)."""
    st = load(name)
    fails = 0 if ok else int(st.get("fails", 0)) + 1
    st["fails"] = fails
    save(name, st)
    return fails
