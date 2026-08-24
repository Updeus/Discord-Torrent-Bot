from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _integer(name: str, default: int = 0) -> int:
    value = _env(name)
    return int(value) if value else default


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    discord_guild_id: int
    command_prefix: str
    qbit_base_url: str
    qbit_username: str
    qbit_password: str
    collection_source: Path
    collection_view: Path
    database_path: Path
    prowlarr_base_url: str
    prowlarr_api_key: str
    nyaa_url: str
    jellyfin_base_url: str
    jellyfin_api_key: str
    jellyfin_movies_library_id: str
    jellyfin_shows_library_id: str
    poll_seconds: int
    jellyfin_timeout_seconds: int
    timezone: str

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> Settings:
        load_dotenv(env_file or ".env")
        settings = cls(
            discord_token=_env("DISCORD_BOT_TOKEN"),
            discord_guild_id=_integer("DISCORD_GUILD_ID"),
            command_prefix=_env("COMMAND_PREFIX", "!"),
            qbit_base_url=_env("QBITTORRENT_BASE_URL").rstrip("/"),
            qbit_username=_env("QBITTORRENT_USERNAME"),
            qbit_password=_env("QBITTORRENT_PASSWORD"),
            collection_source=Path(_env("COLLECTION_SOURCE", "/mnt/AWA/Collection")),
            collection_view=Path(_env("COLLECTION_VIEW", "/mnt/AWA/Collection/JellyfinView2")),
            database_path=Path(
                _env("DATABASE_PATH", "/home/jarod/Discord-Torrent-Bot/data/bot.db")
            ),
            prowlarr_base_url=_env("PROWLARR_BASE_URL", "http://127.0.0.1:9696").rstrip("/"),
            prowlarr_api_key=_env("PROWLARR_API_KEY"),
            nyaa_url=_env("NYAA_URL", "https://nyaa.si").rstrip("/"),
            jellyfin_base_url=_env("JELLYFIN_BASE_URL", "http://127.0.0.1:8097").rstrip("/"),
            jellyfin_api_key=_env("JELLYFIN_API_KEY"),
            jellyfin_movies_library_id=_env("JELLYFIN_MOVIES_LIBRARY_ID"),
            jellyfin_shows_library_id=_env("JELLYFIN_SHOWS_LIBRARY_ID"),
            poll_seconds=max(5, _integer("POLL_SECONDS", 10)),
            jellyfin_timeout_seconds=max(60, _integer("JELLYFIN_TIMEOUT_SECONDS", 600)),
            timezone=_env("TZ", "America/Port_of_Spain"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        missing = []
        for name, value in {
            "DISCORD_BOT_TOKEN": self.discord_token,
            "QBITTORRENT_BASE_URL": self.qbit_base_url,
            "QBITTORRENT_USERNAME": self.qbit_username,
            "QBITTORRENT_PASSWORD": self.qbit_password,
        }.items():
            if not value:
                missing.append(name)
        if missing:
            raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
        if not 1 <= len(self.command_prefix) <= 5:
            raise RuntimeError("COMMAND_PREFIX must be between 1 and 5 characters")

    @property
    def jellyfin_enabled(self) -> bool:
        return bool(self.jellyfin_base_url and self.jellyfin_api_key)

    @property
    def prowlarr_enabled(self) -> bool:
        return bool(self.prowlarr_base_url and self.prowlarr_api_key)
