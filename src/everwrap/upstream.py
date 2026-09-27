"""Private, read-only Evernote transport. No upstream tools are exposed to MCP."""

import threading
from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from .connect import CALLBACK_URL, SERVER_URL, ReadOnlyOAuthProvider, credential_store
from .service import EvernoteSignInRequired, ProcessingBlocked


# Set when the SDK would need interactive Evernote sign-in; the remote login flow
# reads it to offer re-linking and clears it after a successful link.
sign_in_required = threading.Event()


async def require_setup(*args):
    # Interactive OAuth belongs in the local setup command or the remote login
    # page, never an MCP response.
    sign_in_required.set()
    raise EvernoteSignInRequired("Evernote sign-in needed.")


@asynccontextmanager
async def official_client():
    auth = ReadOnlyOAuthProvider(
        server_url=SERVER_URL,
        client_metadata=OAuthClientMetadata(
            client_name="EverWrapMCP",
            redirect_uris=[AnyUrl(CALLBACK_URL)],
            token_endpoint_auth_method="none", scope="read",
        ),
        storage=credential_store(), redirect_handler=require_setup, callback_handler=require_setup,
    )
    async with httpx2.AsyncClient(auth=auth, timeout=httpx2.Timeout(60, connect=30)) as http:
        async with Client(streamable_http_client(SERVER_URL, http_client=http),
                          read_timeout_seconds=60) as client:
            yield client


class OfficialBackend:
    def __init__(self, policy, client_factory=official_client):
        self.policy = policy
        self.client_factory = client_factory

    async def semantic_search(self, query, max_results):
        if self.policy.access_mode != 'denylist' or self.policy.content_mode != 'redacted':
            raise ProcessingBlocked('Safe semantic search is not enabled.')
        async with self.client_factory() as client:
            return await client.call_tool('semantic_search', {
                'query': query, 'maxResults': max_results,
                'clientTimeZone': 'UTC', 'keywordSearchFallback': False,
            })

    async def get_note(self, note_id):
        authorized = self.policy.authorize(note_id)  # Before transport/auth/network.
        if self.policy.content_mode not in ("unredacted", "redacted"):
            raise ProcessingBlocked("Content output is not enabled.")
        async with self.client_factory() as client:
            return await client.call_tool("get_note", {"noteId": authorized})

    async def search_notes(self, query, sort, start_index, max_results):
        if self.policy.access_mode != "denylist" or self.policy.content_mode not in ("unredacted", "redacted"):
            raise ProcessingBlocked("Account search is not enabled.")
        async with self.client_factory() as client:
            return await client.call_tool("search_notes", {
                "query": query, "sortBy": "relevance" if sort == "relevance" else "updated",
                "ascending": sort == "updated_asc", "startIndex": start_index,
                "maxResults": max_results, "clientTimeZone": "UTC",
                "fullBooleanSearch": True,
            })
