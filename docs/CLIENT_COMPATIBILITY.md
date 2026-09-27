# ChatGPT, Claude and Codex: which setup works?

Researched against official documentation on 2026-09-17. Platform capabilities
below are distinct from integrations actually tested with EverWrapMCP.

**Codex is not required. A repository link alone is not an installed connection.**
An agent with local terminal access can assist with installation; ordinary chat
can explain the steps but does not automatically run a persistent service on the
user's computer. EverWrapMCP supports macOS Keychain and experimental Windows Credential Manager storage.

| User's client | Connection path | EverWrapMCP status |
| --- | --- | --- |
| Windows clients | Experimental local stdio runtime | Native credential backend and PowerShell setup implemented; live verification pending |
| Codex local on Mac | Local stdio MCP registration | Live tested |
| Claude Desktop chat on Mac | Local MCP configuration; a desktop extension could simplify installation later | Manual setup documented and live tested (edit the config only while Desktop is quit); no EverWrapMCP extension bundle yet |
| Claude Code on Mac | Local stdio MCP registration | Live tested |
| ChatGPT cloud chat | Private Secure MCP Tunnel to the local wrapper, or an authenticated public HTTPS MCP service | Tunnel setup documented; not live tested; no hosted EverWrapMCP service |
| Claude web/mobile or cloud remote connector | Authenticated, internet-reachable remote MCP endpoint | Self-hosted single-owner Docker deployment ([REMOTE.md](REMOTE.md)) live tested as a claude.ai custom connector; browser-based Evernote re-linking deployed but not yet exercised live; no hosted service |

## Windows: upstream compatibility versus wrapper support

Evernote documents a remote Streamable HTTP service with OAuth that can be used
by any compliant MCP client. Its guide does not impose a macOS requirement or
provide a Windows-specific EverWrapMCP installation procedure.
[Evernote client requirements](https://dev.evernote.com/mcp/clients/any) ·
[Evernote tested clients](https://dev.evernote.com/mcp/supported-clients).

**Windows support is experimental.** The wrapper selects Windows Credential
Manager explicitly, protects private policy files with Windows ACLs, and provides
a PowerShell installer. macOS retains its explicit Keychain backend. Linux/WSL
remain unsupported; there is no automatic or plaintext credential fallback.

See [Windows setup](WINDOWS.md) for installation, current verification limits,
synthetic checks, sign-in and removal. Full live read/masking, OAuth refresh and
client-restart verification remain required before a stable Windows release.
A direct Evernote connection bypasses this wrapper's block list and masking.

## ChatGPT: additional connection setup

OpenAI documents Secure MCP Tunnel for reaching a private stdio/HTTP server. A
local tunnel client must remain running; setup requires a tunnel identity, runtime
API key, Platform permissions, workspace association and appropriate ChatGPT
access. The wrapper can still perform masking locally before returning results.
This is a private connection route, not public plugin distribution. Public plugin
submission requires an appropriate public HTTPS endpoint.
[Official tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

ChatGPT developer-mode connection setup accepts a public MCP endpoint or a tunnel,
then discovers the tools. Availability depends on account/workspace policy. A
GitHub source-code URL is neither of those endpoints. Do not infer that Codex's
working local configuration makes a cloud ChatGPT chat connected.
[Official connection instructions](https://developers.openai.com/plugins/deploy/connect-chatgpt).

## Claude: desktop is different from web

Claude Desktop supports local MCP servers, including installable desktop
extensions. That fits EverWrapMCP's local architecture, but we have not produced an
EverWrapMCP `.mcpb` bundle. The documented manual configuration has been verified live on macOS.
[Official local-server guide](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop).

Claude remote connectors originate from Anthropic's cloud, even when configured
in its desktop app. They need a reachable remote endpoint. Local desktop JSON
configuration is a separate mechanism, not a way to enable claude.ai or mobile.
[Official remote-connector guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

## Recommended next product steps

1. Verify Claude Desktop on a clean Mac using the existing local architecture.
2. Verify ChatGPT's private tunnel end to end with a synthetic note, blocked ID,
   masking, reconnect and account eligibility. Document actual user effort.
3. If those work, package a local installer/desktop extension and improve the
   setup prompts. The user still completes Evernote authorization.
4. Consider a hosted service only as a separate design decision. Hosting would
   move raw-note processing and credential handling to that host, requiring user
   isolation, authentication, storage/retention controls and operating costs.
   Current macOS Keychain code is not a multi-user cloud deployment.

For sharing today, describe EverWrapMCP as a local MCP wrapper in preview release, live
verified with Codex, Claude Code and Claude Desktop on macOS, plus an optional self-hosted
claude.ai connector; ChatGPT and Windows setup guides await validation. Do not
advertise “paste into ChatGPT and it works.” Researching these routes has not
installed a tunnel, exposed a server or changed repository visibility.
