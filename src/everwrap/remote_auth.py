"""Single-user OAuth 2.1 authorization server for the remote connector.

The MCP SDK handlers implement the protocol (metadata, registration, PKCE, token
and redirect checks). This provider decides who receives a token: only someone
who knows the owner's passphrase. Tokens are opaque random strings; only their
SHA-256 digests are kept. Access tokens live in memory; registered clients and
refresh-token digests persist in an encrypted state file so restarts keep the
connector signed in. Nothing here logs tokens, codes, passphrases or URLs.
"""

import asyncio
import base64
import hashlib
import hmac
import html
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

SCOPE = "everwrap"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 300
PENDING_TTL = 600
MAX_FAILURES = 5          # per source address
MAX_GLOBAL_FAILURES = 30  # across all sources
FAILURE_WINDOW = 900
MAX_CLIENTS = 50
MAX_CLIENT_METADATA = 4096
MAX_CLIENT_NAME = 100
MAX_PENDING = 1000
MAX_PENDING_PER_CLIENT = 10
MIN_PASSPHRASE = 16
MAX_PASSPHRASE = 1024
DEFAULT_REDIRECTS = ("https://claude.ai/api/mcp/auth_callback",
                     "https://claude.com/api/mcp/auth_callback")
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _scrypt(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p,
                          maxmem=128 * 1024 * 1024, dklen=32)


def hash_passphrase(passphrase: str) -> str:
    if not MIN_PASSPHRASE <= len(passphrase) <= MAX_PASSPHRASE:
        raise ValueError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    salt = secrets.token_bytes(16)
    digest = _scrypt(passphrase, salt, **SCRYPT)
    return "scrypt${n}${r}${p}$".format(**SCRYPT) + _b64(salt) + "$" + _b64(digest)


def parse_passphrase_hash(stored: str) -> tuple:
    try:
        name, n, r, p, salt, digest = stored.strip().split("$")
        n, r, p = int(n), int(r), int(p)
        if name != "scrypt" or n < 2 ** 14 or r < 8 or p < 1:
            raise ValueError
        salt, digest = _unb64(salt), _unb64(digest)
        if len(salt) < 16 or len(digest) != 32:
            raise ValueError
        return n, r, p, salt, digest
    except (ValueError, TypeError):
        raise ValueError("Invalid passphrase hash.") from None


def verify_passphrase(passphrase: str, parsed: tuple) -> bool:
    if type(passphrase) is not str or not 1 <= len(passphrase) <= MAX_PASSPHRASE:
        return False
    n, r, p, salt, digest = parsed
    return hmac.compare_digest(_scrypt(passphrase, salt, n, r, p), digest)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _same_url(a, b) -> bool:
    return str(a).rstrip("/") == str(b).rstrip("/")


