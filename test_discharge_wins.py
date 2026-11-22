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
