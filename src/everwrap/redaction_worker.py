"""Run the masking models in an on-demand child process that exits when idle.

The server process never imports PyTorch, spaCy or Presidio. The first request
that needs masking starts `python -m everwrap.redaction_worker`, which loads the
models and answers length-prefixed JSON frames over its stdin/stdout. After
`idle_seconds` without requests the child is stopped, so its memory is returned
to the operating system instead of drifting into swap.

Fail closed: any child error, crash, timeout or malformed frame raises
ProcessingBlocked for that request and discards the child; the next request
starts a fresh one. No text, offsets or error details are logged.
"""

import json
import os
import selectors
import struct
import subprocess
import sys
import threading
import time

from .service import ProcessingBlocked

MAX_FRAME = 8 * 1024 * 1024
HEADER = struct.Struct(">I")


def _write_frame(stream, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("Frame too large.")
    stream.write(HEADER.pack(len(data)) + data)
    stream.flush()


def _read_exact(stream, size):
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def _read_frame(stream) -> dict:
    (size,) = HEADER.unpack(_read_exact(stream, HEADER.size))
    if size > MAX_FRAME:
        raise ValueError("Frame too large.")
    payload = json.loads(_read_exact(stream, size))
    if type(payload) is not dict:
        raise ValueError("Unexpected frame.")
    return payload


class WorkerRedactor:
    """Drop-in for PresidioRedactor's sanitize_* methods, backed by a child process."""

    def __init__(self, mask_dates, languages, *, idle_seconds=900, timeout=180,
                 command=None, reap_interval=None):
        self.mask_dates = mask_dates
        self.languages = tuple(languages)
        self.idle_seconds = idle_seconds
        self.timeout = timeout
        self._command = command or [sys.executable, "-m", "everwrap.redaction_worker",
                                    "1" if mask_dates else "0", ",".join(self.languages)]
        self._lock = threading.Lock()
        self._process = None
        self._last_used = 0.0
        self._closed = False
        interval = reap_interval if reap_interval is not None else min(30.0, max(idle_seconds / 4, 0.05))
        self._reaper = threading.Thread(target=self._reap, args=(interval,), daemon=True)
        self._reaper.start()

    # Redactor interface ------------------------------------------------------
    def sanitize_text(self, text):
        return self._call({"op": "text", "text": text})

    def sanitize_markup(self, text):
        return self._call({"op": "markup", "text": text})

    def sanitize_window(self, text, start, end):
        return self._call({"op": "window", "text": text, "start": start, "end": end})

    # Lifecycle ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self):
        with self._lock:
            self._stop_locked()

    def close(self):
        self._closed = True
        self.stop()

    def _stop_locked(self, graceful=True):
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if not graceful:
                raise RuntimeError  # Failed or hung child: kill without waiting.
            process.stdin.close()
            process.wait(timeout=5)
        except Exception:
            process.kill()
            try:
                process.wait(timeout=5)
            except Exception:
                pass

    def _start_locked(self):
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        self._process = subprocess.Popen(
            self._command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, close_fds=True)

    def _reap(self, interval):
        while not self._closed:
            time.sleep(interval)
            with self._lock:
                if (self._process is not None
                        and time.monotonic() - self._last_used >= self.idle_seconds):
                    self._stop_locked()

    def _call(self, request):
        with self._lock:
            if self._closed:
                raise ProcessingBlocked("Redaction is unavailable.")
            failed = False
            try:
                if not self.running:
                    self._stop_locked()
                    self._start_locked()
                _write_frame(self._process.stdin, request)
                response = self._read_with_deadline()
            except Exception:
                failed = True
            finally:
                self._last_used = time.monotonic()
            if failed:
                self._stop_locked(graceful=False)  # A half-read pipe or dead child is never reused.
                raise ProcessingBlocked("Redaction worker failed.")
        if response.get("ok") is not True or type(response.get("result")) is not str:
            raise ProcessingBlocked("Content could not be safely processed.")
        return response["result"]

    def _read_with_deadline(self):
        selector = selectors.DefaultSelector()
        stdout = self._process.stdout
        selector.register(stdout, selectors.EVENT_READ)
        try:
            if not selector.select(timeout=self.timeout):
                raise TimeoutError
            return _read_frame(stdout)
        finally:
            selector.close()


def _serve(mask_dates: bool, languages):
    # The protocol owns the original stdout; anything libraries print goes to stderr.
    protocol_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    protocol_in = sys.stdin.buffer
    import logging
    logging.disable(logging.CRITICAL)
    from .packs import validate_languages
    from .redaction import get_redactor
    redactor = get_redactor(mask_dates, validate_languages(languages))
    while True:
        try:
            request = _read_frame(protocol_in)
        except EOFError:
            return 0
        try:
            op, text = request.get("op"), request.get("text")
            if type(text) is not str:
                raise ValueError
            if op == "text":
                result = redactor.sanitize_text(text)
            elif op == "markup":
                result = redactor.sanitize_markup(text)
            elif op == "window":
                start, end = request.get("start"), request.get("end")
                if type(start) is not int or type(end) is not int:
                    raise ValueError
                result = redactor.sanitize_window(text, start, end)
            else:
                raise ValueError
            response = {"ok": True, "result": result}
        except Exception:
            response = {"ok": False}  # Never echo text or error details.
        _write_frame(protocol_out, response)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("0", "1"):
        raise SystemExit(2)
    try:
        raise SystemExit(_serve(sys.argv[1] == "1", tuple(sys.argv[2].split(","))))
    except KeyboardInterrupt:
        raise SystemExit(130)
