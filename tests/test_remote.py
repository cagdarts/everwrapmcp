"""Remote connector OAuth + MCP boundary, in-process with a fictional service."""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from cryptography.fernet import Fernet

from everwrap.remote import build_app, public_url
from everwrap.remote_auth import (ACCESS_TTL, MAX_FAILURES, SingleUserAuthProvider,
                                  hash_passphrase, parse_passphrase_hash, verify_passphrase)
from everwrap.secret_store import EncryptedFile

BASE = "https://everwrap.example.test"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PASSPHRASE = "correct horse battery staple 42"
HASH = hash_passphrase(PASSPHRASE)


class FictionalService:
    async def read_safe_note(self, note_id, **selection):
        return {"id": note_id, "title": "Garden", "content": "Bring seeds."}

    async def search_safe_notes(self, query, sort="updated_desc", limit=5):
        return [{"id": "11111111-1111-4111-8111-111111111111", "title": "Garden", "snippet": "seeds"}]

    async def semantic_search_safe_notes(self, query, limit=3):
        return []


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


def fake_client(client_id):
    return type("C", (), {"client_id": client_id})()


def fake_params():
    return type("P", (), {"code_challenge": "x", "redirect_uri": REDIRECT, "state": "s",
                          "redirect_uri_provided_explicitly": True, "resource": None,
                          "scopes": None})()


def make_provider(tmp_path, clock=None):
    key = tmp_path / "key"
    if not key.exists():
        key.write_bytes(Fernet.generate_key())
    return SingleUserAuthProvider(
        issuer_url=BASE, resource_url=f"{BASE}/mcp", passphrase_hash=HASH,
        state_file=EncryptedFile(tmp_path / "state.enc", key), clock=clock or Clock())


def run_with_client(provider, scenario):
    app = build_app(base_url=BASE, service=FictionalService(), provider=provider)

    async def main():
        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx2.AsyncClient(transport=transport, base_url=BASE) as client:
                return await scenario(client)

    return asyncio.run(main())


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def register(client, redirect=REDIRECT):
    return await client.post("/register", json={
        "redirect_uris": [redirect], "client_name": "Claude",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})


async def start_authorization(client, client_id, challenge, resource=f"{BASE}/mcp"):
    return await client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": "synthetic-state", "resource": resource})


async def login(client, passphrase=PASSPHRASE):
    """Register, authorize and submit the passphrase. Returns (client_id, verifier, response)."""
    client_id = (await register(client)).json()["client_id"]
    verifier, challenge = pkce()
    authorize = await start_authorization(client, client_id, challenge)
    assert authorize.status_code == 302
    login_url = authorize.headers["location"]
    assert login_url.startswith(f"{BASE}/login?request=")
    request_id = parse_qs(urlsplit(login_url).query)["request"][0]
    page = await client.get(login_url)
    assert page.status_code == 200 and "Claude" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    response = await client.post("/login", data={"request": request_id, "passphrase": passphrase})
    return client_id, verifier, request_id, response


async def full_tokens(client):
    client_id, verifier, _, response = await login(client)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["synthetic-state"]
    token = await client.post("/token", data={
        "grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": REDIRECT,
        "client_id": client_id, "code_verifier": verifier, "resource": f"{BASE}/mcp"})
    assert token.status_code == 200, token.text
    return client_id, token.json()


def mcp_body(response):
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


async def mcp(client, token, method, params=None, id_=1, headers=None):
    return await client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method,
                                           "params": params or {}},
                             headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/json, text/event-stream",
                                      **(headers or {})})


async def initialize(client, token):
    response = await mcp(client, token, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "synthetic", "version": "1"}})
    return response


def test_passphrase_hash_rules():
    with pytest.raises(ValueError):
        hash_passphrase("too short")
    parsed = parse_passphrase_hash(HASH)
    assert verify_passphrase(PASSPHRASE, parsed)
    assert not verify_passphrase(PASSPHRASE + "x", parsed)
    assert not verify_passphrase("", parsed)
    assert PASSPHRASE not in HASH
    for bad in ["", "plain", "scrypt$2$8$1$AAAA$AAAA", HASH.replace("scrypt", "md5")]:
        with pytest.raises(ValueError):
            parse_passphrase_hash(bad)


@pytest.mark.parametrize("value", ["http://x.test", "https://x.test/mcp", "https://u:p@x.test",
                                   "https://x.test?q=1", ""])
def test_public_url_must_be_https_origin(value):
    with pytest.raises(ValueError):
        public_url(value)


def test_metadata_advertises_pkce_registration_and_protected_resource(tmp_path):
    async def scenario(client):
        meta = (await client.get("/.well-known/oauth-authorization-server")).json()
        assert meta["issuer"].rstrip("/") == BASE
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert meta["registration_endpoint"] == f"{BASE}/register"
        resource = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert resource.status_code == 200
        assert (await client.get("/healthz")).text == "ok"
    run_with_client(make_provider(tmp_path), scenario)


def test_unauthenticated_and_invalid_tokens_are_rejected(tmp_path):
    async def scenario(client):
        missing = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                    headers={"Accept": "application/json, text/event-stream"})
        assert missing.status_code == 401
        assert "resource_metadata" in missing.headers.get("www-authenticate", "")
        assert (await initialize(client, "forged-token")).status_code == 401
    run_with_client(make_provider(tmp_path), scenario)


def test_registration_requires_allowlisted_redirect(tmp_path):
    async def scenario(client):
        denied = await register(client, "https://attacker.example/callback")
        assert denied.status_code == 400
        assert denied.json()["error"] == "invalid_redirect_uri"
        assert (await register(client)).status_code == 201
    run_with_client(make_provider(tmp_path), scenario)


def test_full_flow_reaches_the_same_three_tools(tmp_path):
    async def scenario(client):
        _, tokens = await full_tokens(client)
        assert tokens["scope"] == "everwrap" and tokens["expires_in"] == ACCESS_TTL
        init = await initialize(client, tokens["access_token"])
        assert init.status_code == 200, init.text
        session = init.headers.get("mcp-session-id")
        extra = {"mcp-session-id": session} if session else {}
        await mcp(client, tokens["access_token"], "notifications/initialized", headers=extra)
        listed = mcp_body(await mcp(client, tokens["access_token"], "tools/list", id_=2, headers=extra))
        assert [t["name"] for t in listed["result"]["tools"]] == [
            "read_safe_note", "search_safe_notes", "semantic_search_safe_notes", "list_safe_notebooks"]
        called = mcp_body(await mcp(client, tokens["access_token"], "tools/call",
                                    {"name": "search_safe_notes", "arguments": {"query": "garden"}},
                                    id_=3, headers=extra))
        assert called["result"]["structuredContent"]["notes"][0]["title"] == "Garden"
    run_with_client(make_provider(tmp_path), scenario)


def test_wrong_host_header_is_refused(tmp_path):
    async def scenario(client):
        _, tokens = await full_tokens(client)
        response = await mcp(client, tokens["access_token"], "tools/list",
                             headers={"Host": "attacker.example"})
        assert response.status_code == 421
    run_with_client(make_provider(tmp_path), scenario)


def test_wrong_passphrase_then_lockout(tmp_path):
    async def scenario(client):
        _, _, request_id, response = await login(client, "wrong passphrase value!!")
        assert response.status_code == 403 and "Incorrect passphrase" in response.text
        for _ in range(MAX_FAILURES - 1):
            await client.post("/login", data={"request": request_id, "passphrase": "still wrong!!"})
        locked = await client.post("/login", data={"request": request_id, "passphrase": PASSPHRASE})
        assert locked.status_code == 403 and "Too many failed attempts" in locked.text
    run_with_client(make_provider(tmp_path), scenario)


