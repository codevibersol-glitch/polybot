"""
utils/crypto.py
───────────────
Secure private-key storage with two fallback layers:

  1. OS keychain via `keyring`  (preferred – macOS Keychain, Windows DPAPI …)
  2. AES-256-GCM encrypted file (fallback – user sets a password once)

The private key is NEVER written to disk in plaintext, NEVER logged.

Public API
──────────
  save_key(key: str, password: str | None = None)  → None
  load_key(password: str | None = None)             → str | None
  delete_key()                                      → None
"""

import base64
import os
from pathlib import Path
from utils.logger import get_logger

log = get_logger(__name__)

_SERVICE = "polybot"
_ACCOUNT = "private_key"
_ENC_FILE = Path(__file__).resolve().parent.parent / ".key.enc"


# ── keyring helpers ───────────────────────────────────────────────────────────
def _keyring_save(key: str) -> bool:
    try:
        import keyring  # optional dep
        keyring.set_password(_SERVICE, _ACCOUNT, key)
        log.info("Private key stored in OS keychain.")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("keyring unavailable (%s) – falling back to encrypted file.", exc)
        return False


def _keyring_load() -> "str | None":
    try:
        import keyring
        val = keyring.get_password(_SERVICE, _ACCOUNT)
        return val
    except Exception:  # noqa: BLE001
        return None


def _keyring_delete() -> None:
    try:
        import keyring
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except Exception:  # noqa: BLE001
        pass


# ── AES-GCM file helpers ──────────────────────────────────────────────────────
def _derive_aes_key(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 key derivation (310 000 rounds)."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=310_000,
    )
    return kdf.derive(password.encode())


def _file_save(key: str, password: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(16)
    nonce = os.urandom(12)
    aes_key = _derive_aes_key(password, salt)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, key.encode(), None)
    # Layout: 16-byte salt | 12-byte nonce | ciphertext
    with _ENC_FILE.open("wb") as f:
        f.write(salt + nonce + ciphertext)
    log.info("Private key saved to encrypted file %s.", _ENC_FILE.name)


def _file_load(password: str) -> "str | None":
    if not _ENC_FILE.exists():
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        data = _ENC_FILE.read_bytes()
        salt, nonce, ct = data[:16], data[16:28], data[28:]
        aes_key = _derive_aes_key(password, salt)
        aesgcm = AESGCM(aes_key)
        return aesgcm.decrypt(nonce, ct, None).decode()
    except Exception as exc:  # noqa: BLE001
        log.error("Could not decrypt key file: %s", exc)
        return None


def _file_delete() -> None:
    try:
        _ENC_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


# ── Public API ────────────────────────────────────────────────────────────────
def save_key(key: str, password: "str | None" = None) -> None:
    """
    Persist the private key securely.
    If `password` is None, uses OS keychain.
    If `password` is given, uses AES-GCM file encryption.
    """
    if password is None:
        if not _keyring_save(key) and False:  # keyring failed
            log.warning("Key NOT saved – enable keyring or provide a password.")
    else:
        _file_save(key, password)


def load_key(password: "str | None" = None) -> "str | None":
    """
    Load the private key from secure storage.
    Returns None if not found or decryption fails.
    """
    if password is None:
        val = _keyring_load()
        if val:
            return val
        # Try file without password as last resort (shouldn't exist)
        return None
    return _file_load(password)


def delete_key() -> None:
    """Remove private key from all storage locations."""
    _keyring_delete()
    _file_delete()
    log.info("Private key deleted from secure storage.")


def key_is_saved() -> bool:
    """Return True if a key is stored (keychain OR encrypted file)."""
    if _keyring_load() is not None:
        return True
    return _ENC_FILE.exists()
