import hashlib
import secrets

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

password_hasher = PasswordHash.recommended()
_dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, stored_hash: str | None) -> tuple[bool, str | None]:
    if stored_hash is None or stored_hash.startswith("!"):
        password_hasher.verify(password, _dummy_hash)
        return False, None
    try:
        return password_hasher.verify_and_update(password, stored_hash)
    except UnknownHashError:
        password_hasher.verify(password, _dummy_hash)
        return False, None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
