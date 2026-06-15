"""Password hashing and session-cookie signing for FinBrief."""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

from finbrief.config import SECRET_KEY

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_signer = URLSafeTimedSerializer(SECRET_KEY)
_SALT = "finbrief-session"


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def make_session(user_id: int) -> str:
    return _signer.dumps(user_id, salt=_SALT)


def read_session(token: str, max_age: int = 86400 * 30) -> int | None:
    try:
        value = _signer.loads(token, salt=_SALT, max_age=max_age)
        return int(value)
    except (SignatureExpired, BadSignature, Exception):
        return None
