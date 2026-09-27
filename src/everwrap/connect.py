"""Authenticate to official Evernote MCP and inspect get_note's schema only.

This setup command has no call_tool or resource-read path. It cannot read notes.
Tokens stay in the OS credential store, not configuration files or stdout.
"""

import asyncio
import json
import logging
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from .policy import SingleNotePolicy
from .credentials import secure_keyring
from .oauth_refresh import PersistedRefreshMixin

SERVER_URL = "https://mcp.evernote.com/mcp"
CALLBACK_URL = "http://127.0.0.1:8766/callback"
KEYCHAIN_SERVICE = "EverWrap:official-evernote-mcp"


class ReadOnlyOAuthProvider(PersistedRefreshMixin, OAuthClientProvider):
    """Pin consent to read, including after the SDK's scope discovery.

    MCP SDK 2.2.0 overwrites client_metadata.scope using advertised scopes.
    This small version-pinned hook constrains the final authorization request;
    the redirect handler and token store independently enforce the same policy.
    """

    async def _perform_authorization(self):
        self.context.client_metadata.scope = "read"
        return await super()._perform_authorization()


def require_read_only(tokens):
    if not tokens.scope or set(tokens.scope.split()) != {"read"}:
        raise ValueError("EverWrapMCP requires a read-only OAuth grant.")
    return tokens


class KeychainStore:
    def __init__(self):
        # Explicit backend: never silently falls back to plaintext token storage.
        self._keyring = secure_keyring()

    async def get_tokens(self):
        value = await asyncio.to_thread(self._keyring.get_password, KEYCHAIN_SERVICE, "tokens")
        return require_read_only(OAuthToken.model_validate_json(value)) if value else None

    async def set_tokens(self, tokens):
        require_read_only(tokens)
        await asyncio.to_thread(self._keyring.set_password, KEYCHAIN_SERVICE,
                                "tokens", tokens.model_dump_json())

    async def get_client_info(self):
        value = await asyncio.to_thread(self._keyring.get_password, KEYCHAIN_SERVICE, "client")
        return OAuthClientInformationFull.model_validate_json(value) if value else None

    async def set_client_info(self, client_info):
        await asyncio.to_thread(self._keyring.set_password, KEYCHAIN_SERVICE,
                                "client", client_info.model_dump_json())


class EncryptedFileStore:
    """Headless alternative to KeychainStore; selected only by explicit configuration."""

    def __init__(self, path: Path, key_file: Path):
        from .secret_store import EncryptedFile
        self._file = EncryptedFile(path, key_file)

    async def _get(self, field):
        return (await asyncio.to_thread(self._file.load)).get(field)

    async def _set(self, field, value):
        def update():
            data = self._file.load()
            data[field] = value
            self._file.save(data)
        await asyncio.to_thread(update)

    async def get_tokens(self):
        value = await self._get("tokens")
        return require_read_only(OAuthToken.model_validate_json(value)) if value else None

    async def set_tokens(self, tokens):
        require_read_only(tokens)
        await self._set("tokens", tokens.model_dump_json())

    async def get_client_info(self):
        value = await self._get("client")
        return OAuthClientInformationFull.model_validate_json(value) if value else None

    async def set_client_info(self, client_info):
        await self._set("client", client_info.model_dump_json())


def credential_store():
    """OS credential store by default; encrypted file only when a key file is configured."""
    key_file = os.environ.get("EVERWRAP_CREDENTIAL_KEY_FILE")
    if not key_file:
        return KeychainStore()
    data_dir = os.environ.get("EVERWRAP_DATA_DIR")
    if not data_dir:
        raise RuntimeError("EVERWRAP_DATA_DIR is required with EVERWRAP_CREDENTIAL_KEY_FILE.")
    return EncryptedFileStore(Path(data_dir) / "evernote-credentials.enc", Path(key_file))


def policy_path() -> Path:
    configured = os.environ.get("EVERWRAP_POLICY")
    return (Path(configured) if configured
            else Path(__file__).resolve().parents[2] / ".everwrap-local.json")


