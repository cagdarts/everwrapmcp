"""Locally configured reads with access checks and optional Presidio redaction.

Search responses may contain blocked metadata/snippets locally. Those rows are
dropped before projection; their note bodies are never fetched. Redacted mode
processes every returned text field without falling back to raw text on errors.
"""

import asyncio
import math
import re
from pathlib import Path

from .policy import AccessDenied, SingleNotePolicy, canonical_note_id
from .service import ProcessingBlocked, UpstreamUnavailable
from .upstream import OfficialBackend

MAX_NOTEBOOKS = 100
NOTEBOOK_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def canonical_notebook_id(value):
    if type(value) is not str or not NOTEBOOK_ID.fullmatch(value):
        raise AccessDenied("Invalid notebook.")
    return value.lower()


def bounded_text(value, limit, nullable=False):
    if nullable and value is None:
        return None
    if type(value) is not str or len(value) > limit:
        raise ProcessingBlocked("Invalid upstream text.")
    return value


def structured(result):
    # Never fall back to upstream text/error/resource content.
    if result.is_error:
        raise UpstreamUnavailable("Evernote reported an error.")
    if type(result.structured_content) is not dict:
        raise ProcessingBlocked("Invalid upstream response.")
    return result.structured_content


class NoteService:
    def __init__(self, policy, backend, redactor=None):
        self.policy = policy
        self.backend = backend
        self.redactor = redactor

    def require_opt_in(self):
        if self.policy.content_mode not in ("unredacted", "redacted"):
            raise ProcessingBlocked("Content output is not enabled.")
        if self.policy.content_mode == "redacted" and self.redactor is None:
            raise ProcessingBlocked("Redaction is unavailable.")

    async def text_field(self, value, limit, nullable=False, markup=False):
        value = bounded_text(value, limit, nullable)
        if value is None or self.policy.content_mode != "redacted":
            return value
        method = self.redactor.sanitize_markup if markup else self.redactor.sanitize_text
        output = await asyncio.to_thread(method, value)
        return bounded_text(output, limit)

    async def read_safe_note(self, note_id, **selection):
        authorized = self.policy.authorize(note_id)
        self.require_opt_in()
        from .sections import read_section, validate_selection
        validate_selection(**({'view': 'start', 'section': 0, 'offset': 0,
                               'max_chars': 4000, 'query': None} | selection))
        if selection and self.policy.content_mode != 'redacted':
            raise AccessDenied('Section reads require redacted mode.')
        data = structured(await self.backend.get_note(authorized))
        returned_id = self.policy.authorize(data.get("id"))
        if returned_id != authorized:
            raise ProcessingBlocked("Unexpected upstream identity.")
        if self.policy.content_mode == 'redacted':
            page = await asyncio.to_thread(read_section, data.get('content'), self.redactor, **selection)
            return {'id': authorized, 'title': await self.text_field(data.get('title'), 1000),
                    **page, 'content_format': 'plain_text', 'content_mode': 'redacted'}
        return {
            "id": authorized,
            "title": await self.text_field(data.get("title"), 1000),
            "content": await self.text_field(data.get("content"), 100_000, markup=True),
            "content_format": "plain_text" if self.policy.content_mode == "redacted" else "enml",
            "content_mode": self.policy.content_mode,
        }

    async def _notebooks(self, query):
        """Validated upstream notebook rows: (id, label, upstream id). Denylist only."""
        if self.policy.access_mode != "denylist":
            raise AccessDenied("Notebooks are not available in single-note mode.")
        page = structured(await self.backend.search_notebooks(query, MAX_NOTEBOOKS))
        hits = page.get("hits")
        if type(hits) is not list or len(hits) > MAX_NOTEBOOKS:
            raise ProcessingBlocked("Invalid notebook response.")
        rows = []
        for hit in hits:
            if type(hit) is not dict:
                raise ProcessingBlocked("Invalid notebook hit.")
            upstream_id = hit.get("notebookId")
            identity = canonical_notebook_id(upstream_id)
            rows.append((identity, bounded_text(hit.get("label"), 300), upstream_id))
        return rows

    async def list_safe_notebooks(self, query="", limit=20):
        if (type(query) is not str or len(query) > 200 or type(limit) is not int
                or not 1 <= limit <= MAX_NOTEBOOKS):
            raise AccessDenied("Invalid notebook request.")
        self.require_opt_in()
        rows = await self._notebooks(query.strip())
        # Names pass through the same masking as titles in redacted mode.
        return [{"id": identity, "name": await self.text_field(label, 300),
                 "content_mode": self.policy.content_mode}
                for identity, label, _ in rows[:limit]]

    async def search_safe_notes(self, query="", sort="updated_desc", limit=5, notebook_id=None):
        if (type(query) is not str or len(query) > 500
                or type(limit) is not int or not 1 <= limit <= 10
                or sort not in ("updated_desc", "updated_asc", "relevance")
                or (notebook_id is None and not query.strip())):
            raise AccessDenied("Invalid search request.")
        self.require_opt_in()
        if notebook_id is not None:
            wanted = canonical_notebook_id(notebook_id)
            matches = [upstream for identity, _, upstream in await self._notebooks("") if identity == wanted]
            if not matches:
                raise AccessDenied("Unknown notebook.")
            # Built locally from a validated GUID, never from upstream query text.
            query = (f'nbGuid:"{matches[0]}" ' + query.strip()).strip()
        if self.policy.access_mode == "single_note":
            note = await self.read_safe_note(self.policy.allowed_note_id)
            text = (note["title"] + "\n" + note["content"]).casefold()
            return ([{"id": note["id"], "title": note["title"], "snippet": note["content"][:300],
                      "content_mode": self.policy.content_mode}]
                    if all(word in text for word in query.casefold().split()) else [])

        results = []
        seen = set()
        # A bounded scan of the first 100 hits. Do not expose upstream totals,
        # blocked IDs, blocked titles, blocked snippets, or raw pagination data.
        for start in range(0, 100, 20):
            page = structured(await self.backend.search_notes(query, sort, start, 20))
            hits = page.get("hits")
            if type(hits) is not list or len(hits) > 20 or type(page.get("isLastPage")) is not bool:
                raise ProcessingBlocked("Invalid search response.")
            for hit in hits:
                if type(hit) is not dict:
                    raise ProcessingBlocked("Invalid search hit.")
                identity = canonical_note_id(hit.get("noteId"))
                try:
                    self.policy.authorize(identity)
                except AccessDenied:
                    continue  # No other fields from this row are inspected or returned.
                if identity in seen:
                    continue
                seen.add(identity)
                projected = {
                    "id": identity, "title": await self.text_field(hit.get("title"), 1000),
                    "snippet": await self.text_field(hit.get("snippet"), 5000, nullable=True, markup=True),
                    "content_mode": self.policy.content_mode,
                }
                # Keep sorted order, but do not leak date metadata in redacted mode.
                if self.policy.content_mode == "unredacted":
                    projected.update({
                        "created_at": bounded_text(hit.get("createdAt"), 100, nullable=True),
                        "updated_at": bounded_text(hit.get("updatedAt"), 100, nullable=True),
                    })
                results.append(projected)
                if len(results) == limit:
                    return results
            if page["isLastPage"] or not hits:
                break
        return results

    async def semantic_search_safe_notes(self, query, limit=3):
        if (type(query) is not str or not 1 <= len(query) <= 500 or not query.strip()
                or type(limit) is not int or not 1 <= limit <= 10):
            raise AccessDenied('Invalid semantic search request.')
        self.require_opt_in()
        if self.policy.access_mode != 'denylist' or self.policy.content_mode != 'redacted':
            raise ProcessingBlocked('Safe semantic search requires denylist and redacted mode.')
        data = structured(await self.backend.semantic_search(query, min(50, limit * 3)))
        rows = data.get('semanticHits')
        if (type(rows) is not list or len(rows) > 250 or type(data.get('hits')) is not dict
                or len(data['hits']) > 50):
            raise ProcessingBlocked('Invalid semantic response.')
        candidates = []
        total_chars = 0
        for row in rows:
            if type(row) is not dict:
                raise ProcessingBlocked('Invalid semantic hit.')
            identity = canonical_note_id(row.get('noteGuid'))
            try:
                self.policy.authorize(identity)
            except AccessDenied:
                continue  # Do not inspect a blocked row's score or text.
            score = row.get('score')
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ProcessingBlocked('Invalid semantic score.')
            chunk = bounded_text(row.get('chunkContent'), 100_000)
            total_chars += len(chunk)
            if total_chars > 1_000_000:
                raise ProcessingBlocked('Semantic response exceeds local bounds.')
            candidates.append((score, identity, chunk))
        results, seen = [], set()
        for score, identity, chunk in sorted(candidates, key=lambda item: item[0], reverse=True):
            if identity in seen:
                continue
            seen.add(identity)
            safe = await self.text_field(chunk, 100_000, markup=True)
            # Slice only after masking the entire passage.
            end = min(800, len(safe))
            marker = safe.rfind('[', 0, end)
            if end < len(safe) and marker >= 0 and ']' not in safe[marker:end]:
                close = safe.find(']', end)
                if close >= 0 and close - marker < 100:
                    end = marker
            results.append({'id': identity, 'score': score, 'snippet': safe[:end],
                            'snippet_truncated': end < len(safe), 'content_mode': 'redacted'})
            if len(results) == limit:
                break
        return results


