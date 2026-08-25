from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Route(StrEnum):
    AUTO = "auto"
    MOVIE = "movie"
    SHOW = "show"
    ANIME = "anime"


class RequestState(StrEnum):
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    ORGANIZING = "organizing"
    SCANNING = "scanning"
    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"
    ERROR = "error"
    REMOVED = "removed"


TERMINAL_STATES = {
    RequestState.READY,
    RequestState.NEEDS_ATTENTION,
    RequestState.REMOVED,
}


@dataclass(slots=True)
class MediaRequest:
    id: int
    info_hash: str
    title: str
    route: Route
    state: RequestState
    requester_id: int
    guild_id: int
    channel_id: int
    message_id: int | None = None
    qbit_name: str | None = None
    content_path: str | None = None
    progress: float = 0.0
    download_speed: int = 0
    eta: int = 0
    error: str | None = None
    jellyfin_item_id: str | None = None
    scheduled_for: str | None = None
    source_uri: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> MediaRequest:
        values = dict(row)
        values["route"] = Route(values["route"])
        values["state"] = RequestState(values["state"])
        return cls(**values)


@dataclass(slots=True)
class SearchResult:
    result_id: str
    title: str
    size: int
    seeders: int
    leechers: int
    indexer: str
    category: str
    download_url: str | None = None
    magnet: str | None = None


@dataclass(slots=True)
class TorrentStatus:
    info_hash: str
    name: str
    state: str
    progress: float
    download_speed: int
    eta: int
    content_path: str
    category: str = ""

    @property
    def complete(self) -> bool:
        return self.progress >= 1.0 and self.state not in {
            "checkingDL",
            "checkingUP",
            "checkingResumeData",
            "moving",
        }

    @property
    def paused(self) -> bool:
        return self.state.lower().startswith("paused") or self.state == "stoppedDL"