def test_login_request_is_single_use_and_unknown_ids_expire(tmp_path):
    async def scenario(client):
        _, _, request_id, response = await login(client)
        assert response.status_code == 302
        again = await client.post("/login", data={"request": request_id, "passphrase": PASSPHRASE})
        assert again.status_code == 400 and "expired" in again.text
        assert (await client.get("/login?request=unknown")).status_code == 400
    run_with_client(make_provider(tmp_path), scenario)


def test_foreign_resource_is_refused(tmp_path):
    async def scenario(client):
        client_id = (await register(client)).json()["client_id"]
        _, challenge = pkce()
        response = await start_authorization(client, client_id, challenge, "https://other.test/mcp")
        assert "/login" not in response.headers.get("location", "")
        assert "invalid_target" in response.headers.get("location", "") + response.text
    run_with_client(make_provider(tmp_path), scenario)


def test_refresh_rotates_and_revocation_stops_access(tmp_path):
    async def scenario(client):
        client_id, tokens = await full_tokens(client)
        refreshed = await client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": client_id})
        assert refreshed.status_code == 200, refreshed.text
        new = refreshed.json()
        assert new["refresh_token"] != tokens["refresh_token"]
        assert (await initialize(client, tokens["access_token"])).status_code == 401
        replay = await client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": client_id})
        assert replay.status_code == 400
        assert (await initialize(client, new["access_token"])).status_code == 200
        revoked = await client.post("/revoke", data={"token": new["refresh_token"], "client_secret": "",
                                                     "client_id": client_id})
        assert revoked.status_code == 200, revoked.text
        assert (await initialize(client, new["access_token"])).status_code == 401
    run_with_client(make_provider(tmp_path), scenario)


def test_access_tokens_expire(tmp_path):
    clock = Clock()

    async def scenario(client):
        _, tokens = await full_tokens(client)
        clock.now += ACCESS_TTL + 1
        assert (await initialize(client, tokens["access_token"])).status_code == 401
    run_with_client(make_provider(tmp_path, clock), scenario)


def test_clients_and_refresh_tokens_survive_restart_encrypted(tmp_path):
    async def first(client):
        return await full_tokens(client)
    client_id, tokens = run_with_client(make_provider(tmp_path), first)
    raw = (tmp_path / "state.enc").read_bytes()
    assert tokens["refresh_token"].encode() not in raw and client_id.encode() not in raw

    async def second(client):
        refreshed = await client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": client_id})
        assert refreshed.status_code == 200, refreshed.text
        assert (await initialize(client, tokens["access_token"])).status_code == 401
    run_with_client(make_provider(tmp_path), second)


def test_hash_passphrase_command_writes_private_file(tmp_path, monkeypatch, capsys):
    from everwrap import remote
    answers = iter([PASSPHRASE, PASSPHRASE])
    monkeypatch.setattr(remote.getpass, "getpass", lambda prompt="": next(answers))
    target = tmp_path / "passphrase.hash"
    assert remote.main(["hash-passphrase", "--output", str(target)]) == 0
    stored = target.read_text().strip()
    assert verify_passphrase(PASSPHRASE, parse_passphrase_hash(stored))
    assert PASSPHRASE not in capsys.readouterr().out
    answers = iter([PASSPHRASE, "different passphrase value"])
    assert remote.main(["hash-passphrase", "--output", str(target)]) == 1


def test_registration_spam_cannot_evict_a_signed_in_client(tmp_path, monkeypatch):
    import everwrap.remote_auth as auth
    monkeypatch.setattr(auth, "MAX_CLIENTS", 3)

    async def scenario(client):
        owner_id, tokens = await full_tokens(client)
        for _ in range(5):  # Idle registrations are evicted, never the active owner.
            assert (await register(client)).status_code == 201
        refreshed = await client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": owner_id})
        assert refreshed.status_code == 200, refreshed.text
    run_with_client(make_provider(tmp_path), scenario)


