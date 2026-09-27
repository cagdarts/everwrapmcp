"""Encrypted headless credential storage; synthetic values only, no models."""

import asyncio
import os

import pytest
from cryptography.fernet import Fernet

from everwrap.connect import EncryptedFileStore, KeychainStore, credential_store
from everwrap.secret_store import EncryptedFile, SecretStoreError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "secrets" / "key"
    path.parent.mkdir()
    path.write_bytes(Fernet.generate_key() + b"\n")
    return path


def test_round_trip_is_encrypted_at_rest(tmp_path, key_file):
    store = EncryptedFile(tmp_path / "state.enc", key_file)
    store.save({"token": "synthetic-secret-value"})
    raw = (tmp_path / "state.enc").read_bytes()
    assert b"synthetic-secret-value" not in raw
    assert store.load() == {"token": "synthetic-secret-value"}
    if os.name != "nt":
        assert (tmp_path / "state.enc").stat().st_mode & 0o777 == 0o600


def test_missing_file_is_empty(tmp_path, key_file):
    assert EncryptedFile(tmp_path / "absent.enc", key_file).load() == {}


@pytest.mark.parametrize("content", [None, b"", b"not-a-fernet-key", b"x" * 300])
def test_missing_or_invalid_key_fails_closed(tmp_path, content):
    key = tmp_path / "key"
    if content is not None:
        key.write_bytes(content)
    with pytest.raises(SecretStoreError):
        EncryptedFile(tmp_path / "state.enc", key)


def test_wrong_key_or_tampering_is_rejected(tmp_path, key_file):
    EncryptedFile(tmp_path / "state.enc", key_file).save({"a": 1})
    other = tmp_path / "other"
    other.write_bytes(Fernet.generate_key())
    with pytest.raises(SecretStoreError):
        EncryptedFile(tmp_path / "state.enc", other).load()
    data = bytearray((tmp_path / "state.enc").read_bytes())
    data[20] ^= 1
    (tmp_path / "state.enc").write_bytes(bytes(data))
    with pytest.raises(SecretStoreError):
        EncryptedFile(tmp_path / "state.enc", key_file).load()


def test_file_store_keeps_read_only_tokens_and_client(tmp_path, key_file):
    store = EncryptedFileStore(tmp_path / "evernote.enc", key_file)
    token = OAuthToken(access_token="synthetic-access", token_type="Bearer",
                       refresh_token="synthetic-refresh", scope="read")
    client = OAuthClientInformationFull(client_id="synthetic-client",
                                        redirect_uris=["http://127.0.0.1:8766/callback"])

    async def run():
        assert await store.get_tokens() is None
        await store.set_tokens(token)
        await store.set_client_info(client)
        reopened = EncryptedFileStore(tmp_path / "evernote.enc", key_file)
        assert (await reopened.get_tokens()).refresh_token == "synthetic-refresh"
        assert (await reopened.get_client_info()).client_id == "synthetic-client"
        with pytest.raises(ValueError):
            await store.set_tokens(token.model_copy(update={"scope": "read write"}))

    asyncio.run(run())
    assert b"synthetic-refresh" not in (tmp_path / "evernote.enc").read_bytes()


def test_store_selection_is_explicit(tmp_path, key_file, monkeypatch):
    monkeypatch.delenv("EVERWRAP_CREDENTIAL_KEY_FILE", raising=False)
    monkeypatch.setattr("everwrap.connect.secure_keyring", lambda: object())
    assert type(credential_store()) is KeychainStore
    monkeypatch.setenv("EVERWRAP_CREDENTIAL_KEY_FILE", str(key_file))
    monkeypatch.delenv("EVERWRAP_DATA_DIR", raising=False)
    with pytest.raises(RuntimeError):
        credential_store()
    monkeypatch.setenv("EVERWRAP_DATA_DIR", str(tmp_path))
    assert type(credential_store()) is EncryptedFileStore
