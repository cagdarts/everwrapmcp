"""Remote (Streamable HTTP) EverWrapMCP for a single owner, e.g. a claude.ai connector.

Same tools, policy and masking as the stdio server; adds a passphrase-gated OAuth
authorization server. Configured only through environment variables and secret
files so the public image and repository contain no private values:

  EVERWRAP_PUBLIC_URL             https://host (no path); issuer and resource base
  EVERWRAP_POLICY                 policy JSON (read-only mount)
  EVERWRAP_DATA_DIR               encrypted state directory
  EVERWRAP_CREDENTIAL_KEY_FILE    Fernet key, outside the data directory
  EVERWRAP_PASSPHRASE_HASH_FILE   scrypt hash from `hash-passphrase`
  EVERWRAP_ALLOWED_REDIRECTS      optional comma-separated OAuth redirect URIs

Commands: serve | hash-passphrase | generate-key
"""

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from .live import ConfiguredService
from .policy import SingleNotePolicy
from .remote_auth import (DEFAULT_REDIRECTS, SCOPE, SingleUserAuthProvider,
                          hash_passphrase, login_routes)
from .server import build_server


def public_url(value: str) -> str:
    url = urlsplit(value or "")
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("EVERWRAP_PUBLIC_URL must be an https origin without a path.")
    return f"https://{url.netloc}"


def build_app(*, base_url: str, service, provider: SingleUserAuthProvider):
    host = urlsplit(base_url).netloc
    server = build_server(service)

    async def health(request):
        return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})

    return server.streamable_http_app(
        streamable_http_path="/mcp",
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=[host],
            allowed_origins=[base_url, "https://claude.ai", "https://claude.com"]),
        auth=AuthSettings(
            issuer_url=base_url, resource_server_url=f"{base_url}/mcp",
            validate_token_resource=True, required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True)),
        token_verifier=ProviderTokenVerifier(provider),
        auth_server_provider=provider,
        custom_starlette_routes=login_routes(provider) + [Route("/healthz", health)],
    )


def _required_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required.")
    return Path(value)


def create_from_environment():
    from .connect import credential_store
    from .secret_store import EncryptedFile

    base_url = public_url(os.environ.get("EVERWRAP_PUBLIC_URL", ""))
    policy = _required_path("EVERWRAP_POLICY")
    data_dir = _required_path("EVERWRAP_DATA_DIR")
    key_file = _required_path("EVERWRAP_CREDENTIAL_KEY_FILE")
    passphrase_hash = _required_path("EVERWRAP_PASSPHRASE_HASH_FILE").read_text()
    redirects = [uri.strip() for uri in
                 os.environ.get("EVERWRAP_ALLOWED_REDIRECTS", "").split(",") if uri.strip()]
    credential_store()  # Fail closed now if the Evernote credential store is misconfigured.
    provider = SingleUserAuthProvider(
        issuer_url=base_url, resource_url=f"{base_url}/mcp", passphrase_hash=passphrase_hash,
        state_file=EncryptedFile(data_dir / "connector-state.enc", key_file),
        allowed_redirects=redirects or DEFAULT_REDIRECTS)
    return base_url, policy, ConfiguredService(policy), provider


def warm_up(policy_path: Path):
    """Load the configured masking models once, before accepting connections."""
    policy = SingleNotePolicy.from_file(policy_path)
    if policy.content_mode == "redacted":
        from .redaction import get_redactor
        get_redactor(policy.mask_dates, policy.languages).sanitize_text("Warm-up text.")


def serve():
    import uvicorn
    base_url, policy, service, provider = create_from_environment()
    warm_up(policy)
    app = build_app(base_url=base_url, service=service, provider=provider)
    print("EverWrapMCP remote server ready.", file=sys.stderr, flush=True)
    uvicorn.run(app, host=os.environ.get("EVERWRAP_HOST", "0.0.0.0"),
                port=int(os.environ.get("EVERWRAP_PORT", "8000")), workers=1,
                access_log=False, log_level="critical", server_header=False,
                proxy_headers=False)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m everwrap.remote")
    parser.add_argument("command", choices=["serve", "hash-passphrase", "generate-key"])
    parser.add_argument("--output", type=Path,
                        help="hash-passphrase: write the hash to this file (0600) instead of stdout")
    args = parser.parse_args(argv)
    command = args.command
    if command == "generate-key":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return 0
    if command == "hash-passphrase":
        first = getpass.getpass("New connector passphrase: ")
        if getpass.getpass("Repeat passphrase: ") != first:
            print("Passphrases do not match.", file=sys.stderr)
            return 1
        try:
            hashed = hash_passphrase(first)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        if args.output is None:
            print(hashed)
            return 0
        from .private_files import restrict_file
        temporary = args.output.with_name(args.output.name + ".tmp")
        with temporary.open("w") as stream:
            restrict_file(temporary)
            stream.write(hashed + "\n")
        os.replace(temporary, args.output)
        print(f"Passphrase hash written to {args.output}.", file=sys.stderr)
        return 0
    # Libraries may log URLs or request details; emit only static messages.
    logging.disable(logging.CRITICAL)
    try:
        serve()
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # The class name carries no request data, paths or secrets; messages might.
        print("EverWrapMCP remote server could not start safely "
              f"({type(error).__name__}). Check configuration, secrets and policy.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
