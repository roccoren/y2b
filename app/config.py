import os
from pydantic import Field, validator, field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List, Optional

def _get_int(name: str, default: int) -> int:
    """
    Robust integer environment parser: strips inline comments and whitespace.
    Raises ValueError with clear message if conversion fails.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    # Strip any inline comment after a '#'
    raw = raw.split("#", 1)[0].strip()
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}")


class Settings(BaseSettings):
    """
    Central application configuration loaded from environment variables (.env supported).
    Includes optional Azure Blob Storage integration flags.
    """

    # Download / processing
    YT_DLP_OUTPUT_DIR: str = Field(default=os.getenv("YT_DLP_OUTPUT_DIR", "/app/downloads"))
    DEFAULT_AUDIO_FORMAT: str = Field(default=os.getenv("DEFAULT_AUDIO_FORMAT", "mp3"))
    DEFAULT_AUDIO_QUALITY: str = Field(default=os.getenv("DEFAULT_AUDIO_QUALITY", "best"))  # best|high|medium|low
    ALLOWED_FORMATS: str = Field(default=os.getenv("ALLOWED_FORMATS", "mp3,m4a,ogg,wav"))
    MAX_CONCURRENT_DOWNLOADS: int = Field(default=_get_int("MAX_CONCURRENT_DOWNLOADS", 3))
    QUALITY_BITRATES: str = Field(default=os.getenv("QUALITY_BITRATES", "best=0,high=192,medium=128,low=64"))

    # Cleanup / retention
    MAX_FILE_AGE_HOURS: int = Field(default=_get_int("MAX_FILE_AGE_HOURS", 6))
    CLEANUP_INTERVAL_SECONDS: int = Field(default=_get_int("CLEANUP_INTERVAL_SECONDS", 600))
    MIN_FREE_DISK_MB: int = Field(default=_get_int("MIN_FREE_DISK_MB", 100))

    # Logging
    LOG_LEVEL: str = Field(default=os.getenv("LOG_LEVEL", "INFO"))

    # Domain restrictions (comma-separated hostnames; if empty => allow all)
    ALLOWED_DOMAINS: str = Field(default=os.getenv("ALLOWED_DOMAINS", "youtube.com,youtu.be"))

    # Azure Blob Storage (optional)
    # Two credential modes supported:
    # 1) Connection string (full access) via AZURE_STORAGE_CONNECTION_STRING
    # 2) Pre-generated SAS token (no account key) via AZURE_BLOB_ACCOUNT_URL + AZURE_SAS_TOKEN
    AZURE_UPLOAD_ENABLED: bool = Field(default=os.getenv("AZURE_UPLOAD_ENABLED", "false").lower() in ("1", "true", "yes", "on"))
    AZURE_STORAGE_CONNECTION_STRING: Optional[str] = Field(default=os.getenv("AZURE_STORAGE_CONNECTION_STRING"))
    AZURE_BLOB_ACCOUNT_URL: Optional[str] = Field(default=os.getenv("AZURE_BLOB_ACCOUNT_URL"))  # e.g. https://mystorage.blob.core.windows.net
    AZURE_SAS_TOKEN: Optional[str] = Field(default=os.getenv("AZURE_SAS_TOKEN"))  # Either with or without leading '?'
    AZURE_BLOB_CONTAINER_NAME: str = Field(default=os.getenv("AZURE_BLOB_CONTAINER_NAME", "audio-files"))
    AZURE_BLOB_PREFIX: str = Field(default=os.getenv("AZURE_BLOB_PREFIX", ""))  # Optional path prefix in container
    AZURE_GENERATE_SAS: bool = Field(default=os.getenv("AZURE_GENERATE_SAS", "false").lower() in ("1", "true", "yes", "on"))
    AZURE_SAS_EXPIRY_SECONDS: int = Field(default=int(os.getenv("AZURE_SAS_EXPIRY_SECONDS", "3600")))
    AZURE_SAS_PERMISSIONS: str = Field(default=os.getenv("AZURE_SAS_PERMISSIONS", "r"))  # Typical: r (read)
    # When true, remove the local downloaded file immediately after a successful Azure upload
    AZURE_DELETE_LOCAL_AFTER_UPLOAD: bool = Field(default=os.getenv("AZURE_DELETE_LOCAL_AFTER_UPLOAD", "false").lower() in ("1", "true", "yes", "on"))
 
    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def allowed_formats_list(self) -> List[str]:
        return [f.strip().lower() for f in self.ALLOWED_FORMATS.split(",") if f.strip()]

    @property
    def allowed_domains_list(self) -> List[str]:
        return [d.strip().lower() for d in self.ALLOWED_DOMAINS.split(",") if d.strip()]

    @property
    def quality_bitrate_mapping(self) -> dict:
        mapping = {}
        for part in self.QUALITY_BITRATES.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    mapping[k.strip()] = int(v.strip())
                except ValueError:
                    continue
        return mapping

    @property
    def azure_uses_connection_string(self) -> bool:
        return bool(self.AZURE_STORAGE_CONNECTION_STRING)

    @property
    def azure_sas_token_clean(self) -> Optional[str]:
        if not self.AZURE_SAS_TOKEN:
            return None
        return self.AZURE_SAS_TOKEN.lstrip("?").strip()

    @property
    def azure_uses_sas(self) -> bool:
        return bool(self.AZURE_BLOB_ACCOUNT_URL and self.azure_sas_token_clean)

    @property
    def azure_is_configured(self) -> bool:
        """
        True if feature flag enabled AND (connection string OR (account URL + SAS token)).
        """
        if not self.AZURE_UPLOAD_ENABLED:
            return False
        return self.azure_uses_connection_string or self.azure_uses_sas

    @validator("DEFAULT_AUDIO_FORMAT")
    def validate_default_format(cls, v):
        return v.lower()

    @validator("AZURE_SAS_PERMISSIONS")
    def validate_sas_permissions(cls, v):
        # Azure valid permission chars (blob service subset): r w d l a c u p t f m e x
        allowed_chars = set("rwdlacup tfmex".replace(" ", ""))
        invalid = {ch for ch in v if ch not in allowed_chars}
        if invalid:
            raise ValueError(f"Invalid Azure SAS permission characters: {''.join(sorted(invalid))}")
        return v

    @validator("AZURE_SAS_EXPIRY_SECONDS")
    def validate_sas_expiry(cls, v):
        if v <= 0:
            raise ValueError("AZURE_SAS_EXPIRY_SECONDS must be positive")
        if v > 86400 * 7:  # 7 days safeguard
            raise ValueError("AZURE_SAS_EXPIRY_SECONDS too large (max 604800)")
        return v

    # Pre-parse sanitization for integer fields to allow inline comments in .env
    @field_validator("MAX_FILE_AGE_HOURS", "CLEANUP_INTERVAL_SECONDS", "MIN_FREE_DISK_MB", "AZURE_SAS_EXPIRY_SECONDS", mode="before")
    @classmethod
    def strip_inline_comment_int(cls, v):
        if isinstance(v, str):
            v = v.split("#", 1)[0].strip()
        return v

    def ensure_directories(self):
        os.makedirs(self.YT_DLP_OUTPUT_DIR, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings