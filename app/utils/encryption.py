from cryptography.fernet import Fernet

from app.configuration.encryption import ENCRYPTION_KEY

from sqlalchemy import types

_fernet = Fernet(ENCRYPTION_KEY.encode())


class EncryptedType(types.TypeDecorator[str]):
    """SQLAlchemy column type that transparently encrypts/decrypts string values using AES-256 (Fernet)."""

    impl = types.Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return _fernet.encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return _fernet.decrypt(value.encode()).decode()