def parse_callback(target: str, expected_state: str | None) -> AuthorizationCodeResult:
    url = urlsplit(target)
    if url.scheme or url.netloc or url.path != "/callback" or url.fragment:
        raise ValueError("Invalid authorization callback.")
    params = parse_qs(url.query, keep_blank_values=True, max_num_fields=10)
    if (not expected_state or "error" in params
            or any(len(params.get(k, [])) != 1 for k in ("code", "state"))
            or params["state"][0] != expected_state or not params["code"][0]
            or ("iss" in params and len(params["iss"]) != 1)):
        raise ValueError("Invalid authorization callback.")
    return AuthorizationCodeResult(code=params["code"][0], state=params["state"][0],
                                   iss=params.get("iss", [None])[0])


class LoopbackCallback:
    def __init__(self):
        self.result = asyncio.get_running_loop().create_future()
        self.expected_state = None

    async def open_browser(self, authorization_url: str):
        url = urlsplit(authorization_url)
        if (url.scheme != "https" or url.username or url.password
                or not url.hostname or not (url.hostname == "evernote.com"
                or url.hostname.endswith(".evernote.com"))):
            raise ValueError("Unexpected authorization host.")
        params = parse_qs(url.query)
        states = params.get("state", [])
        if len(states) != 1 or not states[0]:
            raise ValueError("Missing authorization state.")
        if params.get("scope") != ["read"]:
            raise ValueError("Authorization must request read-only access.")
        self.expected_state = states[0]
        # This URL contains a public client ID, state, and PKCE challenge, not
        # the verifier, authorization code, or access/refresh tokens.
        print("Authorize EverWrapMCP with read-only access:", flush=True)
        print(authorization_url, flush=True)
        try:
            opened = await asyncio.to_thread(webbrowser.open, authorization_url)
        except Exception:
            opened = False
        print("Complete Evernote sign-in in the browser." if opened else
              "Open the link above on this computer to complete sign-in.", flush=True)

    async def wait(self):
        return await asyncio.wait_for(self.result, timeout=600)

    async def handle(self, reader, writer):
        status = "400 Bad Request"
        message = b"Invalid callback. Return to the Evernote sign-in page."
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            if method != "GET" or self.result.done():
                raise ValueError
            result = parse_callback(target, self.expected_state)
            self.result.set_result(result)
            status = "200 OK"
            message = b"Authorization response received. You can return to EverWrapMCP."
        except Exception:
            pass  # Never log callback URLs, authorization codes, or request bytes.
        try:
            writer.write((f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                          f"Content-Length: {len(message)}\r\nCache-Control: no-store\r\n"
                          "Connection: close\r\n\r\n").encode() + message)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


async def inspect_read_schema(client):
    """Only metadata discovery. Never calls an upstream note or search tool."""
    cursor = None
    for _ in range(10):
        page = await client.list_tools(cursor=cursor)
        for tool in page.tools:
            if tool.name == "get_note":
                return {"name": tool.name, "input_schema": tool.input_schema,
                        "output_schema": tool.output_schema}
        cursor = page.next_cursor
        if cursor is None:
            break
    raise RuntimeError("Expected get_note tool was not found.")


async def connect():
    # Validate local restrictions before opening any connection. This is not yet
    # a note fetcher; the subsequent adapter must enforce this same policy.
    SingleNotePolicy.from_file(policy_path())
    callback = LoopbackCallback()
    oauth = ReadOnlyOAuthProvider(
        server_url=SERVER_URL,
        client_metadata=OAuthClientMetadata(
            client_name="EverWrapMCP",
            redirect_uris=[AnyUrl(CALLBACK_URL)],
            token_endpoint_auth_method="none",
            scope="read",
        ),
        storage=credential_store(), redirect_handler=callback.open_browser,
        callback_handler=callback.wait,
    )
    # A container publishes this only on host loopback; see docs/REMOTE.md.
    bind = os.environ.get("EVERWRAP_CALLBACK_HOST", "127.0.0.1")
    listener = await asyncio.start_server(callback.handle, bind, 8766, limit=8192)
    async with listener:
        async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(60, connect=30)) as http:
            transport = streamable_http_client(SERVER_URL, http_client=http)
            async with Client(transport, read_timeout_seconds=660) as client:
                schema = await inspect_read_schema(client)
                print("Connected. No notes were read.")
                print(json.dumps(schema, indent=2))


def main():
    # Authentication libraries may otherwise include credential-bearing URLs in
    # exceptions/logs. This dedicated command emits only static failures.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(connect())
    except KeyboardInterrupt:
        print("Connection setup cancelled.", file=sys.stderr)
        return 130
    except Exception:
        print("Evernote connection setup did not complete. No notes were read. "
              "Check network access, sign-in, and availability of localhost port 8766.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