def test_concurrent_logins_verify_one_at_a_time_and_respect_lockout(tmp_path):
    provider = make_provider(tmp_path)
    running, peak = 0, 0
    original = verify_passphrase

    def tracking(passphrase, parsed):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            return original(passphrase, parsed)
        finally:
            running -= 1

    async def scenario():
        from everwrap import remote_auth
        remote_auth.verify_passphrase, saved = tracking, remote_auth.verify_passphrase
        try:
            ids = [(await provider.authorize(fake_client(f"c{i}"), fake_params())).split("request=")[1]
                   for i in range(12)]
            results = await asyncio.gather(*(provider.complete_login(i, "wrong passphrase!!") for i in ids),
                                           return_exceptions=True)
            return results
        finally:
            remote_auth.verify_passphrase = saved

    results = asyncio.run(scenario())
    assert peak == 1
    assert [str(r) for r in results].count("invalid") == MAX_FAILURES
    assert [str(r) for r in results].count("locked") == 12 - MAX_FAILURES


def test_oversized_registration_is_refused_before_touching_state(tmp_path):
    async def scenario(client):
        owner_id, tokens = await full_tokens(client)
        big = await client.post("/register", json={
            "redirect_uris": [REDIRECT], "client_name": "x" * 50_000,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
        assert big.status_code == 400
        refreshed = await client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": owner_id})
        assert refreshed.status_code == 200, refreshed.text
    run_with_client(make_provider(tmp_path), scenario)


def test_failed_save_keeps_the_existing_refresh_token(tmp_path):
    provider = make_provider(tmp_path)

    async def scenario(client):
        owner_id, tokens = await full_tokens(client)
        original = provider._state_file.save
        provider._state_file.save = lambda data: (_ for _ in ()).throw(OSError("disk full"))
        failed = await client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": owner_id})
        assert failed.status_code >= 400
        provider._state_file.save = original
        retried = await client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": owner_id})
        assert retried.status_code == 200, retried.text
    run_with_client(provider, scenario)


def test_lockout_is_per_source_with_a_global_ceiling(tmp_path):
    from everwrap.remote_auth import MAX_GLOBAL_FAILURES
    provider = make_provider(tmp_path)

    async def attempt(source, passphrase, n):
        request = (await provider.authorize(fake_client(f"{source}-{n}"), fake_params())).split("request=")[1]
        try:
            await provider.complete_login(request, passphrase, source)
            return "ok"
        except PermissionError as error:
            return str(error)

    async def scenario():
        for n in range(MAX_FAILURES):
            assert await attempt("attacker", "wrong passphrase!!", n) == "invalid"
        assert await attempt("attacker", PASSPHRASE, 99) == "locked"
        assert await attempt("owner", PASSPHRASE, 0) == "ok"
        sources = (MAX_GLOBAL_FAILURES - MAX_FAILURES) // MAX_FAILURES + 1
        for s in range(sources):
            for n in range(MAX_FAILURES):
                await attempt(f"spray{s}", "wrong passphrase!!", n)
        assert await attempt("owner", PASSPHRASE, 1) == "locked"
    asyncio.run(scenario())


def test_pending_requests_are_capped_per_client(tmp_path):
    from everwrap.remote_auth import MAX_PENDING_PER_CLIENT
    provider = make_provider(tmp_path)

    async def scenario():
        owner = (await provider.authorize(fake_client("owner"), fake_params())).split("request=")[1]
        for _ in range(MAX_PENDING_PER_CLIENT * 5):
            await provider.authorize(fake_client("spammer"), fake_params())
        assert provider.pending_summary(owner) is not None
        assert sum(1 for v in provider._pending.values() if v[0] == "spammer") == MAX_PENDING_PER_CLIENT
    asyncio.run(scenario())


def test_login_page_allows_redirect_back_to_claude(tmp_path):
    async def scenario(client):
        client_id = (await register(client)).json()["client_id"]
        _, challenge = pkce()
        page = await client.get((await start_authorization(client, client_id, challenge)).headers["location"])
        policy = page.headers["content-security-policy"]
        assert "form-action 'self' https://claude.ai https://claude.com;" in policy
    run_with_client(make_provider(tmp_path), scenario)
