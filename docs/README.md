# Documentation

Start with the [project overview](../README.md).

## Using EverWrapMCP

- [Install and connect](INSTALL.md)
- [Onboarding, dummy examples and block-list setup](ONBOARDING.md)
- [ChatGPT, Claude and Codex compatibility](CLIENT_COMPATIBILITY.md)
- [Instructions for a setup agent](AGENT_SETUP.md)
- [Language packs, coverage and model licenses](LANGUAGE_PACKS.md)
- [Privacy and masking limits](REDACTION.md)

## Self-hosting

- [Remote connector on your own server (Docker)](REMOTE.md)

## Retrieval reference

- [Choosing a retrieval tool](RETRIEVAL.md)
- [Semantic search](SEMANTIC_SEARCH.md)
- [Large notes and pagination](LARGE_NOTES.md)

## Development and evidence

These are engineering records, not required reading for installation.

- [Current and historical results](RESULTS.md)
- [Design decisions](DECISIONS.md)
- [Access-policy tests](SINGLE_NOTE_TEST.md)
- [Improvement backlog](IMPROVEMENTS.md)
- [Dependency security review](DEPENDENCY_SECURITY.md)
- Reports: [masking](redaction-results.json), [large-note selection](section-results.json),
  [dependency audit](dependency-audit.json), [historical baseline](baseline-results.json)

Source, tests and experiments remain versioned so contributors can reproduce
results. Local credentials, private policy, virtual environments and caches are
excluded through the repository's ignore rules.
