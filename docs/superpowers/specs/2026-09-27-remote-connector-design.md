# Remote connector (Docker, single user) — design

Date: 2026-09-27 · Status: approved direction (option C: Docker), implementation on
branch `remote-connector`. The owner authorized live deployment to their own server once
implementation and validation are ready, plus CI/CD for future changes.

## Goal

Let the owner use EverWrapMCP from claude.ai (web, mobile, Desktop cloud chats) as a
custom connector while their Mac is off. The wrapper runs in one long-running Docker
container on the owner's VPS behind the existing nginx, at
`https://everwrap.example.com/mcp`.

Success: with the Mac off, a claude.ai chat gets masked results; blocked IDs are
denied; a request without a valid token gets nothing; the local stdio setup on the Mac
keeps working unchanged.

## Non-goals

Multi-user or managed hosting, ChatGPT, notebook rules, migrating the Mac's Keychain
grant, changes to masking, and an admin UI. Policy stays a local file edited over SSH.

## Trust boundary change

Raw permitted note text and the Evernote refresh token now exist on the VPS (inside
the container and its encrypted data volume) instead of only on the Mac. The hosting provider
and anyone with root on the VPS are inside the boundary. Block list and masking still run
before any text is returned. Docs must say this plainly and never claim notes stay on
the owner's device in remote mode.

## Architecture

```
claude.ai ──HTTPS──▶ nginx (TLS, everwrap.example.com)
                        │ proxy_pass 127.0.0.1:8790
                        ▼
              container "everwrap" (uid 10001, read-only rootfs)
                uvicorn → everwrap.remote app
                  ├─ OAuth AS: /.well-known/*, /register, /authorize, /login, /token, /revoke
                  └─ /mcp (bearer required) → build_server(ConfiguredService)  [unchanged tools]
                        │ upstream.OfficialBackend (read scope)
                        ▼
                  https://mcp.evernote.com/mcp
```

- `everwrap.remote` is a new entry point. `server.build_server`, `live.ConfiguredService`,
  policy, redaction and sections are reused as-is, so tool behavior is identical to stdio.
- One process, one uvicorn worker. Models for the policy's languages are loaded once at
  startup (warm-up) and cached by the existing `get_redactor` LRU.
- Stdio (`everwrap.server`) and Keychain remain the defaults; remote behavior is
  selected only by explicit environment variables.

## Connector authentication (single user)

- EverWrap is its own OAuth 2.1 authorization server using the MCP SDK handlers
  (metadata, dynamic registration, authorize, token, revoke; PKCE S256 enforced by the SDK).
- Registration is open (claude.ai uses DCR) but redirect URIs must match an allowlist
  (default: `https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`).
- `/authorize` stores a pending request (10 min, single use) and redirects to `/login`.
  The login form shows the client name and redirect host and asks for the passphrase.
- Passphrase (minimum 16 characters) is verified against a scrypt hash read from a
  secret file, one verification at a time. Failures are limited per source address
  (5 per 15 minutes, address from nginx's `X-Real-IP`, trusted because the port is
  loopback-only) with a global ceiling of 30; nginx rate-limits `/login` per address.
  Existing tokens keep working during a lockout.
- Registration metadata is capped (4 KB, name ≤ 100 chars); at 50 clients only
  clients without tokens are evicted. Pending requests are capped per client.
- All persistent state changes go through one locked commit that saves before it is
  visible and rolls back on failure; refresh rotation happens inside that commit.
- Tokens: scope `everwrap`, bound to the resource `https://…/mcp` (SDK checks
  `validate_token_resource`). Access tokens 1 h, in memory only. Refresh tokens 30 days,
  rotated on use, stored as SHA-256 hashes. Revoking either revokes the pair.
- Registered clients and refresh-token hashes persist in an encrypted state file, so
  restarts keep the connector signed in.
- Login page: `Cache-Control: no-store`, `frame-ancestors 'none'`, no external assets.

## Credentials and storage

