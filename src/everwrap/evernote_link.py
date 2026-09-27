"""Browser-based Evernote sign-in for the remote connector.

After the owner passes the passphrase on the EverWrap login page, the browser can
be sent to Evernote's consent page and back to `<base>/evernote/callback`, so no
SSH tunnel is needed. The grant is stored through the same credential store the
upstream client reads, so it refreshes exactly like a grant from
`everwrap.connect`.

Endpoints are fixed Evernote HTTPS URLs (not discovered at runtime). Only a
read-only grant is accepted. The PKCE verifier and state never leave this
process; state is single-use and expires. Nothing here logs codes or tokens.
"""

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx2
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .connect import credential_store, require_read_only
from .oauth_refresh import require_evernote_https
from . import upstream

EVERNOTE_ISSUER = "https://accounts.evernote.com"
AUTHORIZE_URL = EVERNOTE_ISSUER + "/auth/authorize"
TOKEN_URL = EVERNOTE_ISSUER + "/auth/token"
REGISTER_URL = EVERNOTE_ISSUER + "/auth/register"
EVERNOTE_RESOURCE = "https://mcp.evernote.com"
STATE_TTL = 600
MAX_PENDING = 20


def _default_http():
    return httpx2.AsyncClient(timeout=httpx2.Timeout(30, connect=15), follow_redirects=False)


class EvernoteLinker:
    def __init__(self, base_url: str, *, store_factory=credential_store,
                 http_factory=_default_http, clock=time.time):
        self.redirect_uri = base_url.rstrip("/") + "/evernote/callback"
        self._store_factory = store_factory
        self._http_factory = http_factory
        self._clock = clock
        self._client: OAuthClientInformationFull | None = None
        self._pending: dict[str, tuple] = {}
        for url in (AUTHORIZE_URL, TOKEN_URL, REGISTER_URL):
            require_evernote_https(url)

    async def needed(self) -> bool:
        """True when there is no stored grant or the last use needed sign-in."""
        if upstream.sign_in_required.is_set():
            return True
        try:
            return await self._store_factory().get_tokens() is None
        except Exception:
            return True

    async def _web_client(self) -> OAuthClientInformationFull:
        if self._client is not None:
            return self._client
        stored = await self._store_factory().get_client_info()
        if stored and self.redirect_uri in [str(uri) for uri in stored.redirect_uris or []]:
            self._client = stored
            return stored
        async with self._http_factory() as http:
            response = await http.post(REGISTER_URL, json={
                "client_name": "EverWrapMCP", "redirect_uris": [self.redirect_uri],
                "token_endpoint_auth_method": "none", "scope": "read",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]})
        if response.status_code not in (200, 201):
            raise ValueError("Evernote client registration failed.")
        data = response.json()
        if (type(data) is not dict or type(data.get("client_id")) is not str
                or self.redirect_uri not in (data.get("redirect_uris") or [])
                or data.get("token_endpoint_auth_method", "none") != "none"):
            raise ValueError("Unexpected Evernote registration response.")
        # The issuer stamp lets the persisted-refresh path validate this client.
        self._client = OAuthClientInformationFull.model_validate({**data, "issuer": EVERNOTE_ISSUER})
        return self._client

    def _prune(self):
        now = self._clock()
        self._pending = {k: v for k, v in self._pending.items() if v[3] > now}
        while len(self._pending) >= MAX_PENDING:
            self._pending.pop(min(self._pending, key=lambda k: self._pending[k][3]))

    async def start(self, payload) -> str:
        """Return Evernote's authorization URL; `payload` comes back from finish()."""
        client = await self._web_client()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        self._prune()
        self._pending[state] = (client, verifier, payload, self._clock() + STATE_TTL)
        return AUTHORIZE_URL + "?" + urlencode({
            "response_type": "code", "client_id": client.client_id,
            "redirect_uri": self.redirect_uri, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "resource": EVERNOTE_RESOURCE, "scope": "read"})

    def discard(self, state):
        """Forget a pending sign-in the owner declined on Evernote's page."""
        if type(state) is str:
            self._pending.pop(state, None)

    async def finish(self, state, code, issuer=None):
        """Exchange the code, store a read-only grant, and return the start() payload.

        Raises LookupError for an unknown, reused or expired state and ValueError
        for any other failure; nothing is stored unless every check passes.
        """
        self._prune()
        entry = self._pending.pop(state, None) if type(state) is str else None
        if entry is None:
            raise LookupError("Unknown or expired Evernote sign-in.")
        client, verifier, payload, _ = entry
        if type(code) is not str or not code or len(code) > 2048:
            raise ValueError("Missing authorization code.")
        if issuer is not None and issuer != EVERNOTE_ISSUER:
            raise ValueError("Unexpected authorization issuer.")
        async with self._http_factory() as http:
            response = await http.post(TOKEN_URL, data={
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.redirect_uri, "client_id": client.client_id,
                "code_verifier": verifier, "resource": EVERNOTE_RESOURCE})
        if response.status_code != 200:
            raise ValueError("Evernote token exchange failed.")
        tokens = require_read_only(OAuthToken.model_validate(response.json()))
        if not tokens.refresh_token:
            raise ValueError("Evernote grant cannot be refreshed.")
        store = self._store_factory()
        await store.set_client_info(client)
        await store.set_tokens(tokens)
        upstream.sign_in_required.clear()
        return payload
