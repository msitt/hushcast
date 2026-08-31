"""Deployment-shaped configuration from environment variables.

Runtime-tunable options (providers, processing knobs) live in the DB-backed
settings store instead. See settings_store.py.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUSHCAST_", env_file=".env", extra="ignore")

    # config volume: SQLite DB (settings live inside it).
    config_dir: Path = Path("./config")
    # data volume: bulky/replaceable files: original + processed audio,
    # transcripts, and any scratch files.
    data_dir: Path = Path("./data")
    # External base URL used to build feed/enclosure URLs, e.g. https://hushcast.example.com
    public_url: str = "http://localhost:4874"
    # Debug override. When set it wins over the UI-editable `log_level` setting
    # (and locks the UI control). Leave unset for normal operation.
    log_level: str | None = None
    # Web UI / API auth. Enabled by default: the first visit creates the login
    # (see auth.py). Set to "disabled" only when something in front of the app
    # (e.g. reverse-proxy auth) already protects it.
    auth: str = "enabled"

    @property
    def auth_disabled(self) -> bool:
        return self.auth.strip().lower() == "disabled"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.config_dir / 'hushcast.db').as_posix()}"

    @property
    def original_audio_dir(self) -> Path:
        return self.data_dir / "audio" / "original"

    @property
    def processed_audio_dir(self) -> Path:
        return self.data_dir / "audio" / "processed"

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def cues_dir(self) -> Path:
        return self.data_dir / "cues"

    def ensure_dirs(self) -> None:
        for d in (
            self.config_dir,
            self.original_audio_dir,
            self.processed_audio_dir,
            self.transcripts_dir,
            self.cues_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
