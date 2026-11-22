from send_discharge_wins_weekly import discharge_answer


def note_p(ans):
    return {"content": {"sections": [{"questions": [
        {"name": "Patient Successfully Discharged?", "type": "paragraph", "answer": ans}]}]}}


def note_r(*sel):
    return {"content": {"sections": [{"questions": [
        {"name": "Patient Successfully Discharged?", "type": "radiobuttons",
         "answers": [{"value": v, "selected": True} for v in sel]}]}]}}


def test_paragraph_shapes():
    assert discharge_answer(note_p("Yes: [X] Not Yet: [ ]")) == "yes"
    assert discharge_answer(note_p("Yes: [X] Not Yet: []")) == "yes"
    assert discharge_answer(note_p("Yes: [ x] Not Yet: []")) == "yes"
    assert discharge_answer(note_p("Yes: [x ] Not Yet: []")) == "yes"
    assert discharge_answer(note_p("Yes: [ ] Not yet: [x]")) == "not_yet"
    assert discharge_answer(note_p("Yes: [ ] Not Yet: [X]")) == "not_yet"
    assert discharge_answer(note_p("Yes: [x] Not Yet: [x]")) == "ambiguous"
    assert discharge_answer(note_p("Yes: [ ] Not Yet: [ ]")) is None


def test_radio_shapes():
    assert discharge_answer(note_r("Yes")) == "yes"
    assert discharge_answer(note_r("Not Yet")) == "not_yet"
    assert discharge_answer(note_r("Not yet")) == "not_yet"
    assert discharge_answer(note_r("Yes", "Not Yet")) == "ambiguous"


def test_absent_or_unanswered():
    assert discharge_answer({"content": {"sections": []}}) is None
    assert discharge_answer(note_p("")) is None


def test_repeat_patient_guard(monkeypatch=None):
    import send_discharge_wins_weekly as m
    from datetime import datetime
    from zoneinfo import ZoneInfo
    start = datetime(2026, 11, 20, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    old = dict(note_p("Yes: [X] Not Yet: []"), created_at="2026-11-14T10:00:00Z")
    later_only = dict(note_p("Yes: [ ] Not Yet: [X]"), created_at="2026-11-14T10:00:00Z")
    in_window = dict(note_p("Yes: [X] Not Yet: []"), created_at="2026-11-23T10:00:00Z")
    real = m.phase2.fetch_all
    try:
        m.phase2.fetch_all = lambda *a, **k: iter([old, in_window])
        assert m.previously_celebrated("1", start) is True       # earlier Yes -> skip
        m.phase2.fetch_all = lambda *a, **k: iter([later_only, in_window])
        assert m.previously_celebrated("2", start) is False      # earlier Not Yet -> celebrate
        m.phase2.fetch_all = lambda *a, **k: iter([in_window])
        assert m.previously_celebrated("3", start) is False      # first ever Yes
    finally:
        m.phase2.fetch_all = real
