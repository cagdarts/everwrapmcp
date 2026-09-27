# Install EverWrapMCP

EverWrapMCP uses **macOS Keychain** or **Windows Credential Manager** for Evernote credentials.
Windows support is experimental: start with the [PowerShell setup guide](WINDOWS.md).
Native Linux and a one-click extension package are not supported by this build.
Evernote’s remote MCP service does not impose this macOS restriction; the wrapper
does. [Windows requirements and pitfalls](CLIENT_COMPATIBILITY.md#windows-upstream-compatibility-versus-wrapper-support).
The repository is public and MIT-licensed; collaborator access is not required.

Not sure which client path you need? Read [the compatibility guide](CLIENT_COMPATIBILITY.md).
Codex is optional; ordinary ChatGPT cloud chat does not inherit local MCP registration.

## Install the runtime

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/crfgxr/everwrapmcp.git
cd everwrapmcp
uv sync --python 3.12
uv pip install --python .venv/bin/python -r requirements-live.txt
```

The project supports Python 3.12–3.13. Model installation downloads public weights;
redaction runs locally. Keep the checkout in a stable location because client
configurations point to it. Run later commands from this directory unless noted.

## Configure and sign in

For a **new checkout**, copy the example:

```sh
cp single-note.example.json .everwrap-local.json
chmod 600 .everwrap-local.json
```

Create a synthetic note in Evernote. Edit `.everwrap-local.json`, replacing the
placeholder with that note's GUID and setting `content_mode` to `redacted`:

```json
{
  "allowed_note_id": "REPLACE_WITH_YOUR_DUMMY_NOTE_UUID",
  "blocked_note_ids": [],
  "access_mode": "single_note",
  "content_mode": "redacted"
}
```

To obtain the link, see [the Mac menu-bar and shortcut steps](ONBOARDING.md#choose-the-notes-to-block).
Use Copy internal link, not public sharing.

For an internal link shaped like `evernote:///view/ACCOUNT/SHARD/NOTE_GUID/OTHER_GUID`,
use `NOTE_GUID`, the first UUID after the shard. Keep actual IDs and links private.
## Choose language packs

After configuring a valid private policy, run the interactive installer:

```sh
PYTHONPATH=src .venv/bin/python -m everwrap.setup
```

Choose any combination of English (`en`), Turkish (`tr`), Spanish (`es`),
French (`fr`), and German (`de`). Include all languages used in mixed
notes. For agent-driven setup, use the user's answer explicitly:

```sh
PYTHONPATH=src .venv/bin/python -m everwrap.setup --languages en tr
```

For example, use `--languages es` for Spanish alone, or `--languages en fr de`
for English, French and German. Setup installs only the
selected model dependencies, runs fictional masking checks, then saves `languages`
in the ignored local policy. It preserves block lists, access mode and date settings.
It reads no Evernote notes. Existing model files are not uninstalled.

Routing runs locally and withholds passages identified as unsupported or uncertain.
Short or mixed-language text can be misclassified; selection is not a guarantee
that every sensitive detail is found. These five packs have synthetic regression coverage;
additional languages require new models/rules and tests. Local formats vary, and
not every address or national identifier is covered. See [pack details and licenses](LANGUAGE_PACKS.md).
**Download warning:** model wheels are third-party packages; the advisory scan
does not assess their direct-URL releases. Use only the pinned official sources. The block list is enforced before note access.

Existing policies without `languages` retain English-and-Turkish behavior. Rerun
setup to change packs, then restart the MCP connection. Missing selected models
block access. Running plain `uv sync` later removes optional model dependencies;
rerun setup and reinstall `requirements-live.txt` afterward. Developers running the
full regression suite can install all extras with `uv sync --all-extras`,
install live dependencies, and download Turkish weights with `python -m everwrap.language`.

## Authenticate

Authenticate the **wrapper**, not a separate direct Evernote connector:

```sh
PYTHONPATH=src .venv/bin/python -m everwrap.connect
```

Approve read-only access in the browser. If it doesn't open, use the link printed
by the command on the same Mac. Successful setup prints `Connected. No notes were
read.` and the read-tool schema. The callback uses `127.0.0.1:8766`; leave the command
running until it completes. Tokens are stored in the OS credential store, not the JSON file.
[Evernote's official MCP guide](https://dev.evernote.com/mcp) describes account access
and OAuth. There is no Evernote API key to paste into the client configuration.

## Claude Desktop

On macOS the configuration file is
`~/Library/Application Support/Claude/claude_desktop_config.json`
(**Settings → Developer → Edit Config** reveals it).

**Quit Claude Desktop (Command + Q) before editing this file.** While running,
Claude Desktop keeps the file in memory and periodically rewrites it when its own
preferences change, silently discarding external `mcpServers` edits. Saving and then
quitting is not enough: a rewrite can land in between. With Claude Desktop quit, open
the file from Terminal:

```sh
open -e ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Merge the following entry into any existing `mcpServers` object; keep your other
entries. Replace both absolute paths with your checkout location. You can run
`pwd -P` there to find it.

```json
{
  "mcpServers": {
    "everwrap": {
      "command": "/ABSOLUTE/PATH/everwrapmcp/.venv/bin/python",
      "args": ["-m", "everwrap.server"],
      "env": {
        "PYTHONPATH": "/ABSOLUTE/PATH/everwrapmcp/src"
      }
    }
  }
}
```

Save, then reopen Claude Desktop; it reads the file at startup and keeps the entry
from then on. In **Settings → Developer**, `everwrap` should be listed as running.
If it is missing, the file was rewritten before you quit; repeat with Claude Desktop
closed. Check that EverWrapMCP exposes
`read_safe_note`, `search_safe_notes`, and `semantic_search_safe_notes`. This is local developer configuration,
not an extension-directory listing or a remote connector URL.
[Official local-server instructions](https://modelcontextprotocol.io/docs/develop/connect-local-servers).
These setup steps are documentation-checked, but this project's Claude Desktop
integration has not been tested in a live Claude session.

## Claude Code

With Claude Code installed, run from the EverWrapMCP checkout:

```sh
claude mcp add --transport stdio --scope user everwrap --env "PYTHONPATH=$PWD/src" -- "$PWD/.venv/bin/python" -m everwrap.server
```

Open Claude Code and use `/mcp` to inspect the connection. User scope keeps this
registration out of the project's shared MCP config. This follows the
[official stdio setup](https://code.claude.com/docs/en/mcp); live Claude verification
remains outstanding.

## ChatGPT

**Advanced; not yet tested with EverWrapMCP.** ChatGPT can reach local stdio servers
through [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
EverWrapMCP does not provide a public HTTP endpoint.

1. In [Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels),
   create a tunnel associated with your ChatGPT workspace. Obtain the necessary
   tunnel permissions, runtime API key, and `tunnel_id`.
2. Install `tunnel-client` using the download linked in those settings. Supply
   `CONTROL_PLANE_API_KEY` in your shell; don't commit it. On the same Mac where
   EverWrapMCP is authenticated, replace the ID and paths below:

```sh
tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile everwrap \
  --tunnel-id YOUR_TUNNEL_ID \
  --mcp-command '/usr/bin/env "PYTHONPATH=/ABSOLUTE/PATH/everwrapmcp/src" "/ABSOLUTE/PATH/everwrapmcp/.venv/bin/python" -m everwrap.server'

tunnel-client doctor --profile everwrap --explain
tunnel-client run --profile everwrap
```

Keep that process running. The tunnel setup above follows OpenAI's
[stdio quickstart](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels#set-up-tunnel-client).

3. Enable ChatGPT developer mode in **Settings → Security and login** if available.
   In [ChatGPT Plugins](https://chatgpt.com/plugins), use **+**, choose **Tunnel** under
   Connection, and select or enter your tunnel ID. Review the discovered tools,
   then add the connection to a new conversation. Availability depends on your
   account and workspace policy.
   [Official connection steps](https://developers.openai.com/plugins/deploy/connect-chatgpt).

Use the EverWrapMCP process as the tunnel target. Pointing ChatGPT directly at the
Evernote MCP endpoint bypasses this wrapper's block list and redaction.

## Codex

From the checkout, with the Codex CLI installed:

```sh
codex mcp add everwrap --env "PYTHONPATH=$PWD/src" -- "$PWD/.venv/bin/python" -m everwrap.server
```

Restart the MCP connection or Codex. The CLI registration and live stdio calls
have been verified in this project. This is separate from ChatGPT's tunnel setup.

## First test

Ask your client:

> Use EverWrapMCP to read my synthetic test note by its configured GUID. Summarize
> the returned text and tell me whether it contains masking placeholders.

Confirm that the returned `content_mode` is `redacted` and the note body is plain
text. Test a blocked ID too: it should return a static policy denial. No request
should fetch the blocked note's body. Search may receive blocked metadata locally;
EverWrapMCP discards that row before returning anything to the assistant.

## Allow more notes

After testing, edit only the **private local file** if you want account-wide access
with exclusions:

```json
{
  "access_mode": "denylist",
  "content_mode": "redacted",
  "blocked_note_ids": ["REPLACE_WITH_A_NOTE_UUID_TO_BLOCK"]
}
```

Replace the placeholder with your own blocked UUID. Use `[]` only if you intend
no explicit exclusions. The list supports up to 16 UUIDs. An explicit block always
wins. Never paste actual IDs, account links, tokens, or note text into commits or
issues. `.everwrap-local.json` must stay both ignored and untracked.

The server reloads policy on each call; code changes require a server restart.
`blocked` disables content access. `unredacted` intentionally skips masking and
returns original permitted text. Client tool arguments cannot change these modes.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Python/module not found | Use absolute paths; reinstall `requirements-live.txt` after any `uv sync`. |
| Configuration error | Replace placeholder UUIDs and validate local JSON. |
| Content blocked | Check `content_mode`, runtime/model installation, and the local OAuth setup. There is no raw fallback. |
| Sign-in fails | Retry `everwrap.connect`; check network access and localhost port 8766. |
| Old tool descriptions | Restart the MCP process; refresh the ChatGPT connection if applicable. |
| Tunnel not visible | Check workspace association and tunnel permissions in the official tunnel guide. |

Keep direct Evernote connectors removed when the wrapper should be the only note
interface. This does not prevent bypass by an agent that can modify local code or
access credentials. Masking is best-effort; processed text is sent to your AI
provider. Read [redaction limits](REDACTION.md) before using personal notes.

Long notes use [local section selection and masked pages](LARGE_NOTES.md).
The default read returns up to 4,000 body characters; ask for the latest recognized
dated entry or a keyword-matching section to avoid retrieving the whole journal.
Restart the MCP connection after updating so the client sees the new read options.
The updated server also exposes `semantic_search_safe_notes` for meaning-based
retrieval in redacted denylist mode. [Semantic search guide](SEMANTIC_SEARCH.md).

## Run the checks

```sh
.venv/bin/python -m pytest --ignore=tests/test_release_gate.py -q
PYTHONPATH=src .venv/bin/python -m experiments.redaction
```

The historical stock-baseline command is separate and intentionally retains its
six known failures:

```sh
.venv/bin/python -m pytest tests/test_release_gate.py -q
```

See [results](RESULTS.md) for the distinction between access-control tests,
redaction regressions, and the original baseline. Client instructions were checked
against official documentation on 2026-09-17; UI labels and account access can change.
