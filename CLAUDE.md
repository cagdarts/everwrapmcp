# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

EverWrapMCP: a local stdio MCP server that sits between an AI client and Evernote's official MCP (`https://mcp.evernote.com/mcp`). It enforces a private note block list and masks sensitive text locally (Presidio + per-language NER) before anything reaches the assistant. Read-only. Python package name and MCP registration name remain `everwrap`.

## Commands

Python 3.12–3.13, managed with `uv`. `mcp`, `keyring` and `cryptography` are **not** in the lock; they come from `requirements-live.txt`.

```sh
uv sync --python 3.12
uv pip install --python .venv/bin/python -r requirements-live.txt
# Full regression suite needs every language extra plus the Turkish BERT weights:
uv sync --all-extras && uv pip install --python .venv/bin/python -r requirements-live.txt
PYTHONPATH=src .venv/bin/python -m everwrap.language   # downloads pinned Turkish model
```

Plain `uv sync` removes optional model extras; reinstall `requirements-live.txt` afterwards.

Tests (pytest config sets `pythonpath = [".", "src"]`):

```sh
.venv/bin/python -m pytest --ignore=tests/test_release_gate.py -q   # main suite
.venv/bin/python -m pytest tests/test_live.py::test_name -q        # single test
PYTHONPATH=src .venv/bin/python -m experiments.redaction          # regenerates docs/redaction-results.json
```

- `tests/test_release_gate.py` is the historical stock-Presidio baseline; it **intentionally fails** 6 cases and requires `python -m experiments.baseline` first. Keep it excluded from normal runs.
- CI (`.github/workflows/platform-smoke.yml`, macOS + Windows) installs only `requirements-live.txt` + pytest and runs the model-free subset: `test_connect, test_oauth_refresh, test_credentials, test_server, test_live, test_windows_support, test_stdio`. Those tests must not need spaCy/torch models.

Runtime entry points (all `PYTHONPATH=src .venv/bin/python -m ...`): `everwrap.server` (MCP stdio server), `everwrap.connect` (interactive read-only OAuth, loopback on 127.0.0.1:8766, tokens in OS keyring), `everwrap.setup --languages en tr` (install/verify language packs, rewrites only `languages` in the policy), `everwrap.init_policy` (create a disabled policy if absent). `everwrap.remote serve|hash-passphrase|generate-key` is the Docker/HTTP remote connector (see `docs/REMOTE.md`); it is configured only via `EVERWRAP_*` env vars and secret files.

## Architecture

Request path: `server.py` → `live.ConfiguredService` → `live.NoteService` → `upstream.OfficialBackend` → Evernote MCP; text goes through `redaction.PresidioRedactor` (via `sections.read_section` for note bodies).

- **`server.py`** — low-level `mcp.server.lowlevel.Server` exposing exactly three tools: `read_safe_note`, `search_safe_notes`, `semantic_search_safe_notes`. Arguments are re-validated at dispatch (schema is not trusted). All exceptions collapse to three fixed error strings; never return upstream errors, arguments or tracebacks. Logging is disabled because stdout belongs to MCP. The long tool descriptions and server `instructions` are deliberate client-routing text (recent commits tune them) — edit carefully.
- **`policy.py`** — `SingleNotePolicy` loaded from the git-ignored `.everwrap-local.json` (≤1024 bytes, strict keys, duplicate keys rejected). `access_mode`: `single_note` (one `allowed_note_id`) or `denylist` (all except `blocked_note_ids`, max 16). `content_mode`: `blocked` (default, no content), `redacted`, `unredacted`. Block list always wins. Note IDs are canonical lowercase UUIDs.
- **`live.py`** — `ConfiguredService` reloads the policy on **every call** and rejects the result if the policy file changed mid-request. `NoteService` authorizes IDs before any network I/O, re-checks the upstream-returned ID, bounds every text field before and after redaction, and projects fresh dicts (never serializes upstream objects). Search scans at most 100 upstream hits, dropping blocked rows before touching their other fields; redacted mode omits timestamps. Semantic search requires `denylist` + `redacted`.
- **`service.py`** — legacy transport-independent single-note service used by synthetic tests; also defines `ProcessingBlocked`.
- **`upstream.py` / `connect.py` / `oauth_refresh.py` / `credentials.py`** — read-scope OAuth client for the official server. Interactive auth only happens in `everwrap.connect`; the server path raises `ProcessingBlocked` instead. `credentials.ChunkedKeyring` splits tokens for Windows Credential Manager limits.
- **`redaction.py` / `language.py` / `packs.py`** — ENML is flattened to text without executing markup or fetching resources. Deterministic secret/credential/date patterns run first, then Lingua language routing to per-language NER (spaCy for en/es/fr/de, pinned BERT for tr). Unsupported/uncertain passages are withheld. `packs.py` is static metadata (importing loads no models).
- **`sections.py`** — splits large notes into sections, supports `view=start|end|latest|query`, date-heading detection for `latest`, and pagination via `section`/`offset`; only the selected window is redacted.
- **`remote.py` / `remote_auth.py` / `secret_store.py`** — remote mode reuses `build_server` + `ConfiguredService` unchanged, served over Streamable HTTP. `remote_auth.SingleUserAuthProvider` implements the SDK's `OAuthAuthorizationServerProvider`: DCR limited to allowlisted redirect URIs, passphrase login page (scrypt, global lockout), opaque tokens stored only as SHA-256 digests, bound to the `/mcp` resource. `evernote_link.EvernoteLinker` chains Evernote's read-only consent into that login (after the passphrase, when no grant exists, `upstream.sign_in_required` is set, or the owner asks) via `/evernote/callback`; `EvernoteSignInRequired` from the SDK's interactive-auth hook maps to a fixed "Evernote sign-in needed" tool error. `connect.credential_store()` picks `EncryptedFileStore` (Fernet, key file outside the data dir) only when `EVERWRAP_CREDENTIAL_KEY_FILE` is set; otherwise Keychain. The SDK compares code/token expiry against real `time.time()`, so injected test clocks must start at real time.
- **Deployment** — `Dockerfile` installs CPU-only torch (the lock resolves CUDA on Linux) and bakes public models; `.dockerignore` is an allowlist. `.github/workflows/remote-image.yml` tests, smoke-runs the image, and on `main` pushes to GHCR. The server pulls (`deploy/everwrap-update` + systemd timer) with digest pinning and health-checked rollback; no server credentials live in GitHub.
- **`experiments/`** — scripts producing the JSON reports in `docs/`.

## Invariants to preserve

- Fail closed: any redactor/model/parsing error blocks the whole response; never fall back to raw text.
- Access/content modes come only from the local policy file, never from tool arguments.
- No raw Evernote tools, write tools, resources or attachments are exposed.
- `.everwrap-local.json` holds real note IDs and must stay ignored and untracked; committed fixtures use synthetic IDs and fictional text only.
- Masking is documented as best-effort; don't add docs or messages claiming complete PII removal.

## Docs

`docs/README.md` indexes user docs (INSTALL, ONBOARDING, CLIENT_COMPATIBILITY, AGENT_SETUP) and engineering records (DECISIONS, RESULTS, IMPROVEMENTS roadmap, REDACTION limits, LANGUAGE_PACKS). Update the relevant doc and results JSON when behavior or test counts change.
