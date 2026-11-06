"""The idle-unloading worker masks exactly like the in-process redactor (real models)."""

import os
import sys
from pathlib import Path

import pytest

from everwrap.redaction import get_redactor
from everwrap.redaction_worker import WorkerRedactor

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="worker runs in the Linux image")

SAMPLES = [
    "Talk to Alex Smith about the community garden. Email alex@example.org.",
    "password=synthetic-demo-only-927 and Bearer synthetic.token.value",
    "Bugün Ayşe Yılmaz ile konuştum, telefonu 0532 123 45 67.",
    "Meeting with Dr. Emily Carter at 221B Baker Street, London on March 3rd.",
    "Use asyncio to process messages and retry failed requests.",
]


def test_worker_output_matches_in_process(monkeypatch):
    src = str(Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setenv("PYTHONPATH", src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    local = get_redactor(False, ("en", "tr"))
    worker = WorkerRedactor(False, ("en", "tr"), idle_seconds=60)
    try:
        for sample in SAMPLES:
            assert worker.sanitize_text(sample) == local.sanitize_text(sample)
        markup = "<en-note><div>Call Alex Smith</div></en-note>"
        assert worker.sanitize_markup(markup) == local.sanitize_markup(markup)
        assert "221B Baker Street" not in worker.sanitize_text(SAMPLES[3])
    finally:
        worker.close()
