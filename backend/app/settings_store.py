from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


PREFIX = "enc:v1:"


def _fernet(settings: Settings) -> Fernet:
    if not settings.settings_encryption_key:
        raise ValueError("未配置 SETTINGS_ENCRYPTION_KEY，无法安全保存 API Key")
    try:
        return Fernet(settings.settings_encryption_key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError("SETTINGS_ENCRYPTION_KEY 必须是 Fernet 密钥") from exc


def encrypt_secret(value: str, settings: Settings) -> str:
    return PREFIX + _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str, settings: Settings) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _fernet(settings).decrypt(value.removeprefix(PREFIX).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("保存的 API Key 无法解密，请重新配置") from exc


def read_setting(key: str, value: str, settings: Settings) -> str:
    return decrypt_secret(value, settings) if key == "llm_api_key" else value
