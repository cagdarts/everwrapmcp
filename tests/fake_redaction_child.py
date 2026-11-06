"""Fake masking child for worker tests; speaks the worker protocol without models."""
import os
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
from everwrap.redaction_worker import _read_frame, _write_frame

mode = sys.argv[1]
out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
print("library noise that must not reach the protocol")  # goes to stderr now
with open(sys.argv[2], "a") as starts:
    starts.write(f"{os.getpid()}\n")
while True:
    try:
        request = _read_frame(sys.stdin.buffer)
    except EOFError:
        break
    if mode == "crash":
        os._exit(1)
    if mode == "hang":
        time.sleep(60)
    if mode == "error" or request.get("text") == "FAIL":
        _write_frame(out, {"ok": False})
        continue
    if request.get("text") == "SLOW":
        time.sleep(0.3)
    text = request["text"]
    if request["op"] == "window":
        text = text[request["start"]:request["end"]]
    _write_frame(out, {"ok": True, "result": text.upper() + ("<markup>" if request["op"] == "markup" else "")})
