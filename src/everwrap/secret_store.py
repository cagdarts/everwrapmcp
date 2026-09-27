"""Encrypted JSON files for headless (remote) deployments.

The Fernet key lives outside the data directory (for example a Docker secret), so
a copy of the data volume alone reveals nothing. There is no plaintext fallback:
a missing, oversized or malformed key, or a file that fails authentication,
raises instead of returning or writing unprotected data.
"""

import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .private_files import restrict_file

MAX_FILE = 1_000_000


class SecretStoreError(Exception):
    """Static failure; never carries key material, paths or plaintext."""


def read_key(key_file: Path) -> bytes:
    try:
        with Path(key_file).open("rb") as stream:
            raw = stream.read(257)
        key = raw.strip()
        if len(raw) > 256 or not key:
            raise ValueError
        Fernet(key)  # Validates length and encoding.
        return key
    except (OSError, ValueError):
        raise SecretStoreError("Credential encryption key is unavailable or invalid.") from None


class EncryptedFile:
    def __init__(self, path: Path, key_file: Path):
        self.path = Path(path)
        self._fernet = Fernet(read_key(key_file))

    def load(self) -> dict:
        try:
            with self.path.open("rb") as stream:
                token = stream.read(MAX_FILE + 1)
        except FileNotFoundError:
            return {}
        except OSError:
            raise SecretStoreError("Encrypted store is unreadable.") from None
        try:
            if len(token) > MAX_FILE:
                raise ValueError
            data = json.loads(self._fernet.decrypt(token))
            if type(data) is not dict:
                raise ValueError
            return data
        except (InvalidToken, ValueError):
            raise SecretStoreError("Encrypted store failed authentication.") from None

    def save(self, data: dict):
        token = self._fernet.encrypt(json.dumps(data, separators=(",", ":")).encode())
        if len(token) > MAX_FILE:
            raise SecretStoreError("Encrypted store exceeds supported size.")
        fd, name = tempfile.mkstemp(prefix=".everwrap-store-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                restrict_file(Path(name))
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
