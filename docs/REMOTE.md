# Remote connector (self-hosted, single owner)

**Preview; not yet verified with a live claude.ai connection.** This runs
EverWrapMCP in one Docker container on a server you control, so claude.ai (web,
mobile and Desktop cloud chats) can use it as a custom connector while your
computer is off. The local stdio setup in [INSTALL.md](INSTALL.md) is unchanged.

## What changes compared with local use

- Raw text from notes you permit, and your Evernote refresh token, are processed on
  the server. Your hosting provider and anyone with root on the server are inside the
  trust boundary. The block list and masking still run before text is returned.
- The endpoint is on the public internet. Only someone who completes the OAuth login
  with your passphrase receives a token. Use a long, unique passphrase.
- The image and this repository contain no private values. The policy, passphrase
  hash, encryption key and Evernote credentials exist only on your server.

## How it works

`python -m everwrap.remote serve` serves the same three tools over Streamable HTTP
at `https://<your-name>/mcp` and acts as its own OAuth authorization server:

- claude.ai registers itself (dynamic client registration). Only redirect URIs on an
  allowlist are accepted (default: claude.ai and claude.com callbacks;
  override with `EVERWRAP_ALLOWED_REDIRECTS`).
- The login page asks for your passphrase (scrypt hash, minimum 16 characters).
  After 5 failures from one address within 15 minutes that address is refused; after
  30 failures in total, all logins pause until the window passes. nginx also limits
  `/login` to 10 requests per minute per address. Existing sessions keep working.
- Registrations are capped in size and number; signed-in clients are never evicted.
- Access tokens last 1 hour and are kept in memory; refresh tokens last 30 days, rotate
  on use, and are stored only as hashes in an encrypted file. Tokens are bound to the
  `/mcp` resource and the `everwrap` scope.
- Evernote sign-in happens in the same browser flow: after the passphrase, if the server
  has no working Evernote grant (or you tick **Also reconnect Evernote**), the browser
  goes to Evernote's read-only consent page and returns to `/evernote/callback`. Only a
  read-only grant is accepted; the PKCE verifier and single-use state stay on the
  server. When a stored grant stops refreshing, tools answer "Evernote sign-in needed";
  disconnect and connect the connector in Claude to renew it.
- Evernote credentials are stored in `/data/evernote-credentials.enc`, encrypted with a
  key mounted from outside the data directory. There is no plaintext fallback.
- The container runs as uid 10001 with a read-only filesystem, no capabilities, a
  1.5 GB memory limit, and ports published only on the host's loopback interface.
  English and Turkish masking used about 600–800 MB after warm-up in testing.

## Continuous deployment

`.github/workflows/remote-image.yml` tests every change, builds the image, starts it
with fictional secrets and models loaded, and checks that `/mcp` refuses
unauthenticated requests. Pushes to `main` publish `ghcr.io/crfgxr/everwrapmcp:main`
and `:sha-<commit>` with a build provenance attestation, using only the workflow's own
`GITHUB_TOKEN`.

The server pulls; nothing pushes to it. `everwrap-update.timer` runs
`/opt/everwrap/everwrap-update` every 5 minutes: when the `main` digest changes it
recreates the container pinned to that digest, waits for the health check, and rolls
back to the previous digest if the new one does not become healthy; a digest that
failed is recorded in `/var/lib/everwrap-update/rejected` and not retried. No server
credential is stored in GitHub. Actions are pinned to commit SHAs, base images to
digests, and every Python package (including the CPU-only torch wheel) to a hash
(`deploy/requirements-image.txt`, regenerated with `deploy/lock-image-requirements.sh`
after changing `uv.lock` or `requirements-live.txt`).

Anything merged to `main` reaches the server within minutes, so protect `main`
(branch protection, required checks) the same way you protect the server. The
updater does not yet verify the build provenance attestation. Changes to `deploy/` files themselves are copied to
the server manually.

## Deploy

Replace `everwrap.example.com` with your name. Run as root on the server unless noted.

1. **DNS:** create an A (and optionally AAAA) record for `everwrap.example.com`
   pointing to the server.
2. **Image visibility:** after the first push to `main`, open the package settings for
   `everwrap` on GitHub and set visibility to public so the server can pull without a
   token. The image contains no secrets.
