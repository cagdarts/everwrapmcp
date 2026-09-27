"""Browser-based Evernote linking for the remote connector, against a fake Evernote."""

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from cryptography.fernet import Fernet
from mcp import Client
from mcp.shared.auth import OAuthToken

from everwrap import upstream
from everwrap.connect import EncryptedFileStore
from everwrap.evernote_link import (AUTHORIZE_URL, EVERNOTE_ISSUER, EVERNOTE_RESOURCE,
                                    EvernoteLinker)
from everwrap.server import build_server
from everwrap.service import EvernoteSignInRequired, ProcessingBlocked
from tests.test_remote import (BASE, PASSPHRASE, REDIRECT, FictionalService, make_provider,
                               pkce, register, run_with_client, start_authorization)
from everwrap.remote import build_app


class FakeEvernote:
    """Records requests; issues a code-bound, PKCE-checked read grant."""

    def __init__(self, scope="read", refresh="synthetic-evernote-refresh"):
        self.scope, self.refresh = scope, refresh
        self.registrations, self.token_requests = [], []
        self.challenges = {}

    def handler(self, request):
        if request.url.path == "/auth/register":
            body = json.loads(request.content)
            self.registrations.append(body)
            return httpx2.Response(201, json={**body, "client_id": "mcp_synthetic-web-client"})
        if request.url.path == "/auth/token":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if self.challenges.get(form["code"][0]) != challenge:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            return httpx2.Response(200, json={
                "access_token": "synthetic-evernote-access", "token_type": "Bearer",
                "expires_in": 3600, "refresh_token": self.refresh, "scope": self.scope})
        return httpx2.Response(404)

    def approve(self, authorize_url, code="synthetic-code"):
        """Simulate the owner approving on Evernote: returns the callback query."""
        query = parse_qs(urlsplit(authorize_url).query)
        self.challenges[code] = query["code_challenge"][0]
        return {"code": code, "state": query["state"][0], "iss": EVERNOTE_ISSUER}


@pytest.fixture
def store(tmp_path):
    key = tmp_path / "evernote-key"
    key.write_bytes(Fernet.generate_key())
    return EncryptedFileStore(tmp_path / "evernote.enc", key)


@pytest.fixture(autouse=True)
def clear_flag():
    upstream.sign_in_required.clear()
    yield
    upstream.sign_in_required.clear()


def make_linker(store, fake):
    return EvernoteLinker(BASE, store_factory=lambda: store,
                          http_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handler)))


def test_authorize_url_is_read_only_pkce_to_our_callback(store):
    fake = FakeEvernote()
    linker = make_linker(store, fake)

    async def run():
        url = await linker.start("payload")
        again = await linker.start("payload")
        return url, again
    url, again = asyncio.run(run())
    assert url.startswith(AUTHORIZE_URL + "?")
    query = parse_qs(urlsplit(url).query)
    assert query["redirect_uri"] == [f"{BASE}/evernote/callback"]
    assert query["scope"] == ["read"] and query["code_challenge_method"] == ["S256"]
    assert query["resource"] == [EVERNOTE_RESOURCE] and query["client_id"] == ["mcp_synthetic-web-client"]
    assert len(fake.registrations) == 1  # registered once, then reused
    assert fake.registrations[0]["redirect_uris"] == [f"{BASE}/evernote/callback"]
    assert parse_qs(urlsplit(again).query)["state"] != query["state"]


def test_finish_stores_read_grant_with_issuer_and_clears_flag(store):
    fake = FakeEvernote()
    linker = make_linker(store, fake)
    upstream.sign_in_required.set()

    async def run():
        assert await linker.needed()
        callback = fake.approve(await linker.start({"login": 1}))
        payload = await linker.finish(callback["state"], callback["code"], callback["iss"])
        return payload, await store.get_tokens(), await store.get_client_info(), await linker.needed()
    payload, tokens, client, needed = asyncio.run(run())
    assert payload == {"login": 1}
    assert tokens.scope == "read" and tokens.refresh_token == "synthetic-evernote-refresh"
    assert client.client_id == "mcp_synthetic-web-client" and str(client.issuer).rstrip("/") == EVERNOTE_ISSUER
    assert needed is False and not upstream.sign_in_required.is_set()
    request = fake.token_requests[0]
    assert request["redirect_uri"] == [f"{BASE}/evernote/callback"] and request["resource"] == [EVERNOTE_RESOURCE]


@pytest.mark.parametrize("change", ["unknown_state", "reused_state", "wrong_issuer", "wrong_scope",
                                    "no_refresh", "bad_verifier"])
def test_finish_rejects_and_stores_nothing(store, change):
    fake = FakeEvernote(scope="read write" if change == "wrong_scope" else "read",
                        refresh=None if change == "no_refresh" else "synthetic-evernote-refresh")
    linker = make_linker(store, fake)

    async def run():
        callback = fake.approve(await linker.start("payload"))
        if change == "unknown_state":
            callback["state"] = "forged"
        if change == "wrong_issuer":
            callback["iss"] = "https://attacker.example"
        if change == "bad_verifier":
            fake.challenges[callback["code"]] = "different"
        if change == "reused_state":
            fake.challenges["other"] = fake.challenges[callback["code"]]
            await linker.finish(callback["state"], callback["code"], callback["iss"])
            await store.set_tokens(type(await store.get_tokens())(access_token="kept", token_type="Bearer",
                                                                   refresh_token="kept", scope="read"))
        with pytest.raises((LookupError, ValueError)):
            await linker.finish(callback["state"], callback["code"], callback["iss"])
        return await store.get_tokens()
    tokens = asyncio.run(run())
    if change == "reused_state":
        assert tokens.refresh_token == "kept"  # a replayed callback changes nothing
    else:
        assert tokens is None


