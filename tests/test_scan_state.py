"""Тесты состояния сканирования."""

from scan_state import ScanState


def test_reset_initializes_running():
    st = ScanState()
    st.reset("Python")
    assert st.running is True
    assert st.query == "Python"
    assert st.phase == "search"
    assert len(st.logs) == 1


def test_finish_sets_flags():
    st = ScanState()
    st.reset("Go")
    st.finish("done", "Готово")
    assert st.running is False
    assert st.phase == "done"
    assert st.finished_at is not None


def test_logs_bounded():
    st = ScanState()
    st.reset("X")
    for i in range(100):
        st.log(f"msg {i}")
    assert len(st.logs) <= 40


def test_to_dict_has_expected_keys():
    st = ScanState()
    st.reset("X")
    d = st.to_dict()
    for key in ("running", "phase", "query", "elapsed", "logs", "skipped_count"):
        assert key in d