3. **Directories and files:**

   ```sh
   install -d -m 755 /opt/everwrap
   install -d -m 750 /etc/everwrap
   install -d -m 700 -o 10001 -g 10001 /etc/everwrap/config /var/lib/everwrap
   cp deploy/compose.yaml deploy/everwrap-update /opt/everwrap/
   cp deploy/everwrap-update.service deploy/everwrap-update.timer /etc/systemd/system/
   printf 'EVERWRAP_PUBLIC_URL=https://everwrap.example.com\nEVERWRAP_IMAGE=ghcr.io/crfgxr/everwrapmcp:main\n' \
     > /etc/everwrap/compose.env
   ```

4. **Secrets** (typed on the server, never pasted into chat or committed):

   ```sh
   IMAGE=ghcr.io/crfgxr/everwrapmcp:main
   docker pull $IMAGE
   docker run --rm $IMAGE python -m everwrap.remote generate-key > /etc/everwrap/credential.key
   docker run --rm -it --user 0 -v /etc/everwrap:/out $IMAGE \
     python -m everwrap.remote hash-passphrase --output /out/passphrase.hash
   chown 10001:10001 /etc/everwrap/credential.key /etc/everwrap/passphrase.hash
   chmod 400 /etc/everwrap/credential.key /etc/everwrap/passphrase.hash
   ```

   Back up the key separately from `/var/lib/everwrap`; without it the encrypted data
   cannot be read (you can always delete the data and sign in again).

5. **Policy:** create `/etc/everwrap/config/policy.json` as in [INSTALL.md](INSTALL.md),
   starting with a fictional test note, `"content_mode": "redacted"` and your
   `languages` (the default image includes `en` and `tr`). Then
   `chown 10001:10001` and `chmod 400` it. It is reloaded on every call.
6. **TLS and nginx:** obtain the certificate without letting certbot edit files:
   `certbot certonly --nginx -d everwrap.example.com`. Then copy
   `deploy/nginx-everwrap.conf` to `/etc/nginx/sites-available/everwrap`, replace the
   example name, link it into `sites-enabled`, and run
   `nginx -t && systemctl reload nginx`. Port 80 only redirects to HTTPS. The app
   trusts the `X-Real-IP` header this file sets, which is safe only because the
   container port is published on the host's loopback interface.
7. **Start and enable updates:**

   ```sh
   systemctl daemon-reload
   /opt/everwrap/everwrap-update
   systemctl enable --now everwrap-update.timer
   ```

8. **Connect claude.ai:** Settings → Connectors → Add custom connector, URL
   `https://everwrap.example.com/mcp`. Claude opens the EverWrapMCP login page; enter
   your passphrase. On first use the browser continues to Evernote: approve **View**
   only. You return to Claude connected. This is a separate grant from your local
   Keychain one; don't copy local tokens to the server. Then test a read of the
   fictional note and a denial of a blocked ID before widening the policy.
9. **Fallback sign-in over SSH** (only if the browser flow is unavailable). Forward the
   callback port with `ssh -L 8766:127.0.0.1:18766 root@your-server`, run
   `docker exec -it everwrap python -m everwrap.connect` in that session, and open the
   printed link in your computer's browser.

Keep direct Evernote connectors removed from claude.ai; they bypass this wrapper.

## Operate

| Task | Command or action |
|---|---|
| Status and logs | `docker ps --filter name=everwrap`, `docker logs everwrap`, `journalctl -u everwrap-update` |
| Change the passphrase | Rerun the `hash-passphrase` command from step 4, restore ownership/mode, then `docker restart everwrap` |
| Sign out every connector session | `rm /var/lib/everwrap/connector-state.enc && docker restart everwrap` |
| Remove Evernote access | `rm /var/lib/everwrap/evernote-credentials.enc`, and revoke the app in your Evernote account settings |
| Renew Evernote access | Disconnect and connect the connector in Claude, tick **Also reconnect Evernote** if asked |
| Pause automatic updates | `systemctl disable --now everwrap-update.timer` |
| Stop the service | `docker compose -f /opt/everwrap/compose.yaml --env-file /etc/everwrap/compose.env down` |