class SingleUserAuthProvider:
    def __init__(self, *, issuer_url: str, resource_url: str, passphrase_hash: str,
                 state_file=None, allowed_redirects=DEFAULT_REDIRECTS, clock=time.time):
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url
        self._passphrase = parse_passphrase_hash(passphrase_hash)
        self._state_file = state_file
        self._allowed_redirects = frozenset(allowed_redirects)
        self._clock = clock
        self._pending: dict[str, tuple] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access: dict[str, dict] = {}
        self._failures: dict[str, deque] = {}
        # One scrypt (32 MiB) at a time bounds memory and makes the lockout exact.
        self._verify_lock = asyncio.Lock()
        # Serializes persistent-state changes with their save, in order.
        self._state_lock = asyncio.Lock()
        state = state_file.load() if state_file else {}
        self._clients: dict[str, str] = dict(state.get("clients", {}))
        self._refresh: dict[str, dict] = dict(state.get("refresh", {}))

    # Persistence -----------------------------------------------------------
    async def _commit(self, change):
        """Apply change() and save it; restore the previous state if either fails."""
        async with self._state_lock:
            before = (dict(self._clients), dict(self._refresh), dict(self._access))
            try:
                change()
                now = self._clock()
                self._refresh = {k: v for k, v in self._refresh.items() if v["expires_at"] > now}
                if self._state_file is not None:
                    snapshot = {"clients": dict(self._clients), "refresh": dict(self._refresh)}
                    await asyncio.to_thread(self._state_file.save, snapshot)
            except BaseException:
                self._clients, self._refresh, self._access = before
                raise

    # Clients ---------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        stored = self._clients.get(client_id)
        return OAuthClientInformationFull.model_validate_json(stored) if stored else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        redirects = [str(uri) for uri in client_info.redirect_uris or []]
        if not redirects or any(uri not in self._allowed_redirects for uri in redirects):
            raise RegistrationError(error="invalid_redirect_uri",
                                    error_description="Redirect URI is not allowed for this server.")
        stored = client_info.model_dump_json()
        if (len(stored) > MAX_CLIENT_METADATA
                or len(client_info.client_name or "") > MAX_CLIENT_NAME):
            raise RegistrationError(error="invalid_client_metadata",
                                    error_description="Client metadata is too large.")

        def change():
            if len(self._clients) >= MAX_CLIENTS:
                # Evict only registrations that never obtained a token, so registration
                # spam cannot push out the owner's signed-in client.
                active = {entry["client_id"] for entry in self._refresh.values()}
                idle = [cid for cid in self._clients if cid not in active]
                if not idle:
                    raise RegistrationError(error="invalid_client_metadata",
                                            error_description="Too many registered clients.")
                oldest = min(idle, key=lambda cid: OAuthClientInformationFull
                             .model_validate_json(self._clients[cid]).client_id_issued_at or 0)
                self._forget_client(oldest)
            self._clients[client_info.client_id] = stored
        await self._commit(change)

    def _forget_client(self, client_id):
        self._clients.pop(client_id, None)
        self._refresh = {k: v for k, v in self._refresh.items() if v["client_id"] != client_id}
        self._access = {k: v for k, v in self._access.items() if v["client_id"] != client_id}

    # Authorization ---------------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource is not None and not _same_url(params.resource, self.resource_url):
            raise AuthorizeError(error="invalid_target", error_description="Unknown resource.")
        if params.scopes and set(params.scopes) != {SCOPE}:
            raise AuthorizeError(error="invalid_scope", error_description="Unsupported scope.")
        self._prune()
        # Per-client cap first, so one client's requests cannot evict another's.
        mine = sorted((v[2], k) for k, v in self._pending.items() if v[0] == client.client_id)
        for _, key in mine[:max(0, len(mine) - MAX_PENDING_PER_CLIENT + 1)]:
            del self._pending[key]
        while len(self._pending) >= MAX_PENDING:
            self._pending.pop(min(self._pending, key=lambda k: self._pending[k][2]))
        request_id = secrets.token_urlsafe(32)
        self._pending[request_id] = (client.client_id, params, self._clock() + PENDING_TTL)
        return f"{self.issuer_url}/login?request={request_id}"

    def _prune(self):
        now = self._clock()
        self._pending = {k: v for k, v in self._pending.items() if v[2] > now}
        self._codes = {k: v for k, v in self._codes.items() if v.expires_at > now}
        self._access = {k: v for k, v in self._access.items() if v["expires_at"] > now}
        for source in list(self._failures):
            window = self._failures[source]
            while window and window[0] <= now - FAILURE_WINDOW:
                window.popleft()
            if not window:
                del self._failures[source]

    def _locked(self, source: str) -> bool:
        self._prune()
        total = sum(len(window) for window in self._failures.values())
        return (len(self._failures.get(source, ())) >= MAX_FAILURES
                or total >= MAX_GLOBAL_FAILURES)

    async def complete_login(self, request_id: str, passphrase: str, source: str = "unknown") -> str:
        """Return the client redirect URL with a code, or raise PermissionError.

        `source` is the client address; failures are limited per source so one
        attacker cannot lock the owner out, with a global ceiling as a backstop.
        """
        if type(request_id) is not str or request_id not in self._pending:
            raise LookupError
        async with self._verify_lock:
            if self._locked(source):
                raise PermissionError("locked")
            if not await asyncio.to_thread(verify_passphrase, passphrase, self._passphrase):
                self._failures.setdefault(source, deque()).append(self._clock())
                raise PermissionError("invalid")
            pending = self._pending.pop(request_id, None)  # _locked() pruned expired ones.
        if pending is None:
            raise LookupError
        client_id, params, _ = pending
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code, scopes=[SCOPE], expires_at=self._clock() + CODE_TTL,
            client_id=client_id, code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource_url, subject="owner",
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    def pending_summary(self, request_id: str):
        self._prune()
        pending = self._pending.get(request_id) if type(request_id) is str else None
        if pending is None:
            return None
        client = self._clients.get(pending[0])
        name = OAuthClientInformationFull.model_validate_json(client).client_name if client else None
        return name or "Unnamed client", urlsplit(str(pending[1].redirect_uri)).hostname or ""

    async def load_authorization_code(self, client, authorization_code: str):
        self._prune()
        code = self._codes.get(authorization_code)
        return code if code is not None and code.client_id == client.client_id else None

    # Tokens ----------------------------------------------------------------
    async def _issue(self, client_id: str, replaces: str | None = None) -> OAuthToken:
        now = self._clock()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        refresh_digest = _digest(refresh)

        def change():
            if replaces is not None:
                # Rotate inside the same commit: the old token disappears only if the
                # new pair is saved, and a concurrent reuse finds it already gone.
                if self._refresh.pop(replaces, None) is None:
                    raise TokenError(error="invalid_grant",
                                     error_description="Refresh token is invalid.")
                self._access = {k: v for k, v in self._access.items() if v["refresh"] != replaces}
            self._access[_digest(access)] = {"client_id": client_id,
                                             "expires_at": now + ACCESS_TTL,
                                             "refresh": refresh_digest}
            self._refresh[refresh_digest] = {"client_id": client_id,
                                             "expires_at": int(now + REFRESH_TTL)}
        await self._commit(change)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          refresh_token=refresh, scope=SCOPE)

    async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError(error="invalid_grant", error_description="Authorization code is invalid.")
        return await self._issue(client.client_id)

    async def load_refresh_token(self, client, refresh_token: str):
        if type(refresh_token) is not str:
            return None
        entry = self._refresh.get(_digest(refresh_token))
        if (entry is None or entry["client_id"] != client.client_id
                or entry["expires_at"] <= self._clock()):
            return None
        return RefreshToken(token=refresh_token, client_id=client.client_id, scopes=[SCOPE],
                            expires_at=entry["expires_at"], resource=self.resource_url,
                            subject="owner")

    async def exchange_refresh_token(self, client, refresh_token, scopes) -> OAuthToken:
        if scopes and set(scopes) != {SCOPE}:
            raise TokenError(error="invalid_scope", error_description="Unsupported scope.")
        return await self._issue(client.client_id, replaces=_digest(refresh_token.token))

    async def load_access_token(self, token: str) -> AccessToken | None:
        if type(token) is not str:
            return None
        entry = self._access.get(_digest(token))
        if entry is None or entry["expires_at"] <= self._clock():
            return None
        return AccessToken(token=token, client_id=entry["client_id"], scopes=[SCOPE],
                           expires_at=int(entry["expires_at"]), resource=self.resource_url,
                           subject="owner")

    async def revoke_token(self, token) -> None:
        digest = _digest(token.token)

        def change():
            if isinstance(token, AccessToken):
                entry = self._access.pop(digest, None)
                refresh = entry["refresh"] if entry else None
            else:
                refresh = digest
            if refresh is not None:
                self._refresh.pop(refresh, None)
                self._access = {k: v for k, v in self._access.items() if v["refresh"] != refresh}
        await self._commit(change)

    def redirect_origins(self) -> list[str]:
        return sorted({f"{urlsplit(uri).scheme}://{urlsplit(uri).netloc}"
                       for uri in self._allowed_redirects})


