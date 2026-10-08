"""Protocol tests use synthetic data, never an Evernote connection."""

import asyncio

from mcp import Client

from everwrap.policy import SingleNotePolicy
from everwrap.server import build_server
from everwrap.service import SingleNoteService


NOTE_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


class NoFetchBackend:
    async def get_note(self, note_id):
        raise AssertionError("Backend must never be called while privacy gate is closed")


def test_protocol_exposes_only_safe_tools_and_denies_all_content():
    async def check():
        backend = NoFetchBackend()
        backend.calls = 0
        async def count_fetch(note_id):
            backend.calls += 1
            raise AssertionError("Unexpected fetch")
        backend.get_note = count_fetch
        server = build_server(SingleNoteService(SingleNotePolicy(NOTE_ID), backend))
        async with Client(server) as client:
            listing = await client.list_tools()
            assert {t.name for t in listing.tools} == {"read_safe_note", "search_safe_notes", "semantic_search_safe_notes", "list_safe_notebooks"}
            for tool in listing.tools:
                assert tool.input_schema["additionalProperties"] is False
                assert "Evernote" in tool.description
                assert "EverWrapMCP" in tool.description
            for name, arguments, message in [
                ("read_safe_note", {"note_id": NOTE_ID}, "Content blocked"),
                ("read_safe_note", {"note_id": OTHER_ID}, "Request denied"),
                ("read_safe_note", {"note_id": [NOTE_ID]}, "Request denied"),
                ("read_safe_note", {"note_id": NOTE_ID, "raw": True}, "Request denied"),
                ("read_safe_note", {}, "Request denied"),
                ("get_note", {"noteId": NOTE_ID}, "Request denied"),
                ("search_safe_notes", {"query": "synthetic"}, "Content blocked"),
                ("search_safe_notes", {"query": "synthetic", "notebook": "all"}, "Request denied"),
                ("search_safe_notes", {"query": "synthetic", "limit": True}, "Request denied"),
                ("search_safe_notes", {"query": "x" * 501}, "Request denied"),
            ]:
                result = await client.call_tool(name, arguments)
                assert result.is_error
                wire = result.model_dump_json()
                assert message in wire
                assert NOTE_ID not in wire and OTHER_ID not in wire
                assert result.structured_content is None
            assert backend.calls == 0
        capabilities = server.create_initialization_options().capabilities
        assert capabilities.resources is None
        assert capabilities.prompts is None
        initialization = server.create_initialization_options()
        assert initialization.server_name == "EverWrapMCP"
        assert "prefer these EverWrapMCP tools over Computer Use" in initialization.instructions
        assert "explicitly asks to open or operate the Evernote interface" in initialization.instructions
        assert "Evernote notes" in initialization.instructions
    asyncio.run(check())


def test_unexpected_service_errors_are_static():
    class BrokenService:
        async def read_safe_note(self, note_id):
            raise RuntimeError("SYNTHETIC_SECRET_DO_NOT_RETURN")

    async def check():
        async with Client(build_server(BrokenService())) as client:
            result = await client.call_tool("read_safe_note", {"note_id": NOTE_ID})
            assert result.is_error
            assert "SYNTHETIC_SECRET" not in result.model_dump_json()
    asyncio.run(check())