| Item | Location | Protection |
|---|---|---|
| Encryption key (Fernet) | host `/etc/everwrap/credential.key` → `/run/secrets/credential_key` | outside the data volume; host file 0400 owned by uid 10001 |
| Passphrase hash (scrypt) | host `/etc/everwrap/passphrase.hash` → `/run/secrets/passphrase_hash` | outside the data volume |
| Evernote OAuth tokens + client | `/data/evernote-credentials.enc` | Fernet-encrypted |
| Connector clients + refresh hashes | `/data/connector-state.enc` | Fernet-encrypted |
| Policy | host `/etc/everwrap/policy.json` → `/config/policy.json` (read-only) | reloaded per call |

- `EncryptedFileStore` implements the same interface as `KeychainStore` and is chosen
  only when `EVERWRAP_CREDENTIAL_KEY_FILE` is set. Missing/unreadable key → fail closed;
  never plaintext.
- Evernote sign-in: a fresh read-only grant made inside the container
  (`docker compose exec everwrap python -m everwrap.connect`). The container's callback
  listener binds `0.0.0.0:8766`, published only on host loopback `127.0.0.1:18766`;
  the owner forwards it with `ssh -L 8766:127.0.0.1:18766`. The Mac's Keychain grant is
  not copied (refresh-token rotation would make two copies invalidate each other).

## Container hardening and limits

Non-root uid 10001, read-only root filesystem, `tmpfs /tmp`, `cap_drop: ALL`,
`no-new-privileges`, `mem_limit 1536m`, `cpus 1.5`, `restart unless-stopped`, only
host-loopback port publishes (`127.0.0.1:8790`, `127.0.0.1:18766`). CPU-only torch
wheels (the lock resolves CUDA on Linux). Uvicorn access logs off; only static error
messages, never URLs, codes, tokens or note text.

Target constraints: a small VPS (about 2 GiB free memory) shared with other services; English+Turkish models peak at ~800 MB RSS (measured on macOS) and ~600 MB after warm-up; host ports 8790 and 18766 must be free; the Evernote callback port 8766 may already be used on the host, hence the 18766 mapping.

## Open source and secrets

The repository stays public; everything in it and in the image is generic. The
endpoint is single-user. Never committed or baked into the image: the encryption key,
passphrase or its hash, Evernote tokens, the policy (real note IDs), the domain-specific
deployment env, and any server credential. `deploy/` holds templates and scripts only;
real values live in `/etc/everwrap/` on the server. `.dockerignore` excludes local
policy, `.venv`, `.models`, credentials and caches.

## CI/CD (pull-based deployment)

- **GitHub Actions** (`.github/workflows/remote-image.yml`): on pull requests, run the
  model-free test suite and build the image without pushing. On pushes to `main`, test,
  build, and push `ghcr.io/cagdarts/everwrapmcp:main` plus `:sha-<commit>`, with a build
  provenance attestation. The workflow uses only the built-in `GITHUB_TOKEN`
  (`packages: write`, `attestations: write`, `id-token: write`). No server credential
  is stored in GitHub.
- **Server updater** (`deploy/everwrap-update`, run by a systemd timer every 5 minutes):
  pulls the `main` tag, and only when the digest changes recreates the container pinned
  to that digest, waits for `/healthz` to report ready (models loaded), and rolls back
  to the previous digest if it doesn't become healthy. It logs digests, never secrets.
- Why pull, not push: the server needs no inbound deploy key, and a leaked GitHub secret
  cannot reach the VPS. The narrowest possible deploy access is none. The GHCR package
  must be public (it contains no secrets) so the server pulls without a token.
- `/healthz` is unauthenticated and returns only `ok` or `starting`.

## Prerequisites needing owner action

1. DNS A record for the chosen name pointing to the server.
2. After the first image push, set the GHCR package visibility to public.
3. Choose the login passphrase and type it into the hashing command over SSH (it is
   never sent through chat or stored in plaintext).
4. Complete the read-only Evernote sign-in in the browser through the SSH tunnel.
5. Add the connector in claude.ai (Settings → Connectors) and log in once.

## Testing

Model-free tests (run in CI): encrypted store round-trip, wrong key, no plaintext on
disk, missing key fails closed; OAuth flow end to end in-process (register allowlist,
authorize → login → code → token → `/mcp` tools/list), wrong passphrase, lockout,
expired/unknown/revoked tokens, resource binding, refresh rotation, state persistence
across restart, host-header check. Not verifiable locally: Docker build, container
memory on the VPS, live claude.ai connection — covered by the deployment checklist.