# Compatibility for the original access-control regression tests.
UnredactedNoteService = NoteService


class ConfiguredService:
    """Reload local policy per call; fail if it changes while a call is in flight."""

    def __init__(self, path: Path, backend_factory=OfficialBackend, redactor_factory=None):
        self.path = path
        self.backend_factory = backend_factory
        self.redactor_factory = redactor_factory

    async def _run(self, operation, **arguments):
        policy = SingleNotePolicy.from_file(self.path)
        if operation == "read_safe_note":
            policy.authorize(arguments["note_id"])
        redactor = None
        if policy.content_mode == "redacted":
            from .redaction import get_redactor
            redactor = (await asyncio.to_thread(self.redactor_factory) if self.redactor_factory
                        else await asyncio.to_thread(get_redactor, policy.mask_dates, policy.languages))
        service = NoteService(policy, self.backend_factory(policy), redactor)
        result = await getattr(service, operation)(**arguments)
        if SingleNotePolicy.from_file(self.path) != policy:
            raise ProcessingBlocked("Local policy changed during processing.")
        return result

    async def read_safe_note(self, note_id, **selection):
        return await self._run("read_safe_note", note_id=note_id, **selection)

    async def search_safe_notes(self, query="", sort="updated_desc", limit=5, notebook_id=None):
        return await self._run("search_safe_notes", query=query, sort=sort, limit=limit,
                               notebook_id=notebook_id)

    async def list_safe_notebooks(self, query="", limit=20):
        return await self._run("list_safe_notebooks", query=query, limit=limit)

    async def semantic_search_safe_notes(self, query, limit=3):
        return await self._run('semantic_search_safe_notes', query=query, limit=limit)