# Login page ----------------------------------------------------------------

def _headers(form_origins: list[str]) -> dict:
    # Browsers apply form-action to the redirect that follows the POST, so the
    # allowed OAuth redirect origins must be listed next to 'self'.
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                   f"form-action 'self' {' '.join(form_origins)}; "
                                   "frame-ancestors 'none'; base-uri 'none'",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }


def _page_with(body: str, status: int = 200, headers: dict | None = None) -> HTMLResponse:
    document = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>EverWrapMCP sign-in</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}"
        "input,button{font:inherit;width:100%;padding:.6rem;margin:.4rem 0;box-sizing:border-box}"
        ".warn{background:#fff4d6;padding:.6rem;border-radius:6px}"
        "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}.warn{background:#3a2f10}}"
        f"</style></head><body>{body}</body></html>")
    return HTMLResponse(document, status_code=status, headers=headers or _headers([]))


def login_routes(provider: SingleUserAuthProvider) -> list[Route]:
    headers = _headers(provider.redirect_origins())

    def _page(body, status=200):
        return _page_with(body, status, headers)

    def form(request_id: str, message: str = "", status: int = 200) -> HTMLResponse:
        summary = provider.pending_summary(request_id)
        if summary is None:
            return _page("<h1>Sign-in expired</h1><p>Start the connection again from Claude.</p>", 400)
        name, host = (html.escape(value) for value in summary)
        note = f"<p role=alert><strong>{html.escape(message)}</strong></p>" if message else ""
        return _page(
            "<h1>EverWrapMCP</h1>"
            f"<p><strong>{name}</strong> ({host}) is asking to read your Evernote notes "
            "through this private EverWrapMCP server.</p>"
            "<p class=warn>Continue only if you just started this connection yourself. "
            "Access goes to the Claude account that started the request.</p>" + note +
            "<form method=post action=/login>"
            f"<input type=hidden name=request value='{html.escape(request_id, quote=True)}'>"
            "<label>Passphrase<input type=password name=passphrase autocomplete=current-password "
            "required autofocus></label><button type=submit>Allow access</button></form>", status)

    async def login(request: Request) -> Response:
        if request.method == "GET":
            return form(request.query_params.get("request", ""))
        try:
            data = await request.form(max_files=0, max_fields=4, max_part_size=4096)
            request_id, passphrase = data.get("request", ""), data.get("passphrase", "")
        except Exception:
            return _page("<h1>Invalid request</h1>", 400)
        try:
            # The container port is published only on host loopback, so X-Real-IP
            # can only come from the local nginx (see deploy/nginx-everwrap.conf).
            source = request.headers.get("x-real-ip") or (
                request.client.host if request.client else "unknown")
            target = await provider.complete_login(request_id, passphrase, source[:64])
        except LookupError:
            return form("")
        except PermissionError as error:
            message = ("Too many failed attempts. Try again later." if str(error) == "locked"
                       else "Incorrect passphrase.")
            return form(request_id, message, 403)
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    return [Route("/login", login, methods=["GET", "POST"])]
