from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import bencodepy
import pytest

from torrent_bot.database import Database
from torrent_bot.models import RequestState, Route
from torrent_bot.service import MediaService, torrent_file_hash


class FakeQbit:
    def __init__(self) -> None:
        self.added: list[tuple[str, Route, str]] = []

    async def add_magnet(self, magnet: str, route: Route, save_path: str) -> None:
        self.added.append((magnet, route, save_path))

    async def ensure_categories(self, save_path: str) -> None:
        return None


@pytest.mark.asyncio
async def test_duplicate_add_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.db")
    await database.connect()
    qbit = FakeQbit()
    settings = SimpleNamespace(
        collection_source=tmp_path,
        collection_view=tmp_path / "view",
        poll_seconds=10,
        prowlarr_enabled=False,
        jellyfin_enabled=False,
    )
    service = MediaService(settings, database, qbit, None, None, None)  # type: ignore[arg-type]
    magnet = "magnet:?xt=urn:btih:" + "cd" * 20
    first, created = await service.add_magnet(magnet, Route.AUTO, 1, 2, 3)
    second, duplicate_created = await service.add_magnet(magnet, Route.MOVIE, 1, 2, 3)
    assert created is True
    assert duplicate_created is False
    assert first.id == second.id
    assert len(qbit.added) == 1
    assert first.state == RequestState.QUEUED
    await database.close()


def test_torrent_file_hash_is_info_dictionary_hash() -> None:
    payload = bencodepy.encode(
        {b"announce": b"https://tracker", b"info": {b"name": b"test", b"length": 4}}
    )
    assert len(torrent_file_hash(payload)) == 40


def test_invalid_torrent_file() -> None:
    with pytest.raises(ValueError):
        torrent_file_hash(bencodepy.encode({b"not-info": b"value"}))
