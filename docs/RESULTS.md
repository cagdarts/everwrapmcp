# Current results — 2026-09-17

The production path uses Lingua EN/TR routing, English spaCy, Turkish BERT and
Presidio rules/anonymization. The current synthetic report is
[redaction-results.json](redaction-results.json): **25/26** checks pass; the
remaining utility failure masks `asyncio`. The live semantic-search repeat
returned substantially more readable Turkish snippets with masking enabled.
This is not a general recall or relevance benchmark.

See [redaction validation](REDACTION.md#language-routing-validation-2026-09-17)
for application tests and [known gaps](IMPROVEMENTS.md) for outstanding work.
The chronology below records earlier stages; its test counts and configurations
are historical, not current installation claims.

# Baseline result — 2026-09-17

**Stock-baseline decision: unsuitable for reliable privacy protection.** The
original results below remain unchanged. A separate best-effort Presidio mode
with additional patterns is now implemented and enabled at the user's request.

Presidio 2.2.364, spaCy 3.8.16, en_core_web_lg 3.8.0, Python 3.12.13.
Exact package resolution is in uv.lock.

Four of ten synthetic checks passed: English person, email, Turkish phone removal,
and unchanged technical English text. Six failed: Turkish person, Turkish birthday,
OpenAI-style test key, password assignment, bearer value, password in connection URL.

The Turkish person example retained the surname Yılmaz. The birthday example
retained 14 Mart. All four synthetic credential values remained visible.
The English model also over-redacted Turkish non-sensitive words. Phone removal
passing does not mean the surrounding Turkish text was preserved correctly.

This is a tiny deliberately challenging corpus, not an estimate of population-wide
accuracy. Secret patterns use synthetic placeholders; tests of realistic formats,
obfuscation, and transformations remain necessary. This evaluates stock Presidio
with English NLP, not the custom recognizers proposed in the handoff and not a
Turkish NLP configuration.

Next experiment: add independent deterministic secret handling and suitable local
NLP for both English and Turkish. Evaluate contextual names, dates, birthdays,
and addresses in each language, using new held-out cases as well as regressions.
The passing English person example does not establish English privacy coverage.

## Integration checks — 2026-09-17

At this earlier stage, the focused suite passed 183 tests covering single-note and denylist
authorization, explicit block-list precedence, OAuth setup safeguards, upstream
argument mapping, response validation, policy reload, and the MCP boundary.
These tests use synthetic fixtures. Real-Presidio tests now validate selected
masking behavior and failure paths, without proving general detection coverage.

Read-only OAuth completed and the official `get_note` input schema was inspected.
The local wrapper was registered with Codex. Calls through its exposed MCP tool
returned a privacy-gate error for the allowed dummy and a policy denial for an
excluded note. A fresh stdio process also verified the configured denial.
Those initial checks fetched no note content.

After explicit opt-in to denylist access and unredacted output, a fresh stdio
server verified a live read of the permitted synthetic dummy, a denial of the
blocked ID, and a keyword search returning a permitted result. The dummy response
structure was inspected locally to implement the parser. Raw content and private
note identifiers were not copied into code or reports. Search rows are filtered
locally before output; upstream search can supply blocked metadata to the local
wrapper, but the wrapper never requests a blocked note body.

## Presidio redaction — 2026-09-17

The new local redactor uses Presidio Analyzer and Anonymizer, the installed
`en_core_web_lg` model, conservative English/Turkish patterns, and credential
recognizers. It normalizes text and masks the union of overlapping detected spans.
ENML becomes plain text; titles and search snippets are also processed. Redacted
search omits date metadata. Processing failures never return raw note text.

The earlier English-only production configuration achieved **24/26** corpus checks
passed. All targeted sensitive values were removed, including the values missed
in the six original baseline cases. Two utility controls fail: the model masks
`asyncio` as a person and a harmless Turkish word as an organization. Some
conservative patterns can remove extra context. These limitations are retained
here as historical limitations. The linked report now reflects the newer configuration.

A fresh stdio server denied the blocked ID and returned the permitted dummy in
redacted plain text with 27 masking placeholders. Only placeholder counts/types
were printed during this check, not live note text or identifiers.

The original stock-baseline test command still reports **6 failed, 4 passed**;
the original stock-baseline report was not overwritten. Turkish statistical NER
has since been added. Broader adversarial coverage, exact-output review, and OS
isolation remain unfinished. Passing these fixtures does not establish that every name, birthday,
address, secret, or contextual identifier is detected.

## Live client checks — 2026-09-28

Checked by the maintainer on macOS with their own Evernote account; no note text,
titles or identifiers are recorded here.

- **Claude Code (local stdio):** registered with `claude mcp add`; keyword search
  returned masked results.
- **Claude Desktop (local stdio):** manual config loaded and a tool call completed.
  External edits to the config are only kept when Claude Desktop is quit first.
- **Self-hosted remote connector (Docker):** claude.ai custom connector completed the
  passphrase OAuth login; keyword and semantic search returned masked results; the
  blocked note was denied before any Evernote call; unauthenticated and forged-token
  requests were refused. Evernote sign-in used the SSH fallback. Browser-based
  re-linking was deployed afterwards and not yet exercised live.
- Still outstanding: ChatGPT tunnel, Windows clients, fresh-Mac onboarding.
