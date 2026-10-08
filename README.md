# EverWrapMCP

**Block private Evernote notes. Mask sensitive details. Keep the useful context.**

Some notes should never reach your AI assistant. Others are useful, but contain
names, contact details or secrets. EverWrapMCP adds a private block list and local
masking between Evernote and your assistant. Your original notes stay unchanged.

**Preview release · macOS + experimental Windows · read-only · Languages (English, Turkish, Spanish, French, German) · MIT code**

![EverWrapMCP: blocked notes stay out of AI responses, while allowed notes have detected sensitive details masked.](docs/assets/everwrap-social-alternative-a.png)

It connects to [Evernote’s official MCP server](https://dev.evernote.com/mcp).
MCP is the protocol that lets an AI assistant use external tools and data.

**Windows:** Native credential storage and a PowerShell installer are available
in this experimental implementation. [Windows setup and verification limits](docs/WINDOWS.md).

## See the difference

Fictional example, checked with the local redactor:

| Original note | What your assistant receives |
| --- | --- |
| Talk to Alex Smith about the community garden. | Talk to [PERSON] about the community garden. |
| Email alex@example.org. | Email [REDACTED]. |
| password=synthetic-demo-only-927 | [SECRET] |
| Bring seeds and a notebook. | Bring seeds and a notebook. |

Illustrative conversation after connecting your test note:

> **You:** What should I bring to the garden?
>
> **Assistant:** Seeds and a notebook.

For a blocked note, the wrapper denies access and returns no content.
[More fictional prompts, answers and masking examples](docs/ONBOARDING.md#example-prompts-and-answers).

## Get started

You need macOS or Windows, an Evernote account with MCP access, and a compatible client.
**Codex is not required.** Codex, Claude Code and Claude Desktop have been tested live on macOS.

| Client | Setup and status |
| --- | --- |
| Codex | [Local setup](docs/INSTALL.md#codex) · live tested |
| Claude Desktop / Claude Code | [Desktop](docs/INSTALL.md#claude-desktop) / [Code](docs/INSTALL.md#claude-code) · live tested on macOS |
| ChatGPT cloud chat | [Private tunnel setup](docs/INSTALL.md#chatgpt) · advanced, not live tested |
| claude.ai web/mobile (your own server) | [Self-hosted remote connector](docs/REMOTE.md) · advanced, live tested (single owner) |

## Follow the installation guide

**[Open the step-by-step installation guide →](docs/INSTALL.md)**

Install the wrapper, choose your private exclusions, sign in to Evernote, and
connect your client.

Or give a coding agent with local terminal access this setup request:

> Help me install https://github.com/cagdarts/everwrapmcp. Follow docs/AGENT_SETUP.md.
> Ask about my client, note languages and notes to block. Preserve existing settings,
> start with a fictional test note and masking enabled, and guide me through
> read-only Evernote sign-in. Verify the connection before claiming it works.

A repository link alone does not connect an ordinary web chat to your notes.
[Understand the client options](docs/CLIENT_COMPATIBILITY.md).

## What setup asks you

1. **Your app and note languages.** Choose Turkish and English, or **different languages — for example French, German, Spanish, or any combination of the five supported languages**. Setup installs and tests the selected masking packs; routing is automatic during use.
   Other languages need additional models, rules and validation. Unsupported or
   uncertain passages are withheld; language detection is not a privacy guarantee.
   [Language packs and coverage](docs/ONBOARDING.md#adding-another-language).
2. **A fictional test note.** Start with access to just that note.
3. **Which notes to block.** Provide internal note links or IDs, not their contents.
4. **Read-only sign-in.** Complete Evernote authorization in your browser.
5. **A read and a denial test.** Broader access is your choice after testing.

On Mac, select a note and look under **Note → Copy internal link** in the menu bar,
or use **Control + Option + Command + C**. Menu placement varies by version.
Titles alone cannot enforce a block; the current list supports up to 16 note IDs.
[How to find IDs and configure exclusions](docs/ONBOARDING.md#choose-the-notes-to-block).

<details>
<summary>Example onboarding conversation</summary>

| Agent asks | Fictional user response |
| --- | --- |
| Which client and note languages do you use? | Codex on Mac; English and Turkish. |
| Which notes must stay out of AI results? | My Private demo note. I’ll supply its internal link. |
| Shall we keep initial access limited to the Garden demo note? | Yes. |

The user completes browser sign-in; the agent reports actual test results.
[Complete walkthrough and dummy note](docs/ONBOARDING.md).

</details>

## Where the trust boundary sits

MCP places the consent and data-handling duties on the **host** — the client that
runs the assistant. The host is responsible for getting your consent before
exposing your data to a server and for not sending it onward without your
agreement. It is also supposed to treat tool descriptions as untrusted unless
they come from a server it trusts.

EverWrapMCP volunteers to be that trusted local server for your Evernote data. It
is the component that decides what leaves your machine, so those duties are
enforced in one place you can read, test and audit. That is the reason the block
list and masking run in a local process you control, and why the wrapper keeps the
policy file, credentials and note IDs on your machine.

It also means the boundary only holds while the assistant connects through the
wrapper. A second, direct Evernote connection in the same client bypasses these
controls entirely. [Client options and limits](docs/CLIENT_COMPATIBILITY.md).

## Know the limits

Masking is **best-effort**: it can miss sensitive details or mask harmless words.
Processed text reaches your AI provider. A separate direct Evernote connection
bypasses the wrapper. Keep note IDs, credentials and your block list private.
Model wheels are third-party downloads and were not assessed by the advisory scan;
use the pinned official sources. [Model warnings and licenses](docs/LANGUAGE_PACKS.md).

### Not covered yet

These gaps are known and tracked. Treat them as open, not as solved.

| Area | Current behavior | Status |
| --- | --- | --- |
| Attachments and embedded files | Attachment data is never fetched or returned. Attachment references are dropped from the flattened text. | No attachment scanning; attached content is neither read nor masked. |
| Images and OCR text | Image bytes are not read. Text that Evernote itself has already placed into the note is handled like any other text. | No OCR or image-text extraction; text visible only inside an image is not seen. |
| Encoded or obfuscated secrets | Deterministic secret and credential recognizers run across the full text window before the language models. | Plain forms are covered best-effort; heavily encoded, split or transformed secrets may still be missed. |
| Indirect note references | Notes are fetched by ID and each note is authorized on its own. A note that mentions or links to another note is not followed. | A reference is not resolved, and it does not grant access. A blocked note stays blocked. |
| Notebook-level rules | Access is enforced by note ID with a block list that always wins. | Notebook-level default-deny rules are not implemented; see the roadmap. |
| Redaction preview | Detected spans are replaced with typed placeholders such as `[PERSON]` and `[SECRET]`. | There is no UI that shows what was removed and why. |
| Guarantees | Failures block the response instead of falling back to raw text. | Detection is probabilistic for names and addresses. No claim of complete PII removal. |

[Roadmap for these items](docs/IMPROVEMENTS.md) · [Full privacy details](docs/REDACTION.md)

This preview release is not a security sandbox or a one-click product. A fresh-Mac
onboarding test and live verification of ChatGPT and Windows clients remain outstanding.

## For contributors

[Documentation index](docs/README.md) · [Development and test results](docs/RESULTS.md)

Internal Python module and MCP registration names remain `everwrap` for
compatibility. Existing installations do not need their folders renamed.

## License

[MIT](LICENSE) for this project’s original code and documentation. Third-party
packages and models retain their own licenses. Independent project; not affiliated
with Evernote.
