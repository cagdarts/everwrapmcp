"""Notebook listing and notebook-scoped keyword search; synthetic, never contacts Evernote."""

import asyncio

import pytest
from mcp import Client

from everwrap.live import NoteService
from everwrap.policy import AccessDenied
from everwrap.server import build_server
from everwrap.service import ProcessingBlocked
from tests.test_live import ALLOWED, BLOCKED, Backend, hit, policy, response

PRODUCT = "33333333-3333-4333-8333-333333333333"
JOURNAL = "44444444-4444-4444-8444-444444444444"


def notebook(identity, label):
    return {"notebookId": identity, "label": label, "searchFilter": f'nbGuid:"{identity}"',
            "score": None, "stack": None, "primaryAccess": "OWNED"}


class NotebookBackend(Backend):
    def __init__(self, notebooks=None, **kwargs):
        super().__init__(**kwargs)
        self.notebook_queries = []
        self.notebooks = notebooks if notebooks is not None else [
            notebook(PRODUCT, "Product manager notebook"), notebook(JOURNAL, "Journal")]

    async def search_notebooks(self, query, max_results):
        self.notebook_queries.append((query, max_results))
        return response({"hits": self.notebooks, "totalResultCount": len(self.notebooks),
                         "startIndex": 0, "isLastPage": True})


class Upper:
    """Stand-in redactor proving names pass through masking."""
    def sanitize_text(self, text): return text.upper()
    def sanitize_markup(self, text): return text.upper()


def run(coro):
    return asyncio.run(coro)


def test_list_returns_ids_and_masked_names():
    backend = NotebookBackend()
    service = NoteService(policy("redacted"), backend, Upper())
    listed = run(service.list_safe_notebooks("prod", 5))
    assert listed == [{"id": PRODUCT, "name": "PRODUCT MANAGER NOTEBOOK", "content_mode": "redacted"},
                      {"id": JOURNAL, "name": "JOURNAL", "content_mode": "redacted"}]
    assert backend.notebook_queries == [("prod", 100)]
    assert all("searchFilter" not in row and "filter" not in row for row in listed)


def test_notebook_search_adds_exact_filter_and_still_drops_blocked_notes():
    backend = NotebookBackend(pages=[response({"hits": [hit(ALLOWED), hit(BLOCKED, "Blocked")],
                                               "isLastPage": True})])
    service = NoteService(policy("redacted"), backend, Upper())
    notes = run(service.search_safe_notes("roadmap", notebook_id=PRODUCT.upper()))
    assert backend.searches[0][0] == f'nbGuid:"{PRODUCT}" roadmap'
    assert [n["id"] for n in notes] == [ALLOWED]


def test_notebook_search_without_query_lists_that_notebook():
    backend = NotebookBackend()
    run(NoteService(policy(), backend).search_safe_notes(notebook_id=JOURNAL))
    assert backend.searches[0][0] == f'nbGuid:"{JOURNAL}"'


@pytest.mark.parametrize("arguments", [{"notebook_id": "55555555-5555-4555-8555-555555555555"},
                                       {"notebook_id": "not-a-uuid"}, {"query": "  "},
                                       {"query": "x" * 501}])
def test_unknown_notebook_or_empty_request_is_denied(arguments):
    backend = NotebookBackend()
    with pytest.raises(AccessDenied):
        run(NoteService(policy(), backend).search_safe_notes(**arguments))
    assert backend.searches == []


@pytest.mark.parametrize("bad_id", ['x" OR -tag:"y', "../etc", "", None, 7])
def test_malformed_upstream_notebook_ids_are_refused(bad_id):
    backend = NotebookBackend(notebooks=[{**notebook(PRODUCT, "Product"), "notebookId": bad_id}])
    with pytest.raises(AccessDenied):
        run(NoteService(policy(), backend).search_safe_notes("x", notebook_id=PRODUCT))
    assert backend.searches == []


def test_names_with_quotes_and_emoji_do_not_affect_the_query():
    backend = NotebookBackend(notebooks=[notebook(PRODUCT, 'Ürün "pm" / 📁 notes')])
    run(NoteService(policy(), backend).search_safe_notes("x", notebook_id=PRODUCT))
    assert backend.searches[0][0] == f'nbGuid:"{PRODUCT}" x'


def test_notebooks_unavailable_in_single_note_or_blocked_mode():
    with pytest.raises(AccessDenied):
        run(NoteService(policy(access_mode="single_note"), NotebookBackend()).list_safe_notebooks())
    with pytest.raises(AccessDenied):
        run(NoteService(policy(access_mode="single_note"), NotebookBackend())
            .search_safe_notes("x", notebook_id=PRODUCT))
    with pytest.raises(ProcessingBlocked):
        run(NoteService(policy("blocked"), NotebookBackend()).list_safe_notebooks())


def test_mcp_tools_route_notebook_requests():
    backend = NotebookBackend()
    server = build_server(NoteService(policy(), backend))

    async def check():
        async with Client(server) as client:
            listed = await client.call_tool("list_safe_notebooks", {"query": "journal"})
            assert listed.structured_content["notebooks"][1]["id"] == JOURNAL
            found = await client.call_tool("search_safe_notes", {"notebook_id": JOURNAL})
            assert not found.is_error
            extra = await client.call_tool("list_safe_notebooks", {"workspace": "x"})
            assert extra.is_error
    run(check())
    assert backend.searches[0][0] == f'nbGuid:"{JOURNAL}"'
