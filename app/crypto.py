import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64

from .config import settings


class DecryptionError(RuntimeError):
    """Encrypted application data cannot be authenticated with APP_SECRET."""


def hash_password(password: str) -> str:
    if len(password) > 256:
        raise ValueError("password too long")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    if len(password) > 256:
        return False
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"fuse.v1", iterations=100_000)
    key = base64.urlsafe_b64encode(kdf.derive(settings.app_secret.encode()))
    return Fernet(key)


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise DecryptionError("encrypted credential data could not be decrypted") from exc


VAULT_KDF = {"alg": "scrypt", "n": 16384, "r": 8, "p": 1, "dklen": 32, "v": 1}


def _fernet_from_key(key32: bytes) -> Fernet:
    if not isinstance(key32, (bytes, bytearray)) or len(key32) != 32:
        raise ValueError("vault key must be 32 bytes")
    return Fernet(base64.urlsafe_b64encode(bytes(key32)))


def vault_kdf(password: str, salt: bytes, params: dict | None = None) -> bytes:
    spec = params or VAULT_KDF
    if spec.get("alg") != "scrypt":
        raise DecryptionError("unsupported vault kdf")
    try:
        n = int(spec.get("n") or 16384)
        r = int(spec.get("r") or 8)
        p = int(spec.get("p") or 1)
        dklen = int(spec.get("dklen") or 32)
    except (TypeError, ValueError) as exc:
        raise DecryptionError("invalid vault kdf params") from exc
    # Bound the stored params: a tampered vault_kdf row must not make
    # scrypt allocate unbounded memory (cost ~128*n*r bytes) at unlock.
    if not (2 <= n <= 2**20) or not (1 <= r <= 32) or not (1 <= p <= 8) or dklen != 32:
        raise DecryptionError("invalid vault kdf params")
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=dklen)


def wrap_dek(dek: bytes, kek: bytes) -> str:
    return _fernet_from_key(kek).encrypt(dek).decode()


def unwrap_dek(token: str, kek: bytes) -> bytes:
    try:
        return _fernet_from_key(kek).decrypt(token.encode())
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise DecryptionError("vault dek could not be decrypted") from exc


def encrypt_with_dek(plain: str, dek: bytes) -> str:
    return _fernet_from_key(dek).encrypt(plain.encode()).decode()


def decrypt_with_dek(token: str, dek: bytes) -> str:
    try:
        return _fernet_from_key(dek).decrypt(token.encode()).decode()
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise DecryptionError("host secret could not be decrypted") from exc
