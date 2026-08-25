from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import bencodepy
import pytest

from torrent_bot.database import Database
from torrent_bot.models import RequestState, Route, SearchResult
from torrent_bot.service import (
    JELLYFIN_TIMEOUT_ERROR,
    MediaService,
    awaiting_jellyfin,
    monitor_interval,
    poster_lookup,
    torrent_file_hash,
)


class FakeQbit:
    def __init__(self) -> None:
        self.added: list[tuple[str, Route, str]] = []

    async def add_magnet(self, magnet: str, route: Route, save_path: str) -> None:
        self.added.append((magnet, route, save_path))

    async def ensure_categories(self, save_path: str) -> None:
        return None


class FakeProwlarr:
    async def search(self, query: str) -> list[SearchResult]:
        return [
            SearchResult(
                result_id="incidental",
                title="Stranger Things - E Pluribus Unum",
                size=2_000,
                seeders=500,
                leechers=0,
                indexer="General",
                category="TV",
                download_url="/incidental",
            ),
            SearchResult(
                result_id="show",
                title="Pluribus S01E01 1080p WEB-DL",
                size=1_000,
                seeders=20,
                leechers=0,
                indexer="TV",
                category="TV",
                download_url="/show",
            ),
        ]


class RedirectingProwlarr:
    async def download(self, result: SearchResult) -> str:
        return "magnet:?xt=urn:btih:" + "ab" * 20


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


@pytest.mark.asyncio
async def test_search_ranks_title_matches_before_incidental_matches(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.db")
    await database.connect()
    settings = SimpleNamespace(prowlarr_enabled=True)
    service = MediaService(
        settings,
        database,
        FakeQbit(),
        FakeProwlarr(),  # type: ignore[arg-type]
        None,
        None,
    )

    results = await service.search("Pluribus", user_id=1)

    assert [result.result_id for result in results] == ["show", "incidental"]
    await database.close()


@pytest.mark.asyncio
async def test_search_result_magnet_redirect_is_added(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.db")
    await database.connect()
    qbit = FakeQbit()
    settings = SimpleNamespace(collection_source=tmp_path)
    service = MediaService(
        settings,
        database,
        qbit,
        RedirectingProwlarr(),  # type: ignore[arg-type]
        None,
        None,
    )
    result = SearchResult(
        result_id="redirect",
        title="Example Show S01",
        size=1000,
        seeders=10,
        leechers=0,
        indexer="General",
        category="TV",
        download_url="/download",
    )

    request, created = await service.add_search_result(result, Route.SHOW, 1, 2, 3)

    assert created is True
    assert request.info_hash == "ab" * 20
    assert qbit.added == [("magnet:?xt=urn:btih:" + "ab" * 20, Route.SHOW, str(tmp_path))]
    await database.close()


def test_torrent_file_hash_is_info_dictionary_hash() -> None:
    payload = bencodepy.encode(
        {b"announce": b"https://tracker", b"info": {b"name": b"test", b"length": 4}}
    )
    assert len(torrent_file_hash(payload)) == 40


def test_invalid_torrent_file() -> None:
    with pytest.raises(ValueError):
        torrent_file_hash(bencodepy.encode({b"not-info": b"value"}))


def test_timed_out_jellyfin_request_remains_reconcilable() -> None:
    request = SimpleNamespace(
        state=RequestState.NEEDS_ATTENTION,
        error=JELLYFIN_TIMEOUT_ERROR,
    )

    assert awaiting_jellyfin(request) is True


def test_active_downloads_poll_twice_as_fast() -> None:
    assert monitor_interval(10, downloading=False) == 10
    assert monitor_interval(10, downloading=True) == 5


def test_anime_poster_lookup_cleans_release_name() -> None:
    request = SimpleNamespace(
        title="[Judas] Witch Hat Atelier (Tongari Boushi no Atelier) (Season 01) [1080p]",
        route=Route.ANIME,
    )

    assert poster_lookup(request) == ("Witch Hat Atelier (Tongari Boushi no Atelier)", True)