def test_expired_state_is_refused(store):
    fake = FakeEvernote()
    clock = [1_000.0]
    linker = EvernoteLinker(BASE, store_factory=lambda: store, clock=lambda: clock[0],
                            http_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handler)))

    async def run():
        callback = fake.approve(await linker.start("payload"))
        clock[0] += 601
        with pytest.raises(LookupError):
            await linker.finish(callback["state"], callback["code"], callback["iss"])
    asyncio.run(run())


def provider_with_linker(tmp_path, store, fake):
    provider = make_provider(tmp_path)
    provider.linker = make_linker(store, fake)
    return provider


async def login_to(client, reconnect=False):
    client_id = (await register(client)).json()["client_id"]
    verifier, challenge = pkce()
    login_url = (await start_authorization(client, client_id, challenge)).headers["location"]
    request_id = parse_qs(urlsplit(login_url).query)["request"][0]
    page = await client.get(login_url)
    data = {"request": request_id, "passphrase": PASSPHRASE}
    if reconnect:
        data["reconnect_evernote"] = "1"
    return client_id, verifier, page, await client.post("/login", data=data)


def test_login_without_evernote_grant_goes_through_evernote_then_claude(tmp_path, store):
    fake = FakeEvernote()
    provider = provider_with_linker(tmp_path, store, fake)

    async def scenario(client):
        client_id, verifier, page, response = await login_to(client)
        assert "Evernote sign-in (read-only) opens next" in page.text
        assert "https://accounts.evernote.com" in page.headers["content-security-policy"]
        assert response.status_code == 302 and response.headers["location"].startswith(AUTHORIZE_URL)
        callback = fake.approve(response.headers["location"])
        back = await client.get("/evernote/callback", params=callback)
        assert back.status_code == 302 and back.headers["location"].startswith(REDIRECT)
        query = parse_qs(urlsplit(back.headers["location"]).query)
        token = await client.post("/token", data={
            "grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": REDIRECT,
            "client_id": client_id, "code_verifier": verifier, "resource": f"{BASE}/mcp"})
        assert token.status_code == 200, token.text
        replay = await client.get("/evernote/callback", params=callback)
        assert replay.status_code == 400
    run_with_client(provider, scenario)
    assert asyncio.run(store.get_tokens()).scope == "read"


def test_login_with_working_grant_skips_evernote_unless_reconnect_requested(tmp_path, store):
    fake = FakeEvernote()
    provider = provider_with_linker(tmp_path, store, fake)
    asyncio.run(store.set_tokens(OAuthToken(access_token="a", token_type="Bearer", refresh_token="r", scope="read")))

    async def scenario(client):
        _, _, page, direct = await login_to(client)
        assert "Also reconnect Evernote" in page.text
        assert direct.headers["location"].startswith(REDIRECT)
        _, _, _, relink = await login_to(client, reconnect=True)
        assert relink.headers["location"].startswith(AUTHORIZE_URL)
    run_with_client(provider, scenario)


def test_declined_or_broken_callbacks_issue_no_code(tmp_path, store):
    fake = FakeEvernote()
    provider = provider_with_linker(tmp_path, store, fake)

    async def scenario(client):
        _, _, _, response = await login_to(client)
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        declined = await client.get("/evernote/callback", params={"error": "access_denied", "state": state})
        assert declined.status_code == 400 and "not granted" in declined.text
        later = await client.get("/evernote/callback", params={"code": "x", "state": state})
        assert later.status_code == 400  # declined state was discarded
        assert (await client.get("/evernote/callback")).status_code == 400
    run_with_client(provider, scenario)
    assert asyncio.run(store.get_tokens()) is None


async def call_search(service):
    async with Client(build_server(service)) as client:
        return await client.call_tool("search_safe_notes", {"query": "garden"})


@pytest.mark.parametrize("wrap", ["plain", "group", "chained"])
def test_tool_reports_evernote_sign_in_needed(wrap):
    class NeedsSignIn(FictionalService):
        async def search_safe_notes(self, query, sort="updated_desc", limit=5):
            error = EvernoteSignInRequired("x")
            if wrap == "group":
                raise ExceptionGroup("task group", [error])
            if wrap == "chained":
                try:
                    raise error
                except EvernoteSignInRequired as inner:
                    raise RuntimeError("wrapped") from inner
            raise error

    result = asyncio.run(call_search(NeedsSignIn()))
    assert result.is_error
    assert result.content[0].text.startswith("Evernote sign-in needed")


def test_other_failures_keep_their_messages():
    class Blocked(FictionalService):
        async def search_safe_notes(self, query, sort="updated_desc", limit=5):
            raise ProcessingBlocked("x")

    class Broken(FictionalService):
        async def search_safe_notes(self, query, sort="updated_desc", limit=5):
            raise RuntimeError("secret upstream detail")

    assert asyncio.run(call_search(Blocked())).content[0].text.startswith("Content blocked")
    broken = asyncio.run(call_search(Broken())).content[0].text
    assert broken == "Request could not be safely processed." and "secret" not in broken
