"""Password hashing and session-cookie signing for FinBrief."""

from __future__ import annotations

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from finbrief.config import SECRET_KEY

_signer = URLSafeTimedSerializer(SECRET_KEY)
_SALT = "finbrief-session"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def make_session(user_id: int) -> str:
    return _signer.dumps(user_id, salt=_SALT)


def read_session(token: str, max_age: int = 86400 * 30) -> int | None:
    try:
        return int(_signer.loads(token, salt=_SALT, max_age=max_age))
    except (SignatureExpired, BadSignature, Exception):
        return None
