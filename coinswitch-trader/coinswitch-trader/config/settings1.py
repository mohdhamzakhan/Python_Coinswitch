"""
Application configuration.
COINSWITCH_API_SECRET holds the hex-encoded Ed25519 private key.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import secrets
import base64
from cryptography.fernet import Fernet


def _generate_fernet_key() -> str:
    """Generate a stable Fernet key, encoded as a URL-safe base64 string."""
    return Fernet.generate_key().decode()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name:   str  = "CoinSwitch Trader"
    app_env:    str  = "production"
    secret_key: str  = Field(default_factory=lambda: secrets.token_hex(32))
    debug:      bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./trading.db"

    # CoinSwitch API — base URL is always https://coinswitch.co per official docs
    coinswitch_base_url: str = "https://coinswitch.co"
    coinswitch_ws_url:   str = "wss://hft.coinswitch.co"
    coinswitch_api_key:  str = "9f7e37bc68a33d5addefc7ceb957cd5336aef9b7b3404c00f4a0af1bcc49c58d"
    # Hex-encoded Ed25519 private key from the CoinSwitch portal
    coinswitch_api_secret: str = "aa18ca959d1a13d7635af410ce76ce09a67952cb1f566b8ed50f5b6c36363fbb"

    # Fernet encryption key for secrets stored in DB.
    # MUST be a URL-safe base64-encoded 32-byte key (Fernet.generate_key() format).
    # If blank, a fresh key is generated — but encrypt/decrypt must use the SAME instance.
    encryption_key: str = Field(default_factory=_generate_fernet_key)

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id:   str = ""

    # Email
    smtp_host:     str = "smtp.gmail.com"
    smtp_port:     int = 587
    smtp_user:     str = ""
    smtp_password: str = ""
    alert_email:   str = ""

    # Trading defaults
    default_leverage:           int   = 1
    max_daily_loss_percent:     float = 5.0
    max_position_size_percent:  float = 10.0
    max_open_positions:         int   = 5

    # Safety: always start in simulation mode
    simulation_mode: bool = True

    def get_fernet(self) -> Fernet:
        """
        Return a Fernet instance that is stable for the lifetime of this
        Settings object, so encrypt() and decrypt() always use the same key.

        If ENCRYPTION_KEY is set in .env: uses that key (base-64 Fernet key).
        If not set (e.g. during testing): generates one key on first call and
        caches it via object.__setattr__ (safe even on frozen Pydantic models).
        """
        if not hasattr(self, "_fernet_instance"):
            if self.encryption_key:
                key = self.encryption_key.encode()
            else:
                key = Fernet.generate_key()
            object.__setattr__(self, "_fernet_instance", Fernet(key))
        return self._fernet_instance  # type: ignore[attr-defined]

    def encrypt_secret(self, value: str) -> str:
        return self.get_fernet().encrypt(value.encode()).decode()

    def decrypt_secret(self, encrypted: str) -> str:
        return self.get_fernet().decrypt(encrypted.encode()).decode()


@lru_cache()
def get_settings() -> Settings:
    return Settings()
