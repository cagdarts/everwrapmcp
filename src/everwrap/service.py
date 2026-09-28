"""Transport-independent prototype; default output gate is closed.

This legacy single-note filtering service is exercised with synthetic backends.
The explicitly opted-in live unredacted service is implemented in live.py.
Injected sanitizers are trusted local code, never tool arguments. Mock sanitizers
in tests are not evidence of PII coverage.
"""

from dataclasses import dataclass, field
from typing import Protocol

from .policy import AccessDenied, SingleNotePolicy


class ProcessingBlocked(Exception):
    """A static error safe for a future MCP boundary to convert to a response."""


class UpstreamUnavailable(ProcessingBlocked):
    """Evernote reported a tool error; its message is never passed on."""


class EvernoteSignInRequired(ProcessingBlocked):
    """The stored Evernote grant is missing or no longer refreshes."""


@dataclass(frozen=True, slots=True)
class RawNote:
    note_id: str = field(repr=False)
    title: str = field(repr=False)
    content: str = field(repr=False)


class NoteBackend(Protocol):
    async def get_note(self, note_id: str) -> RawNote: ...


class Sanitizer(Protocol):
    def sanitize_text(self, text: str) -> str: ...


class SingleNoteService:
    def __init__(
        self, policy: SingleNotePolicy, backend: NoteBackend,
        sanitizer: Sanitizer | None = None,
    ):
        self._policy = policy
        self._backend = backend
        self._sanitizer = sanitizer

    async def read_safe_note(self, note_id: str) -> dict[str, str]:
        authorized = self._policy.authorize(note_id)  # Before ANY backend I/O.
        if self._sanitizer is None:
            raise ProcessingBlocked("Privacy processing is not enabled.")

        failed = False
        try:
            raw = await self._backend.get_note(authorized)
            if type(raw) is not RawNote:
                raise ValueError
            self._policy.authorize(raw.note_id)  # Reject wrong upstream identity.
            for value, limit in ((raw.title, 1000), (raw.content, 100_000)):
                if type(value) is not str or len(value) > limit:
                    raise ValueError
            title = self._sanitizer.sanitize_text(raw.title)
            content = self._sanitizer.sanitize_text(raw.content)
            for value, limit in ((title, 1000), (content, 100_000)):
                if type(value) is not str or len(value) > limit:
                    raise ValueError
            # Explicit projection only. Never serialize the upstream object.
            safe = {"id": authorized, "title": title, "content": content}
        except Exception:
            failed = True
        # Outside the except block: do not chain raw backend exception content.
        if failed:
            raise ProcessingBlocked("Content could not be safely processed.")
        return safe

    async def search_safe_notes(
        self, query: str, sort: str = "updated_desc", limit: int = 5,
    ) -> list[dict[str, str]]:
        if (type(query) is not str or not 1 <= len(query) <= 500
                or not query.strip() or type(limit) is not int or not 1 <= limit <= 10
                or sort not in ("updated_desc", "updated_asc", "relevance")):
            raise AccessDenied("Invalid search request.")
        # Single-note mode NEVER calls upstream search. Sort is immaterial for
        # at most one result. Query instructions cannot select another note.
        safe = await self.read_safe_note(self._policy.allowed_note_id)
        text = (safe["title"] + "\n" + safe["content"]).casefold()
        if not all(term in text for term in query.casefold().split()):
            return []
        return [{"id": safe["id"], "title": safe["title"],
                 "snippet": safe["content"][:300]}]
