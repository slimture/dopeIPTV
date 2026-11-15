import feedback


def test_a_report_is_read_once_its_problem_is_marked():
    sig = "Traceback: boom"
    key = feedback._sig_key(sig)
    r = {"id": "abcdef0123", "sig": sig}
    assert feedback._item_state({}, r) == "new"
    assert feedback._item_state({key: "fixed"}, r) == "fixed"
    assert feedback._item_state({key: "fixed", "abcdef0123": "seen"}, r) == "seen"
    assert feedback._item_state({key: "seen"}, {"id": "x1", "sig": ""}) == "new"
