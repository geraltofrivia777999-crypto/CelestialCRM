import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from passlib.context import CryptContext

from app.core.config import settings

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_context.verify(password, password_hash)


def create_session_token() -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(days=settings.session_days)
    return token, token_hash, expires_at


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _fernet() -> Fernet:
    if settings.field_encryption_key:
        key = settings.field_encryption_key.encode()
    else:
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()

