"""Idle-unloading masking worker; uses a fake child so no models are needed."""

import sys
import threading
import time
from pathlib import Path

import pytest

from everwrap.redaction_worker import WorkerRedactor
from everwrap.service import ProcessingBlocked

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="worker runs in the Linux image; selectors cannot wait on pipes on Windows")

CHILD = str(Path(__file__).with_name("fake_redaction_child.py"))


def worker(tmp_path, mode="ok", **kwargs):
    starts = tmp_path / f"starts-{mode}"
    starts.touch()
    w = WorkerRedactor(False, ("en",), command=[sys.executable, CHILD, mode, str(starts)], **kwargs)
    return w, starts


def start_count(starts):
    return len(starts.read_text().split())


def test_round_trip_for_all_methods_and_reuses_one_child(tmp_path):
    w, starts = worker(tmp_path)
    try:
        assert w.sanitize_text("alex smith") == "ALEX SMITH"
        assert w.sanitize_markup("<b>x</b>") == "<B>X</B><markup>"
        assert w.sanitize_window("hello world", 6, 11) == "WORLD"
        assert w.running and start_count(starts) == 1
    finally:
        w.close()


def test_child_starts_lazily_and_stops_when_idle(tmp_path):
    w, starts = worker(tmp_path, idle_seconds=0.3, reap_interval=0.05)
    try:
        assert not w.running and start_count(starts) == 0
        w.sanitize_text("a")
        assert w.running
        time.sleep(0.8)
        assert not w.running  # memory returned while idle
        assert w.sanitize_text("b") == "B" and start_count(starts) == 2
    finally:
        w.close()


@pytest.mark.parametrize("mode", ["crash", "error"])
def test_child_failures_fail_closed(tmp_path, mode):
    w, _ = worker(tmp_path, mode)
    try:
        with pytest.raises(ProcessingBlocked):
            w.sanitize_text("secret text")
    finally:
        w.close()


def test_hung_child_times_out_and_is_replaced(tmp_path):
    w, starts = worker(tmp_path, "hang", timeout=0.5)
    try:
        started = time.monotonic()
        with pytest.raises(ProcessingBlocked):
            w.sanitize_text("x")
        assert time.monotonic() - started < 5 and not w.running
    finally:
        w.close()


def test_crash_then_recovery_with_fresh_child(tmp_path):
    w, starts = worker(tmp_path)
    try:
        w.sanitize_text("a")
        w._process.kill()
        w._process.wait()
        assert w.sanitize_text("b") == "B" and start_count(starts) == 2
    finally:
        w.close()


def test_error_reply_keeps_child_and_reveals_nothing(tmp_path):
    w, starts = worker(tmp_path)
    try:
        with pytest.raises(ProcessingBlocked) as error:
            w.sanitize_text("FAIL")
        assert "FAIL" not in str(error.value)
        assert w.sanitize_text("ok") == "OK" and start_count(starts) == 1
    finally:
        w.close()


def test_concurrent_calls_are_serialized_and_answered_correctly(tmp_path):
    w, _ = worker(tmp_path)
    results = {}
    try:
        threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, w.sanitize_text(f"v{i}" if i else "SLOW")))
                   for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == {0: "SLOW", **{i: f"V{i}" for i in range(1, 6)}}
    finally:
        w.close()


def test_redactor_for_selects_worker_only_when_configured(monkeypatch):
    from everwrap import redaction
    monkeypatch.setattr(redaction, "get_redactor", lambda m, l: "in-process")
    monkeypatch.delenv("EVERWRAP_REDACTION_WORKER_IDLE", raising=False)
    assert redaction.redactor_for(False, ("en",)) == "in-process"
    monkeypatch.setenv("EVERWRAP_REDACTION_WORKER_IDLE", "abc")
    with pytest.raises(ProcessingBlocked):
        redaction.redactor_for(False, ("en",))
    monkeypatch.setenv("EVERWRAP_REDACTION_WORKER_IDLE", "60")
    w = redaction.redactor_for(False, ("en",))
    try:
        assert type(w).__name__ == "WorkerRedactor" and w.idle_seconds == 60 and not w.running
    finally:
        w.close()
        redaction._worker.cache_clear()
